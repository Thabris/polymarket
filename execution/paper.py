"""PaperRouter: models fills honestly instead of placing orders.

- Immediate signals (theta, calendar arb, manual): fill at the signal's
  entry_price (already the executable ask), taker fee = rate * p * (1-p)
  from the snapshot's fee_rate.
- Zone signals (news fade): a pending "resting maker order" — fills at the
  zone's midpoint ONLY if a BOOK_TOP tick touches the zone before the signal
  expires (maker fill -> zero fee); otherwise the signal expires unfilled.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from config.settings import settings
from core.events import Event, EventType, event_bus
from core.fees import taker_fee_per_share
from core.timeutil import from_db, to_db, utcnow
from data.storage import Database
from execution.router import ExecutionRouter, RiskLimits
from signals.models import Signal, SignalGrade, SignalStatus

logger = logging.getLogger(__name__)


class PaperRouter(ExecutionRouter):
    """Paper implementation of the execution seam."""

    def __init__(
        self,
        database: Database,
        risk_limits: RiskLimits | None = None,
        risk_engine=None,
    ) -> None:
        super().__init__(risk_limits)
        self.db = database
        self.risk_engine = risk_engine
        # serializes the risk-check + insert critical section: without it two
        # concurrent fills can both read a book excluding the other (TOCTOU)
        self._fill_lock = asyncio.Lock()
        # signal_id -> {token_id, zone_low, zone_high, expires_at, signal}
        self._pending: dict[int, dict] = {}
        self._fills = 0
        self._skips = 0
        event_bus.subscribe(EventType.BOOK_TOP, self._check_zones)

    async def load_pending(self) -> None:
        """Re-arm pending zone orders after a restart."""
        open_signals = await self.db.get_signals_by_status([SignalStatus.OPEN.value])
        now = utcnow()
        for s in open_signals:
            if s.entry_zone_low is None or s.entry_zone_high is None or not s.token_id:
                continue
            if s.grade == "C":
                continue  # shadows are never papered — restarts must not arm them
            expires = from_db(s.expires_at)
            if expires and expires < now:
                await self._expire_signal(s.id)
                continue
            snapshot = {}
            if s.snapshot:
                import json
                try:
                    snapshot = json.loads(s.snapshot)
                except json.JSONDecodeError:
                    snapshot = {}
            self._pending[s.id] = {
                "token_id": s.token_id,
                "watch_asset_id": snapshot.get("watch_asset_id") or s.token_id,
                "invert": bool(snapshot.get("invert", False)),
                "zone_low": s.entry_zone_low,
                "zone_high": s.entry_zone_high,
                "expires_at": expires,
                "strategy": s.strategy,
                "market_id": s.market_id,
                "side": s.side,
                "size_hint": s.size_hint,
            }
        if self._pending:
            logger.info(f"paper router re-armed {len(self._pending)} pending zone orders")

    # ------------------------------------------------------------------ intake
    async def on_signal(self, signal: Signal) -> None:
        """Open a paper position (immediate) or arm a pending zone order."""
        if signal.grade == SignalGrade.C:
            self._skips += 1
            return  # never paper grade-C signals
        if signal.id is None:
            return
        open_count = len(
            await self.db.get_paper_positions(strategy=signal.strategy, status="open")
        )
        if open_count >= self.risk.max_open_positions_per_strategy:
            logger.info(f"risk cap: {signal.strategy} has {open_count} open positions; skipping")
            self._skips += 1
            return
        # one position per market per strategy, and never both outcomes of the
        # same market: a re-emitted signal (dedup-key churn, price drift) must
        # never stack a second paper position, and an opposite-side signal must
        # never offset a live one
        if await self.db.conflicting_open_position(
            signal.market_id, signal.token_id, signal.strategy
        ):
            self._skips += 1
            return

        if signal.has_zone:
            # We stream YES tokens only; a NO-side zone is watched on the YES
            # book with inverted prices (snapshot carries watch/invert).
            self._pending[signal.id] = {
                "token_id": signal.token_id,
                "watch_asset_id": signal.snapshot.get("watch_asset_id") or signal.token_id,
                "invert": bool(signal.snapshot.get("invert", False)),
                "zone_low": signal.entry_zone_low,
                "zone_high": signal.entry_zone_high,
                "expires_at": signal.expires_at,
                "strategy": signal.strategy,
                "market_id": signal.market_id,
                "side": signal.side.value if hasattr(signal.side, "value") else signal.side,
                "size_hint": signal.size_hint,
            }
            return

        if signal.entry_price is None or signal.entry_price <= 0:
            self._skips += 1
            return
        fee_rate = float(signal.snapshot.get("fee_rate", 0.0) or 0.0)
        await self._fill(
            signal_id=signal.id,
            strategy=signal.strategy,
            market_id=signal.market_id,
            token_id=signal.token_id,
            side=signal.side.value if hasattr(signal.side, "value") else signal.side,
            price=signal.entry_price,
            fee_rate=fee_rate,
            size_hint=signal.size_hint,
            taker=True,
        )

    # ------------------------------------------------------------- zone filling
    async def _check_zones(self, event: Event) -> None:
        data = event.data
        asset_id = data.get("asset_id") if isinstance(data, dict) else getattr(data, "asset_id", None)
        price = None
        if isinstance(data, dict):
            price = data.get("mid") or data.get("bid")
        else:
            price = getattr(data, "mid", None) or getattr(data, "bid", None)
        if asset_id is None or price is None:
            return
        now = utcnow()
        for signal_id, pending in list(self._pending.items()):
            if pending.get("watch_asset_id", pending["token_id"]) != asset_id:
                continue
            expires = pending.get("expires_at")
            if expires and now > expires:
                # pop is the arbiter: whoever pops non-None owns the transition
                if self._pending.pop(signal_id, None) is not None:
                    await self._expire_signal(signal_id)
                continue
            watched_price = (1.0 - price) if pending.get("invert") else price
            low, high = pending["zone_low"], pending["zone_high"]
            if low <= watched_price <= high:
                if self._pending.pop(signal_id, None) is None:
                    continue  # raced with expire_stale_pending
                # armed-then-conflicted: another order on this market filled
                # while this one rested, so the invariant has to hold here too
                if await self.db.conflicting_open_position(
                    pending["market_id"], pending["token_id"], pending["strategy"]
                ):
                    self._skips += 1
                    await self._cancel_signal(signal_id, "conflicting_position")
                    continue
                # fill at the OBSERVED in-zone price (a level the market
                # actually traded/quoted), never a synthetic zone midpoint
                await self._fill(
                    signal_id=signal_id,
                    strategy=pending["strategy"],
                    market_id=pending["market_id"],
                    token_id=pending["token_id"],
                    side=pending["side"],
                    price=watched_price,
                    fee_rate=0.0,  # maker fill: fee-free
                    size_hint=pending.get("size_hint"),
                    taker=False,
                )

    def pending_market_ids(self) -> set[str]:
        """Markets with a resting zone order (PaperBook must refresh these too)."""
        return {p["market_id"] for p in self._pending.values() if p.get("market_id")}

    def pending_signal_ids(self) -> set[int]:
        """Signal ids still armed as zone orders."""
        return set(self._pending)

    async def cancel_for_market(self, market_id: str, reason: str) -> int:
        """Drop every resting zone order on a market and cancel its signals."""
        cancelled = 0
        for signal_id, pending in list(self._pending.items()):
            if pending.get("market_id") != market_id:
                continue
            if self._pending.pop(signal_id, None) is None:
                continue  # raced with a fill or an expiry
            await self._cancel_signal(signal_id, reason)
            cancelled += 1
        return cancelled

    async def expire_stale_pending(self) -> int:
        """Expire pending zone orders past their validity (called by PaperBook)."""
        now = utcnow()
        expired = 0
        for signal_id, pending in list(self._pending.items()):
            expires = pending.get("expires_at")
            if expires and now > expires:
                if self._pending.pop(signal_id, None) is None:
                    continue  # raced with a zone fill
                await self._expire_signal(signal_id)
                expired += 1
        return expired

    # ------------------------------------------------------------------- fills
    async def _fill(
        self,
        signal_id: int,
        strategy: str,
        market_id: Optional[str],
        token_id: Optional[str],
        side: str,
        price: float,
        fee_rate: float,
        size_hint: Optional[float],
        taker: bool,
    ) -> None:
        notional = size_hint or settings.paper_default_notional
        notional = min(notional, self.risk.max_position_per_market)
        async with self._fill_lock:
            # per-strategy count cap re-checked HERE (not only at arming time):
            # a zone order can fill long after arming, when the cap is full
            open_count = len(
                await self.db.get_paper_positions(strategy=strategy, status="open")
            )
            if open_count >= self.risk.max_open_positions_per_strategy:
                self._skips += 1
                await self._cancel_signal(signal_id, "risk:max_open_per_strategy")
                return
            # the risk gate — every position, both fill paths, passes through
            # here; a blocked fill cancels its signal so the book never
            # quietly diverges
            if self.risk_engine is not None:
                allowed, reason = await self.risk_engine.allow(
                    strategy=strategy, market_id=market_id, notional=notional, p_win=price
                )
                if not allowed:
                    self._skips += 1
                    await self._cancel_signal(signal_id, f"risk:{reason}")
                    return
            await self._insert_and_announce(
                signal_id, strategy, market_id, token_id, side, price, fee_rate, notional, taker
            )

    async def _insert_and_announce(
        self,
        signal_id: int,
        strategy: str,
        market_id: Optional[str],
        token_id: Optional[str],
        side: str,
        price: float,
        fee_rate: float,
        notional: float,
        taker: bool,
    ) -> None:
        size = notional / price if price > 0 else 0.0
        fees = taker_fee_per_share(price, fee_rate) * size if taker else 0.0
        pos = await self.db.insert_paper_position(
            {
                "signal_id": signal_id,
                "strategy": strategy,
                "market_id": market_id,
                "token_id": token_id,
                "side": side,
                "entry_price": price,
                "size": size,
                "fees_paid": fees,
            }
        )
        await self.db.update_signal(signal_id, status=SignalStatus.FILLED.value)
        self._fills += 1
        await event_bus.emit(
            Event(
                type=EventType.POSITION_UPDATED,
                data={
                    "position_id": pos.id,
                    "signal_id": signal_id,
                    "strategy": strategy,
                    "market_id": market_id,
                    "status": "open",
                    "entry_price": price,
                    "size": size,
                },
                source="paper_router",
            )
        )
        logger.info(f"paper fill: {strategy} {side} {size:.1f}sh @ {price:.3f} (signal {signal_id})")

    async def _cancel_signal(self, signal_id: int, reason: str) -> None:
        """Cancel a signal that can no longer be acted on (distinct from expiry:
        expiry means the entry window elapsed, cancellation means the entry
        became impossible)."""
        await self.db.update_signal(signal_id, status=SignalStatus.CANCELLED.value)
        logger.info(f"signal {signal_id} cancelled: {reason}")
        await event_bus.emit(
            Event(
                type=EventType.SIGNAL_UPDATED,
                data={"id": signal_id, "status": SignalStatus.CANCELLED.value, "reason": reason},
                source="paper_router",
            )
        )

    async def _expire_signal(self, signal_id: int) -> None:
        await self.db.update_signal(signal_id, status=SignalStatus.EXPIRED.value)
        await event_bus.emit(
            Event(
                type=EventType.SIGNAL_UPDATED,
                data={"id": signal_id, "status": SignalStatus.EXPIRED.value},
                source="paper_router",
            )
        )

    async def close_position(self, position_id: int, reason: str, price: float) -> None:
        """Close a paper position at `price` (delegated from PaperBook)."""
        positions = await self.db.get_paper_positions(status="open")
        pos = next((p for p in positions if p.id == position_id), None)
        if pos is None:
            return
        pnl = (price - pos.entry_price) * pos.size - pos.fees_paid
        await self.db.close_paper_position(position_id, exit_price=price, exit_reason=reason, pnl=pnl)
        await event_bus.emit(
            Event(
                type=EventType.POSITION_UPDATED,
                data={
                    "position_id": position_id,
                    "signal_id": pos.signal_id,
                    "strategy": pos.strategy,
                    "status": "closed",
                    "exit_price": price,
                    "exit_reason": reason,
                    "pnl": pnl,
                },
                source="paper_router",
            )
        )

    async def void_position(self, position_id: int, reason: str = "voided_conflict") -> bool:
        """Close a position at its own entry price, booking only the fees paid.

        For positions that should never have opened: the exit is a bookkeeping
        unwind, not a market outcome, so it must not read as a win or a loss.
        VOID_EXIT_REASONS keeps these out of win rate and calibration.
        """
        positions = await self.db.get_paper_positions(status="open", limit=10000)
        pos = next((p for p in positions if p.id == position_id), None)
        if pos is None:
            return False
        await self.db.close_paper_position(
            position_id,
            exit_price=pos.entry_price,
            exit_reason=reason,
            pnl=-(pos.fees_paid or 0.0),
        )
        await event_bus.emit(
            Event(
                type=EventType.POSITION_UPDATED,
                data={
                    "position_id": position_id,
                    "signal_id": pos.signal_id,
                    "strategy": pos.strategy,
                    "status": "closed",
                    "exit_price": pos.entry_price,
                    "exit_reason": reason,
                    "pnl": -(pos.fees_paid or 0.0),
                },
                source="paper_router",
            )
        )
        logger.info(f"voided position {position_id} ({pos.strategy} {pos.market_id}): {reason}")
        return True

    def status(self) -> dict:
        return {
            "pending_zone_orders": len(self._pending),
            "fills": self._fills,
            "skips": self._skips,
        }
