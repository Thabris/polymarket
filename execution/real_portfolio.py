"""RealPortfolio: read-only risk tracking of the user's actual Polymarket account.

Monitoring, never enforcement — real trades happen outside this platform, so
breaching a limit here cannot block anything; it raises a toast/alert and
paints the dashboard red instead. The SAME RiskEngine.mc_var runs on the real
book, so paper and real risk are always measured with one yardstick.

Wallet address: public identifier only (set via .env POLYMARKET_WALLET_ADDRESS
or the dashboard, persisted in alert_config "risk.wallet_address"). No key
material is ever read or stored by this component.
"""

from __future__ import annotations

import logging
from typing import Optional

import asyncio

from config.settings import settings
from core.events import Event, EventType, event_bus
from core.models import Alert, AlertType, Severity
from core.timeutil import utcnow
from data.portfolio_client import PortfolioClient, valid_address
from data.storage import Database
from execution.risk import RiskEngine

logger = logging.getLogger(__name__)

WALLET_CONFIG_KEY = "risk.wallet_address"


def positions_to_book(raw_positions: list[dict]) -> list[dict]:
    """Map Data-API positions to RiskEngine book entries (pure function).

    - notional = cost basis (initialValue, fallback size*avgPrice): the
      premium actually at stake;
    - p_win = curPrice of the HELD outcome token (the Data API reports the
      held asset's own price — no YES/NO inversion needed);
    - group = eventId when present (same-event positions resolve together).
    """
    book = []
    for p in raw_positions:
        try:
            size = float(p.get("size") or 0)
            avg = float(p.get("avgPrice") or 0)
            if size <= 0 or avg <= 0:
                continue
            notional = float(p.get("initialValue") or 0) or size * avg
            cur = p.get("curPrice")
            p_win = float(cur) if cur is not None else avg
            event_id = p.get("eventId")
            group = f"ev:{event_id}" if event_id else (p.get("conditionId") or p.get("asset") or "?")
            book.append(
                {
                    "strategy": "real",
                    "market_id": p.get("conditionId"),
                    "group": str(group),
                    "notional": notional,
                    "size": size,
                    "entry": avg,
                    "fees": float(p.get("entryFeesUsdc") or 0),
                    "p_win": min(max(p_win, 0.0), 1.0),
                    # display extras (ignored by mc_var)
                    "title": p.get("title"),
                    "outcome": p.get("outcome"),
                    "current_value": float(p.get("currentValue") or 0),
                    "cash_pnl": float(p.get("cashPnl") or 0),
                    "event_slug": p.get("eventSlug"),
                    "redeemable": bool(p.get("redeemable")),
                }
            )
        except (TypeError, ValueError) as e:
            logger.warning(f"unparseable real position skipped: {e}")
    return book


