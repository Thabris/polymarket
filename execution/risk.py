"""RiskEngine: portfolio exposure tracking, VaR, and hard limits.

The gate every fill must pass — paper today, live later (the LiveRouter
inherits it through the same router seam). Binary markets make risk math
unusually honest:

- a bought token's max loss IS its premium (deployed == worst case, no
  leverage, no gap risk beyond -100%);
- each market is a Bernoulli at its current market-implied probability, so
  portfolio VaR is a direct Monte Carlo over outcomes — no return
  distributions to assume;
- the real correlation structure is event-level: positions in markets of the
  same event move together, so same-event markets share one random draw
  (comonotone) while distinct events are independent. Conservative where it
  matters, simple everywhere else.

Limits (env defaults in settings, runtime overrides persisted in
alert_config under "risk.<name>"):
  total_deployed, per_strategy_deployed, per_event_deployed,
  per_market_deployed, var95, daily_loss (UTC-day realized kill switch).
A manual kill switch (API) blocks all new fills regardless.
"""

from __future__ import annotations

import logging
import random
from collections import deque
from datetime import datetime, time, timezone
from typing import Optional

from config.settings import settings
from core.timeutil import from_db, utcnow
from data.storage import Database

logger = logging.getLogger(__name__)

CONFIG_PREFIX = "risk."


def _limit_defaults() -> dict[str, float]:
    return {
        "total_deployed": settings.risk_max_total_deployed,
        "per_strategy_deployed": settings.risk_max_deployed_per_strategy,
        "per_event_deployed": settings.risk_max_deployed_per_event,
        "per_market_deployed": settings.risk_max_position_per_market,
        "var95": settings.risk_var95_limit,
        "daily_loss": settings.risk_max_daily_loss,
    }


