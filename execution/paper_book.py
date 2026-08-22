"""PaperBook: tracks open paper positions (and real fills) to resolution.

Every 10 minutes:
- refresh market state for open positions' markets straight from Gamma
  (resolved markets drop out of the universe sync, so they must be fetched
  individually);
- close positions on resolution (exit 1.0 / 0.0), apply news-fade time stops
  at current mid, expire stale signals and pending zone orders;
- close real fills on resolution the same way.

Per-strategy stats (P&L, win rate, calibration) are computed on demand.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Optional

from config.settings import settings
from core.fees import taker_fee_per_share
from core.timeutil import from_db, utcnow
from data.gamma_client import GammaClient, parse_market
from data.storage import Database
from execution.paper import PaperRouter
from signals.models import SignalStatus

logger = logging.getLogger(__name__)

CHECK_INTERVAL_S = 600


class PaperBook:
    """Resolution tracker + stats for paper positions and real fills."""

    def __init__(self, database: Database, gamma: GammaClient, router: PaperRouter) -> None:
        self.db = database
        self.gamma = gamma
        self.router = router
        self._last_check = None
        self._checks = 0

    async def run_forever(self) -> None:
        while True:
            try:
                await self.check_once()
            except Exception as e:  # noqa: BLE001 — loop must survive
                logger.error(f"paper book check failed: {e!r}")
            await asyncio.sleep(CHECK_INTERVAL_S)

    async def check_once(self) -> None:
        """One pass over open positions, fills, and stale signals."""
        self._checks += 1
        self._last_check = utcnow()
        await self.router.expire_stale_pending()
        await self._expire_stale_signals()

        open_positions = await self.db.get_paper_positions(status="open")
        open_fills = await self.db.get_real_fills(status="open")
        market_ids = {p.market_id for p in open_positions if p.market_id}
        market_ids |= await self._fill_market_ids(open_fills)
        if not market_ids:
            return

        markets = await self._refresh_markets(market_ids)

        for pos in open_positions:
            market = markets.get(pos.market_id)
            if market is None:
                continue
            exit_price, reason = self._exit_for(market, pos.token_id, pos.side, pos.strategy,
                                                from_db(pos.opened_at))
            if exit_price is not None:
                await self.router.close_position(pos.id, reason, exit_price)
                await self.db.update_signal(pos.signal_id, status=SignalStatus.RESOLVED.value)

        for fill in open_fills:
            signal = await self.db.get_signal(fill.signal_id)
            if signal is None or not signal.market_id:
                continue
            market = markets.get(signal.market_id)
            if market is None:
                continue
            exit_price, reason = self._exit_for(
                market, signal.token_id, fill.side, signal.strategy, from_db(fill.created_at)
            )
            if exit_price is not None and reason.startswith("resolution"):
                pnl = (exit_price - fill.price) * fill.size - (fill.fee_paid or 0.0)
                await self.db.close_real_fill(fill.id, exit_price=exit_price, pnl=pnl)

    async def _fill_market_ids(self, fills) -> set[str]:
        ids = set()
        for fill in fills:
            signal = await self.db.get_signal(fill.signal_id)
            if signal and signal.market_id:
                ids.add(signal.market_id)
        return ids

    async def _refresh_markets(self, market_ids: set[str]) -> dict:
        """Fetch current market state for the given ids directly from Gamma."""
        out = {}
        for market_id in market_ids:
            raw = await self.gamma.get_market(market_id)
            if raw is None:
                continue
            try:
                market = parse_market(raw)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"parse_market failed for {market_id}: {e}")
                continue
            out[market_id] = market
            await self.db.upsert_markets([market])
            await asyncio.sleep(0.15)
        return out

    def _exit_for(self, market, token_id, side, strategy, opened_at) -> tuple[Optional[float], str]:
        """Decide whether a position must exit, and at what price."""
        # Resolution: won if the held token is the resolved outcome
        if market.closed and market.resolved_outcome is not None:
            held_index = 0 if token_id == market.token_id_yes else 1
            won = str(held_index) == market.resolved_outcome
            return (1.0, "resolution_win") if won else (0.0, "resolution_loss")
        if market.closed:
            # Closed but outcome unknown/ambiguous: never guess — leave open
            logger.warning(f"market {market.id} closed without clear outcome; leaving position open")
            return None, ""
        # Time stop for news-fade positions: exit at current mid minus taker fee
        if strategy == "news_fade" and opened_at is not None:
            age = utcnow() - opened_at
            if age > timedelta(hours=settings.fade_time_stop_hours):
                mid = self._current_mid(market, token_id)
                if mid is not None:
                    fee = taker_fee_per_share(mid, market.fee_rate or 0.0)
                    return max(mid - fee, 0.0), "time_stop"
        return None, ""

    @staticmethod
    def _current_mid(market, token_id) -> Optional[float]:
        if market.best_bid is not None and market.best_ask is not None:
            mid = (market.best_bid + market.best_ask) / 2
        else:
            mid = market.last_trade_price or market.price_yes
        if mid is None:
            return None
        if token_id == market.token_id_no:
            mid = 1.0 - mid
        return mid

    async def _expire_stale_signals(self) -> None:
        """Expire open non-zone signals past their expiry."""
        now = utcnow()
        for s in await self.db.get_signals_by_status([SignalStatus.OPEN.value]):
            expires = from_db(s.expires_at)
            if expires and expires < now:
                await self.db.update_signal(s.id, status=SignalStatus.EXPIRED.value)

    # -------------------------------------------------------------------- stats
    async def stats(self) -> dict:
        """Per-strategy performance stats, computed on demand."""
        signals = await self.db.get_signals(limit=10000)
        positions = await self.db.get_paper_positions(limit=10000)
        fills = await self.db.get_real_fills(limit=10000)

        strategies: dict[str, dict] = {}

        for s in signals:
            entry = strategies.setdefault(s.strategy, _empty_stats())
            entry["signals_emitted"] += 1
            entry["by_status"][s.status] = entry["by_status"].get(s.status, 0) + 1

        for p in positions:
            entry = strategies.setdefault(p.strategy, _empty_stats())
            if p.status == "open":
                entry["open_positions"] += 1
                entry["open_notional"] += p.entry_price * p.size
            else:
                entry["closed_positions"] += 1
                entry["total_pnl"] += p.pnl or 0.0
                if (p.pnl or 0.0) > 0:
                    entry["wins"] += 1
                # calibration: entry-price decile vs win frequency
                if p.exit_reason in ("resolution_win", "resolution_loss"):
                    decile = min(int(p.entry_price * 10), 9)
                    cal = entry["calibration"].setdefault(decile, {"n": 0, "wins": 0})
                    cal["n"] += 1
                    if p.exit_reason == "resolution_win":
                        cal["wins"] += 1

        real = {"open": 0, "closed": 0, "total_pnl": 0.0}
        for f in fills:
            if f.status == "open":
                real["open"] += 1
            else:
                real["closed"] += 1
                real["total_pnl"] += f.pnl or 0.0

        for entry in strategies.values():
            closed = entry["closed_positions"]
            entry["win_rate"] = entry["wins"] / closed if closed else None
            filled = entry["by_status"].get("filled", 0) + entry["by_status"].get("resolved", 0)
            emitted = entry["signals_emitted"]
            entry["fill_rate"] = filled / emitted if emitted else None

        return {"strategies": strategies, "real_fills": real}

    def status(self) -> dict:
        return {
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "checks": self._checks,
        }


def _empty_stats() -> dict:
    return {
        "signals_emitted": 0,
        "by_status": {},
        "open_positions": 0,
        "closed_positions": 0,
        "open_notional": 0.0,
        "wins": 0,
        "total_pnl": 0.0,
        "win_rate": None,
        "fill_rate": None,
        "calibration": {},
    }
