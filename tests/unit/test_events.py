"""Unit tests for event bus."""

import asyncio

import pytest

from core.events import Event, EventBus, EventType


class TestEventBus:
    """Tests for EventBus."""

    @pytest.mark.asyncio
    async def test_subscribe_and_emit(self):
        """Should receive events after subscribing."""
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe(EventType.BOOK_TOP, handler)
        await bus.start()

        event = Event(type=EventType.BOOK_TOP, data={"price": 0.5})
        await bus.emit(event)

        # Wait for processing
        await asyncio.sleep(0.1)

        await bus.stop()

        assert len(received) == 1
        assert received[0].type == EventType.BOOK_TOP

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        """Should not receive events after unsubscribing."""
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe(EventType.BOOK_TOP, handler)
        bus.unsubscribe(EventType.BOOK_TOP, handler)

        await bus.start()

        event = Event(type=EventType.BOOK_TOP, data={"price": 0.5})
        await bus.emit(event)

        await asyncio.sleep(0.1)
        await bus.stop()

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self):
        """Should receive all events with wildcard subscription."""
        bus = EventBus()
        received = []

        async def handler(event: Event):
            received.append(event)

        bus.subscribe(None, handler)  # Wildcard
        await bus.start()

        await bus.emit(Event(type=EventType.BOOK_TOP, data={}))
        await bus.emit(Event(type=EventType.TRADE_TICK, data={}))
        await bus.emit(Event(type=EventType.ALERT_CREATED, data={}))

        await asyncio.sleep(0.1)
        await bus.stop()

        assert len(received) == 3

    @pytest.mark.asyncio
    async def test_handler_error_isolation(self):
        """Handler errors should not affect other handlers."""
        bus = EventBus()
        received = []

        async def failing_handler(event: Event):
            raise ValueError("Test error")

        async def working_handler(event: Event):
            received.append(event)

        bus.subscribe(EventType.BOOK_TOP, failing_handler)
        bus.subscribe(EventType.BOOK_TOP, working_handler)

        await bus.start()

        event = Event(type=EventType.BOOK_TOP, data={})
        await bus.emit(event)

        await asyncio.sleep(0.1)
        await bus.stop()

        # Working handler should still receive the event
        assert len(received) == 1
