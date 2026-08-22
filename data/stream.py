"""Polymarket CLOB market-channel WebSocket stream.

Protocol (verified by live capture, tests/fixtures/ws_capture.json, Aug 2026):
- one handshake per connection: {"assets_ids": [...], "type": "market"}
- app-level keepalive: send text "PING", server replies text "PONG"
- frames are JSON objects (occasionally arrays of objects); event_type in
  {book, price_change, last_trade_price, tick_size_change}
- price_change carries best_bid/best_ask PER ASSET in price_changes[] —
  top-of-book comes free, no local book maintenance needed
- book levels are not best-first: best bid = max(bids), best ask = min(asks)

Normalized output on the EventBus:
- BOOK_TOP  {asset_id, bid, ask, mid, bid_size, ask_size, ts}
- TRADE_TICK {asset_id, price, size, side, ts}
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import websockets

from config.settings import settings
from core.events import Event, EventType, event_bus
from core.timeutil import parse_epoch_ms, utcnow

logger = logging.getLogger(__name__)


def parse_frame(raw: str) -> list[dict]:
    """Parse one raw WS frame into normalized event dicts (pure function).

    Returns dicts: {"kind": "book_top"|"trade_tick", ...}. Non-JSON frames
    (PONG keepalives) return [].
    """
    raw = raw.strip()
    if not raw or raw in ("PONG", "PING"):
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = parsed if isinstance(parsed, list) else [parsed]
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        event_type = item.get("event_type")
        if event_type == "book":
            out.extend(_parse_book(item))
        elif event_type == "price_change":
            out.extend(_parse_price_change(item))
        elif event_type == "last_trade_price":
            out.extend(_parse_trade(item))
        # tick_size_change: ignored (tick size is refreshed by the market sync)
    return out


def _parse_book(item: dict) -> list[dict]:
    asset_id = str(item.get("asset_id", ""))
    if not asset_id:
        return []
    best_bid = best_bid_size = best_ask = best_ask_size = None
    for level in item.get("bids") or []:
        try:
            price, size = float(level["price"]), float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if best_bid is None or price > best_bid:
            best_bid, best_bid_size = price, size
    for level in item.get("asks") or []:
        try:
            price, size = float(level["price"]), float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if best_ask is None or price < best_ask:
            best_ask, best_ask_size = price, size
    if best_bid is None and best_ask is None:
        return []
    mid = None
    if best_bid is not None and best_ask is not None:
        mid = (best_bid + best_ask) / 2
    return [{
        "kind": "book_top",
        "asset_id": asset_id,
        "bid": best_bid,
        "ask": best_ask,
        "mid": mid,
        "bid_size": best_bid_size,
        "ask_size": best_ask_size,
        "ts": parse_epoch_ms(item.get("timestamp")) or utcnow(),
    }]


def _parse_price_change(item: dict) -> list[dict]:
    ts = parse_epoch_ms(item.get("timestamp")) or utcnow()
    out = []
    for change in item.get("price_changes") or []:
        asset_id = str(change.get("asset_id", ""))
        if not asset_id:
            continue
        try:
            bid = float(change["best_bid"]) if change.get("best_bid") not in (None, "") else None
            ask = float(change["best_ask"]) if change.get("best_ask") not in (None, "") else None
        except (TypeError, ValueError):
            continue
        if bid is None and ask is None:
            continue
        mid = (bid + ask) / 2 if bid is not None and ask is not None else None
        out.append({
            "kind": "book_top",
            "asset_id": asset_id,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "bid_size": None,
            "ask_size": None,
            "ts": ts,
        })
    return out


def _parse_trade(item: dict) -> list[dict]:
    asset_id = str(item.get("asset_id", ""))
    if not asset_id:
        return []
    try:
        price = float(item["price"])
    except (KeyError, TypeError, ValueError):
        return []
    try:
        size = float(item.get("size") or 0)
    except (TypeError, ValueError):
        size = 0.0
    return [{
        "kind": "trade_tick",
        "asset_id": asset_id,
        "price": price,
        "size": size,
        "side": item.get("side"),
        "ts": parse_epoch_ms(item.get("timestamp")) or utcnow(),
    }]


class WsShard:
    """One WS connection subscribing a chunk of asset ids."""

    def __init__(self, shard_id: int, asset_ids: list[str]) -> None:
        self.shard_id = shard_id
        self.asset_ids = asset_ids
        self.connected = False
        self.messages = 0
        self.reconnects = 0
        self.last_msg_at = None
        self._stop = False

    async def run(self) -> None:
        delay = settings.ws_reconnect_delay
        while not self._stop:
            try:
                async with websockets.connect(
                    settings.polymarket_ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**23,
                ) as ws:
                    await ws.send(json.dumps({"assets_ids": self.asset_ids, "type": "market"}))
                    self.connected = True
                    delay = settings.ws_reconnect_delay
                    logger.info(f"ws shard {self.shard_id}: connected ({len(self.asset_ids)} assets)")
                    ping_task = asyncio.create_task(self._app_ping(ws))
                    try:
                        await self._recv_loop(ws)
                    finally:
                        ping_task.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — reconnect on any failure
                logger.warning(f"ws shard {self.shard_id}: {e!r}")
            self.connected = False
            if self._stop:
                break
            self.reconnects += 1
            await asyncio.sleep(delay)
            delay = min(delay * 2, settings.ws_max_reconnect_delay)

    async def _recv_loop(self, ws) -> None:
        while not self._stop:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=settings.ws_stale_seconds)
            except asyncio.TimeoutError:
                logger.warning(f"ws shard {self.shard_id}: stale socket, forcing reconnect")
                return
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            self.messages += 1
            self.last_msg_at = utcnow()
            for normalized in parse_frame(raw):
                kind = normalized.pop("kind")
                event_type = EventType.BOOK_TOP if kind == "book_top" else EventType.TRADE_TICK
                await event_bus.emit(Event(type=event_type, data=normalized, source=f"ws{self.shard_id}"))

    async def _app_ping(self, ws) -> None:
        while True:
            await asyncio.sleep(settings.ws_app_ping_seconds)
            try:
                await ws.send("PING")
            except Exception:  # noqa: BLE001 — recv loop will notice the dead socket
                return

    def stop(self) -> None:
        self._stop = True


class MarketStream:
    """Manages WS shards over the current universe; restarts on changes."""

    def __init__(self) -> None:
        self._asset_ids: list[str] = []
        self._shards: list[WsShard] = []
        self._restart = asyncio.Event()
        self._running = False

    def set_universe(self, asset_ids: list[str]) -> None:
        """Called by UniverseManager when membership changes."""
        if set(asset_ids) == set(self._asset_ids):
            return
        self._asset_ids = list(asset_ids)
        logger.info(f"stream universe set: {len(asset_ids)} assets")
        self._restart.set()

    async def run_forever(self) -> None:
        self._running = True
        while self._running:
            if not self._asset_ids:
                await asyncio.sleep(5)
                continue
            self._restart.clear()
            chunk = settings.ws_assets_per_connection
            self._shards = [
                WsShard(i, self._asset_ids[i * chunk:(i + 1) * chunk])
                for i in range((len(self._asset_ids) + chunk - 1) // chunk)
            ]
            tasks = [asyncio.create_task(s.run(), name=f"ws-shard-{s.shard_id}") for s in self._shards]
            restart_wait = asyncio.create_task(self._restart.wait())
            try:
                done, _pending = await asyncio.wait(
                    [restart_wait, *tasks], return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                restart_wait.cancel()
                for shard, task in zip(self._shards, tasks):
                    shard.stop()
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            if not self._restart.is_set():
                # a shard task ended on its own — brief pause then rebuild
                await asyncio.sleep(2)

    async def stop(self) -> None:
        self._running = False
        self._restart.set()
        for shard in self._shards:
            shard.stop()

    def status(self) -> dict:
        return {
            "universe": len(self._asset_ids),
            "shards": [
                {
                    "id": s.shard_id,
                    "assets": len(s.asset_ids),
                    "connected": s.connected,
                    "messages": s.messages,
                    "reconnects": s.reconnects,
                    "last_msg_at": s.last_msg_at.isoformat() if s.last_msg_at else None,
                }
                for s in self._shards
            ],
        }
