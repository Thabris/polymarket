"""Event bus for async pub/sub pattern."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional

from core.timeutil import utcnow

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Types of events in the system."""

    # Stream events (from data/stream.py)
    BOOK_TOP = "book_top"  # {asset_id, market_id?, bid, ask, mid, ts}
    TRADE_TICK = "trade_tick"  # {asset_id, price, size, side, ts}

    # Signal / execution events
    SIGNAL_CREATED = "signal_created"
    SIGNAL_UPDATED = "signal_updated"
    POSITION_UPDATED = "position_updated"

    # Alert events
    ALERT_CREATED = "alert_created"
    ALERT_ACKNOWLEDGED = "alert_acknowledged"


@dataclass
class Event:
    """Base event class."""

    type: EventType
    data: Any
    timestamp: datetime = field(default_factory=utcnow)
    source: Optional[str] = None


# Type alias for event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """
    Async event bus for decoupled communication between components.

    Features:
    - Subscribe to specific event types
    - Subscribe to all events (wildcard)
    - Async event handling
    - Error isolation (handler errors don't affect others)
    """

    def __init__(self):
        self._handlers: Dict[EventType, List[EventHandler]] = {}
        self._wildcard_handlers: List[EventHandler] = []
        self._event_queue: asyncio.Queue[Event] = asyncio.Queue()
        self._running = False
        self._processor_task: Optional[asyncio.Task] = None

    def subscribe(
        self,
        event_type: Optional[EventType],
        handler: EventHandler,
    ) -> None:
        """
        Subscribe to events.

        Args:
            event_type: Type of event to subscribe to, or None for all events
            handler: Async function to call when event occurs
        """
        if event_type is None:
            # Wildcard subscription
            if handler not in self._wildcard_handlers:
                self._wildcard_handlers.append(handler)
        else:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def unsubscribe(
        self,
        event_type: Optional[EventType],
        handler: EventHandler,
    ) -> None:
        """
        Unsubscribe from events.

        Args:
            event_type: Type of event, or None for wildcard
            handler: Handler to remove
        """
        if event_type is None:
            if handler in self._wildcard_handlers:
                self._wildcard_handlers.remove(handler)
        else:
            if event_type in self._handlers:
                if handler in self._handlers[event_type]:
                    self._handlers[event_type].remove(handler)

    async def emit(self, event: Event) -> None:
        """
        Emit an event to all subscribers.

        Args:
            event: Event to emit
        """
        await self._event_queue.put(event)

    def emit_sync(self, event: Event) -> None:
        """
        Emit an event synchronously (non-blocking).

        Args:
            event: Event to emit
        """
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping event")

    async def start(self) -> None:
        """Start the event processor."""
        if self._running:
            return

        self._running = True
        self._processor_task = asyncio.create_task(self._process_events())
        logger.info("Event bus started")

    async def stop(self) -> None:
        """Stop the event processor."""
        self._running = False

        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
            self._processor_task = None

        logger.info("Event bus stopped")

    async def _process_events(self) -> None:
        """Process events from the queue."""
        while self._running:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=1.0,
                )
                await self._dispatch(event)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing event: {e}")

    async def _dispatch(self, event: Event) -> None:
        """Dispatch an event to all matching handlers."""
        handlers: List[EventHandler] = []

        # Get type-specific handlers
        if event.type in self._handlers:
            handlers.extend(self._handlers[event.type])

        # Add wildcard handlers
        handlers.extend(self._wildcard_handlers)

        # Call all handlers concurrently
        if handlers:
            tasks = [self._call_handler(handler, event) for handler in handlers]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _call_handler(self, handler: EventHandler, event: Event) -> None:
        """Call a single handler with error isolation."""
        try:
            await handler(event)
        except Exception as e:
            logger.error(
                f"Error in event handler {handler.__name__} "
                f"for {event.type}: {e}"
            )


# Global event bus instance
event_bus = EventBus()


# Convenience functions
async def emit(event_type: EventType, data: Any, source: Optional[str] = None) -> None:
    """
    Emit an event.

    Args:
        event_type: Type of event
        data: Event data
        source: Optional source identifier
    """
    event = Event(type=event_type, data=data, source=source)
    await event_bus.emit(event)


def emit_sync(event_type: EventType, data: Any, source: Optional[str] = None) -> None:
    """
    Emit an event synchronously (non-blocking).

    Args:
        event_type: Type of event
        data: Event data
        source: Optional source identifier
    """
    event = Event(type=event_type, data=data, source=source)
    event_bus.emit_sync(event)


def subscribe(
    event_type: Optional[EventType] = None,
) -> Callable[[EventHandler], EventHandler]:
    """
    Decorator to subscribe a function to events.

    Args:
        event_type: Type of event, or None for all events

    Returns:
        Decorator function
    """

    def decorator(handler: EventHandler) -> EventHandler:
        event_bus.subscribe(event_type, handler)
        return handler

    return decorator
