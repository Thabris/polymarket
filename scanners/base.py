"""Scanner base classes.

PeriodicScanner: REST snapshot scanners (theta, calendar) — run_once() on an
interval with scanner_runs bookkeeping and jitter.

StreamScanner: real-time scanners (news fade) — EventBus events are enqueued
by a lightweight handler; run_forever() consumes the queue so the supervisor
owns the processing task.
"""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod

from core.events import Event, EventType, event_bus
from core.timeutil import utcnow
from data.storage import Database
from signals.models import Signal
from signals.service import SignalService

logger = logging.getLogger(__name__)


class PeriodicScanner(ABC):
    """Base for interval-driven scanners."""

    name: str = "periodic"
    interval_minutes: int = 15

    def __init__(self, universe, signal_service: SignalService, database: Database) -> None:
        self.universe = universe
        self.signals = signal_service
        self.db = database
        self._last_run_at = None
        self._last_ok = None
        self._last_signals = 0
        self._runs = 0

    @abstractmethod
    async def run_once(self) -> tuple[int, list[Signal]]:
        """One scan pass. Returns (items_scanned, signals_to_publish)."""

    async def run_forever(self) -> None:
        while True:
            run_id = await self.db.start_scanner_run(self.name)
            emitted = 0
            try:
                items, found = await self.run_once()
                for signal in found:
                    if await self.signals.publish(signal):
                        emitted += 1
                await self.db.finish_scanner_run(
                    run_id, ok=True, items_scanned=items, signals_emitted=emitted
                )
                self._last_ok = True
            except Exception as e:  # noqa: BLE001 — a failed run must not kill the loop
                logger.error(f"{self.name} run failed: {e!r}")
                await self.db.finish_scanner_run(run_id, ok=False, error=repr(e)[:1000])
                self._last_ok = False
            self._runs += 1
            self._last_run_at = utcnow()
            self._last_signals = emitted
            jitter = 1.0 + random.uniform(-0.1, 0.1)
            await asyncio.sleep(self.interval_minutes * 60 * jitter)

    def status(self) -> dict:
        return {
            "runs": self._runs,
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "last_ok": self._last_ok,
            "last_signals": self._last_signals,
            "interval_minutes": self.interval_minutes,
        }


class StreamScanner(ABC):
    """Base for stream-driven scanners."""

    name: str = "stream"
    subscribed_events: list[EventType] = []

    def __init__(self, universe, signal_service: SignalService, database: Database) -> None:
        self.universe = universe
        self.signals = signal_service
        self.db = database
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=10000)
        self._events_seen = 0
        self._signals_emitted = 0
        self._started = False

    def start(self) -> None:
        """Subscribe the enqueue handler to the event bus."""
        if self._started:
            return
        for event_type in self.subscribed_events:
            event_bus.subscribe(event_type, self._enqueue)
        self._started = True

    async def _enqueue(self, event: Event) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            pass  # drop under backpressure; stream data is superseded quickly

    async def run_forever(self) -> None:
        """Consume events; supervisor owns this task."""
        self.start()
        while True:
            event = await self._queue.get()
            self._events_seen += 1
            try:
                found = await self.on_stream_event(event)
                for signal in found or []:
                    if await self.signals.publish(signal):
                        self._signals_emitted += 1
            except Exception as e:  # noqa: BLE001 — one bad event must not kill the consumer
                logger.error(f"{self.name} event handling failed: {e!r}")

    @abstractmethod
    async def on_stream_event(self, event: Event) -> list[Signal]:
        """Process one stream event; return signals to publish."""

    def status(self) -> dict:
        return {
            "events_seen": self._events_seen,
            "signals_emitted": self._signals_emitted,
            "queue_depth": self._queue.qsize(),
        }