class RealPortfolio:
    """Periodic read-only sync of the real account, risk-scored like paper."""

    def __init__(self, database: Database, engine: RiskEngine, client: PortfolioClient) -> None:
        self.db = database
        self.engine = engine
        self.client = client
        self.wallet: Optional[str] = None
        self._book: list[dict] = []
        self._value: Optional[float] = None
        self._updated_at = None
        self._last_error: Optional[str] = None
        self._breached: set[str] = set()  # currently-breached limit names

    async def load_wallet(self) -> None:
        """Wallet from persisted override, falling back to settings/.env."""
        stored = await self.db.get_config(WALLET_CONFIG_KEY)
        candidate = stored or settings.polymarket_wallet_address
        if candidate and valid_address(candidate):
            self.wallet = candidate
        elif candidate:
            logger.warning(f"ignoring malformed wallet address {candidate[:12]}...")

    async def set_wallet(self, address: str) -> None:
        """Set + persist the tracked wallet (empty string clears)."""
        address = (address or "").strip()
        if address and not valid_address(address):
            raise ValueError("not a valid 0x… address")
        self.wallet = address or None
        await self.db.set_config(WALLET_CONFIG_KEY, address)
        self._book, self._value, self._updated_at = [], None, None
        self._breached.clear()
        if self.wallet:
            await self.refresh()

    async def refresh(self) -> None:
        """One fetch + risk-score + breach-transition pass."""
        if not self.wallet:
            return
        try:
            raw = await self.client.get_positions(self.wallet)
            self._book = positions_to_book(raw)
            self._value = await self.client.get_value(self.wallet)
            self._updated_at = utcnow()
            self._last_error = None
        except Exception as e:  # noqa: BLE001 — a failed poll must not kill the loop
            self._last_error = repr(e)
            logger.warning(f"real portfolio refresh failed: {e!r}")
            return
        await self._check_breaches()

    async def _check_breaches(self) -> None:
        """Alert on limit-breach TRANSITIONS (never re-toast a standing breach)."""
        deployed = sum(e["notional"] for e in self._book)
        var = self.engine.mc_var(self._book)
        now_breached = set()
        if deployed > self.engine.limits["total_deployed"]:
            now_breached.add("total_deployed")
        if var["var"] > self.engine.limits["var95"]:
            now_breached.add("var95")

        for name in now_breached - self._breached:
            current = deployed if name == "total_deployed" else var["var"]
            title = f"[real portfolio] {name} limit breached"
            message = (
                f"Real account {name}: {current:.0f} exceeds limit "
                f"{self.engine.limits[name]:.0f} (monitoring only — no enforcement possible)"
            )
            row = await self.db.add_alert(
                alert_type=AlertType.SYSTEM.value,
                title=title,
                message=message,
                severity=Severity.CRITICAL.value,
            )
            await event_bus.emit(
                Event(
                    type=EventType.ALERT_CREATED,
                    data=Alert(
                        id=row.id, alert_type=AlertType.SYSTEM,
                        severity=Severity.CRITICAL, title=title, message=message,
                    ),
                    source="real_portfolio",
                )
            )
        for name in self._breached - now_breached:
            logger.info(f"real portfolio {name} back under limit")
        self._breached = now_breached

    async def run_forever(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception as e:  # noqa: BLE001
                logger.error(f"real portfolio loop error: {e!r}")
            await asyncio.sleep(settings.real_portfolio_refresh_minutes * 60)

    # -------------------------------------------------------------------- views
    def snapshot(self) -> dict:
        """Risk view of the real book for the API/dashboard."""
        if not self.wallet:
            return {"configured": False}
        deployed = sum(e["notional"] for e in self._book)
        var = self.engine.mc_var(self._book) if self._book else {
            "var": 0.0, "es": 0.0, "worst_case": 0.0, "expected_pnl": 0.0
        }
        by_group: dict[str, float] = {}
        for e in self._book:
            by_group[e["group"]] = by_group.get(e["group"], 0.0) + e["notional"]
        return {
            "configured": True,
            "wallet": self.wallet,
            "updated_at": self._updated_at.isoformat() if self._updated_at else None,
            "last_error": self._last_error,
            "portfolio_value": self._value,
            "cost_basis": round(deployed, 2),
            "unrealized_pnl": round(sum(e["cash_pnl"] for e in self._book), 2),
            "positions": sorted(
                (
                    {
                        "title": e["title"],
                        "outcome": e["outcome"],
                        "size": round(e["size"], 1),
                        "avg_price": e["entry"],
                        "cur_price": e["p_win"],
                        "cost": round(e["notional"], 2),
                        "value": round(e["current_value"], 2),
                        "pnl": round(e["cash_pnl"], 2),
                        "event_slug": e["event_slug"],
                        "redeemable": e["redeemable"],
                    }
                    for e in self._book
                ),
                key=lambda x: -x["cost"],
            ),
            "var": {**var, "limit": self.engine.limits["var95"]},
            "deployed_limit": self.engine.limits["total_deployed"],
            "top_concentrations": [
                {"group": g, "notional": round(v, 2)}
                for g, v in sorted(by_group.items(), key=lambda kv: -kv[1])[:8]
            ],
            "breached": sorted(self._breached),
        }

    def status(self) -> dict:
        return {
            "configured": bool(self.wallet),
            "positions": len(self._book),
            "updated_at": self._updated_at.isoformat() if self._updated_at else None,
            "last_error": self._last_error,
            "breached": sorted(self._breached),
        }