class RiskEngine:
    """Exposure accounting + limit enforcement for the execution seam."""

    def __init__(self, database: Database) -> None:
        self.db = database
        self.limits: dict[str, float] = _limit_defaults()
        self.manual_kill = False
        # daily-loss LATCH: once tripped it stays tripped for the UTC day —
        # a later profitable close must not silently re-open the gate
        self._daily_kill_date: Optional[str] = None
        self._blocks = 0
        self._recent_blocks: deque = deque(maxlen=20)

    # ------------------------------------------------------------------ limits
    async def load_overrides(self) -> None:
        """Apply persisted runtime overrides from alert_config ("risk.<name>")."""
        for name in list(self.limits):
            raw = await self.db.get_config(CONFIG_PREFIX + name)
            if raw is not None:
                import math
                try:
                    value = float(raw)
                    if math.isfinite(value) and value > 0:
                        self.limits[name] = value
                    else:
                        logger.warning(f"non-finite risk override {name}={raw!r}; keeping default")
                except ValueError:
                    logger.warning(f"bad risk override {name}={raw!r}; keeping default")
        raw = await self.db.get_config(CONFIG_PREFIX + "manual_kill")
        self.manual_kill = raw == "1"
        self._daily_kill_date = await self.db.get_config(CONFIG_PREFIX + "daily_kill_date")

    async def set_limit(self, name: str, value: float) -> None:
        """Set and persist a limit at runtime."""
        import math
        if name not in self.limits:
            raise KeyError(name)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("limits must be positive and finite")
        self.limits[name] = float(value)
        await self.db.set_config(CONFIG_PREFIX + name, str(float(value)))
        logger.info(f"risk limit {name} set to {value}")

    async def set_manual_kill(self, enabled: bool) -> None:
        """Flip the manual kill switch (blocks ALL new fills while on)."""
        self.manual_kill = enabled
        await self.db.set_config(CONFIG_PREFIX + "manual_kill", "1" if enabled else "0")
        logger.warning(f"risk manual kill switch {'ENGAGED' if enabled else 'released'}")

    # ------------------------------------------------------------ book reading
    async def _open_book(self) -> list[dict]:
        """Open paper positions enriched with market state.

        Each entry: {strategy, market_id, group, notional, size, entry,
        fees, p_win} — p_win from the CURRENT market price of the held token
        (falls back to entry price when the market row is missing/stale).
        """
        positions = await self.db.get_paper_positions(status="open", limit=100000)
        if len(positions) >= 100000:  # pragma: no cover — loud, never silent
            logger.error("risk book truncated at 100000 open positions; limits under-enforced")
        market_ids = [p.market_id for p in positions if p.market_id]
        rows = await self.db.get_markets_by_ids(market_ids)
        by_id = {r.id: r for r in rows}

        book = []
        for p in positions:
            row = by_id.get(p.market_id)
            p_win = None
            group = p.market_id or f"pos-{p.id}"
            if row is not None:
                if row.event_id:
                    group = f"ev:{row.event_id}"
                mid = None
                if row.best_bid is not None and row.best_ask is not None:
                    mid = (row.best_bid + row.best_ask) / 2
                elif row.price_yes is not None:
                    mid = row.price_yes
                if mid is not None:
                    if p.token_id and p.token_id == row.token_id_yes:
                        p_win = mid
                    elif p.token_id and p.token_id == row.token_id_no:
                        p_win = 1.0 - mid
                    elif p.side == "buy_yes":
                        p_win = mid
                    elif p.side == "buy_no":
                        p_win = 1.0 - mid
                    # token matches neither and side unknown -> entry fallback
            if p_win is None:
                p_win = p.entry_price
            p_win = min(max(p_win, 0.0), 1.0)
            book.append(
                {
                    "strategy": p.strategy,
                    "market_id": p.market_id,
                    "group": group,
                    "notional": p.entry_price * p.size,
                    "size": p.size,
                    "entry": p.entry_price,
                    "fees": p.fees_paid or 0.0,
                    "p_win": p_win,
                }
            )
        return book

    async def realized_pnl_today(self) -> float:
        """Realized P&L of paper positions closed since UTC midnight (SQL-side
        filter on closed_at — a recency window keyed on opened_at was silently
        dropping resolution closes of long-held positions)."""
        midnight = datetime.combine(utcnow().date(), time.min, tzinfo=timezone.utc)
        return await self.db.sum_closed_pnl_since(midnight)

    # ---------------------------------------------------------------------- VaR
    @staticmethod
    def mc_var(
        book: list[dict],
        alpha: float = 0.95,
        sims: int = 0,
        seed: Optional[int] = None,
    ) -> dict:
        """Monte Carlo VaR/ES over binary outcomes.

        Same-`group` entries share one uniform draw (comonotone: an event's
        markets resolve together); distinct groups are independent. Loss is
        measured against entry (premium + sunk fees fully at risk).
        """
        sims = sims or settings.risk_var_sims
        if not book or sims <= 0:
            return {"var": 0.0, "es": 0.0, "worst_case": 0.0, "expected_pnl": 0.0}
        rng = random.Random(seed)
        groups = sorted({e["group"] for e in book})
        losses = []
        pnl_sum = 0.0
        for _ in range(sims):
            draws = {g: rng.random() for g in groups}
            pnl = 0.0
            for e in book:
                win = draws[e["group"]] < e["p_win"]
                pnl += (1.0 - e["entry"]) * e["size"] if win else -e["notional"]
                pnl -= e["fees"]
            losses.append(-pnl)
            pnl_sum += pnl
        losses.sort()
        idx = min(int(alpha * sims), sims - 1)
        var = max(losses[idx], 0.0)
        tail = losses[idx:]
        es = max(sum(tail) / len(tail), 0.0) if tail else 0.0
        worst = sum(e["notional"] + e["fees"] for e in book)
        return {
            "var": round(var, 2),
            "es": round(es, 2),
            "worst_case": round(worst, 2),
            "expected_pnl": round(pnl_sum / sims, 2),
        }

    # ----------------------------------------------------------------- the gate
    async def allow(
        self,
        strategy: str,
        market_id: Optional[str],
        notional: float,
        p_win: Optional[float] = None,
    ) -> tuple[bool, str]:
        """Decide whether a new position may open. Returns (allowed, reason)."""
        if self.manual_kill:
            return self._block(strategy, market_id, "manual_kill")

        today = utcnow().date().isoformat()
        if self._daily_kill_date == today:
            return self._block(strategy, market_id, "daily_loss_latched")
        daily = await self.realized_pnl_today()
        if daily <= -self.limits["daily_loss"]:
            self._daily_kill_date = today
            await self.db.set_config(CONFIG_PREFIX + "daily_kill_date", today)
            return self._block(strategy, market_id, f"daily_loss {daily:.0f}")

        book = await self._open_book()
        total = sum(e["notional"] for e in book)
        if total + notional > self.limits["total_deployed"]:
            return self._block(strategy, market_id, f"total_deployed {total:.0f}+{notional:.0f}")

        strat_total = sum(e["notional"] for e in book if e["strategy"] == strategy)
        if strat_total + notional > self.limits["per_strategy_deployed"]:
            return self._block(strategy, market_id, f"per_strategy_deployed {strat_total:.0f}")

        if market_id:
            market_total = sum(e["notional"] for e in book if e["market_id"] == market_id)
            if market_total + notional > self.limits["per_market_deployed"]:
                return self._block(strategy, market_id, f"per_market_deployed {market_total:.0f}")

            group = await self._group_for(market_id)
            if group:
                group_total = sum(e["notional"] for e in book if e["group"] == group)
                if group_total + notional > self.limits["per_event_deployed"]:
                    return self._block(strategy, market_id, f"per_event_deployed {group_total:.0f}")

        # candidate expressed as a real binary position: entry*size == notional,
        # win prob = the entry price itself (market-implied at fill time)
        entry = min(max(p_win if p_win is not None else 0.5, 0.01), 0.99)
        candidate_group = (await self._group_for(market_id)) if market_id else None
        candidate = {
            "strategy": strategy,
            "market_id": market_id,
            "group": candidate_group or market_id or "candidate",
            "notional": notional,
            "size": notional / entry,
            "entry": entry,
            "fees": 0.0,
            "p_win": entry,
        }
        var = self.mc_var(book + [candidate])
        if var["var"] > self.limits["var95"]:
            return self._block(strategy, market_id, f"var95 {var['var']:.0f}")

        return True, "ok"

    async def _group_for(self, market_id: str) -> Optional[str]:
        rows = await self.db.get_markets_by_ids([market_id])
        if rows and rows[0].event_id:
            return f"ev:{rows[0].event_id}"
        return market_id

    def _block(self, strategy: str, market_id: Optional[str], reason: str) -> tuple[bool, str]:
        self._blocks += 1
        self._recent_blocks.append(
            {
                "at": utcnow().isoformat(),
                "strategy": strategy,
                "market_id": market_id,
                "reason": reason,
            }
        )
        logger.warning(f"RISK BLOCK [{strategy}/{market_id}]: {reason}")
        return False, reason

    # -------------------------------------------------------------------- views
    async def snapshot(self) -> dict:
        """Full risk state for the API/dashboard."""
        book = await self._open_book()
        total = sum(e["notional"] for e in book)
        by_strategy: dict[str, float] = {}
        by_group: dict[str, float] = {}
        for e in book:
            by_strategy[e["strategy"]] = by_strategy.get(e["strategy"], 0.0) + e["notional"]
            by_group[e["group"]] = by_group.get(e["group"], 0.0) + e["notional"]
        var = self.mc_var(book)
        daily = await self.realized_pnl_today()

        def util(name: str, current: float) -> dict:
            limit = self.limits[name]
            return {
                "limit": limit,
                "current": round(current, 2),
                "pct": round(100 * current / limit, 1) if limit else None,
            }

        top_groups = sorted(by_group.items(), key=lambda kv: kv[1], reverse=True)[:8]
        return {
            "kill_switch": {
                "manual": self.manual_kill,
                "daily_loss_tripped": (
                    self._daily_kill_date == utcnow().date().isoformat()
                    or daily <= -self.limits["daily_loss"]
                ),
            },
            "bankroll_reference": settings.risk_bankroll,
            "deployed": {
                "total": util("total_deployed", total),
                "by_strategy": {
                    s: util("per_strategy_deployed", v) for s, v in sorted(by_strategy.items())
                },
                "top_concentrations": [
                    {"group": g, "notional": round(v, 2), "limit": self.limits["per_event_deployed"]}
                    for g, v in top_groups
                ],
            },
            "var": {**var, "limit": self.limits["var95"],
                    "pct": round(100 * var["var"] / self.limits["var95"], 1) if self.limits["var95"] else None},
            "daily_realized_pnl": round(daily, 2),
            "daily_loss_limit": self.limits["daily_loss"],
            "open_positions": len(book),
            "limits": dict(self.limits),
            "blocks": {"total": self._blocks, "recent": list(self._recent_blocks)},
        }

    def status(self) -> dict:
        return {
            "manual_kill": self.manual_kill,
            "blocks": self._blocks,
            "limits": dict(self.limits),
        }
