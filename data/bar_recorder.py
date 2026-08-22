"""BarRecorder: 1-minute OHLC bars from the stream into the prices table.

Bars are keyed by market (YES-token mid basis; trade prices fold into
high/low and add volume). Completed bars flush once a minute in one batch;
old bars and scanner runs are pruned once a day.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from config.settings import settings
from core.events import Event, EventType, event_bus
from core.models import Price
from core.timeutil import utcnow
from data.storage import Database

logger = logging.getLogger(__name__)


class BarRecorder:
    """Aggregates BOOK_TOP / TRADE_TICK events into 1-minute bars."""

    def __init__(self, database: Database, universe) -> None:
        self.db = database
        self.universe = universe
        # market_id -> {"minute": dt, "open","high","low","close","volume"}
        self._bars: dict[str, dict] = {}
        self._completed: list[Price] = []
        self._written = 0
        self._last_flush = None
        self._last_prune_day = None
        event_bus.subscribe(EventType.BOOK_TOP, self._on_book_top)
        event_bus.subscribe(EventType.TRADE_TICK, self._on_trade)

    async def _on_book_top(self, event: Event) -> None:
        data = event.data
        asset_id = data.get("asset_id")
        mid = data.get("mid")
        if asset_id is None or mid is None:
            return
        market_id = self.universe.market_id_for_asset(asset_id)
        if market_id is None:
            return
        self._update(market_id, price=mid, volume=0.0)

    async def _on_trade(self, event: Event) -> None:
        data = event.data
        asset_id = data.get("asset_id")
        price = data.get("price")
        if asset_id is None or price is None:
            return
        market_id = self.universe.market_id_for_asset(asset_id)
        if market_id is None:
            return
        self._update(market_id, price=price, volume=float(data.get("size") or 0))

    def _update(self, market_id: str, price: float, volume: float) -> None:
        minute = utcnow().replace(second=0, microsecond=0)
        bar = self._bars.get(market_id)
        if bar is None or bar["minute"] != minute:
            if bar is not None:
                self._completed.append(self._to_price(market_id, bar))
            self._bars[market_id] = {
                "minute": minute,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
            }
            return
        bar["high"] = max(bar["high"], price)
        bar["low"] = min(bar["low"], price)
        bar["close"] = price
        bar["volume"] += volume

    @staticmethod
    def _to_price(market_id: str, bar: dict) -> Price:
        return Price(
            market_id=market_id,
            timestamp=bar["minute"],
            open=bar["open"],
            high=bar["high"],
            low=bar["low"],
            close=bar["close"],
            volume=bar["volume"],
        )

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self.flush()
            except Exception as e:  # noqa: BLE001 — keep the loop alive
                logger.error(f"bar flush failed: {e!r}")

    async def flush(self) -> None:
        """Flush completed bars (and any bar whose minute has passed)."""
        now_minute = utcnow().replace(second=0, microsecond=0)
        batch = list(self._completed)
        self._completed = []
        for market_id, bar in list(self._bars.items()):
            if bar["minute"] < now_minute:
                batch.append(self._to_price(market_id, bar))
                del self._bars[market_id]
        if batch:
            written = await self.db.add_bars(batch)
            self._written += written
        self._last_flush = utcnow()

        today = self._last_flush.date()
        if self._last_prune_day != today:
            self._last_prune_day = today
            cutoff = utcnow() - timedelta(days=settings.bar_retention_days)
            pruned = await self.db.prune_bars(cutoff)
            await self.db.prune_scanner_runs(settings.scanner_run_retention)
            if pruned:
                logger.info(f"pruned {pruned} old bars")

    async def stop(self) -> None:
        """Final flush on shutdown."""
        try:
            await self.flush()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"final bar flush failed: {e}")

    def status(self) -> dict:
        return {
            "bars_written": self._written,
            "active_bars": len(self._bars),
            "last_flush": self._last_flush.isoformat() if self._last_flush else None,
        }
