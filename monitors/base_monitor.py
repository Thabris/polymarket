"""Base monitor class for all monitoring functionality."""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from core.events import Event, EventBus, EventType, event_bus
from core.models import Alert, AlertType, Severity
from data.storage import Database, db

logger = logging.getLogger(__name__)


class BaseMonitor(ABC):
    """
    Abstract base class for monitors.

    Monitors subscribe to events and analyze data to detect
    conditions that should trigger alerts.
    """

    def __init__(
        self,
        name: str,
        event_bus: EventBus = event_bus,
        database: Database = db,
    ):
        self.name = name
        self.event_bus = event_bus
        self.database = database
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    @abstractmethod
    def alert_type(self) -> AlertType:
        """The type of alert this monitor creates."""
        pass

    @property
    @abstractmethod
    def subscribed_events(self) -> list[EventType]:
        """Event types this monitor subscribes to."""
        pass

    @abstractmethod
    async def analyze(self, event: Event) -> Optional[Alert]:
        """
        Analyze an event and return an alert if triggered.

        Args:
            event: The event to analyze

        Returns:
            Alert if triggered, None otherwise
        """
        pass

    async def start(self) -> None:
        """Start the monitor."""
        if self._running:
            return

        self._running = True

        # Subscribe to events
        for event_type in self.subscribed_events:
            self.event_bus.subscribe(event_type, self._handle_event)

        logger.info(f"Monitor '{self.name}' started")

    async def stop(self) -> None:
        """Stop the monitor."""
        self._running = False

        # Unsubscribe from events
        for event_type in self.subscribed_events:
            self.event_bus.unsubscribe(event_type, self._handle_event)

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info(f"Monitor '{self.name}' stopped")

    async def _handle_event(self, event: Event) -> None:
        """Handle an incoming event."""
        if not self._running:
            return

        try:
            alert = await self.analyze(event)
            if alert:
                await self._emit_alert(alert)
        except Exception as e:
            logger.error(f"Error in monitor '{self.name}': {e}")

    async def _emit_alert(self, alert: Alert) -> None:
        """Store and emit an alert."""
        try:
            # Store in database
            stored = await self.database.add_alert(
                alert_type=alert.alert_type.value,
                title=alert.title,
                message=alert.message,
                market_id=alert.market_id,
                severity=alert.severity.value,
                data=alert.data,
            )
            alert.id = stored.id

            # Emit event
            await self.event_bus.emit(
                Event(
                    type=EventType.ALERT_CREATED,
                    data=alert,
                    source=self.name,
                )
            )

            logger.info(f"Alert created: {alert.title}")

        except Exception as e:
            logger.error(f"Failed to emit alert: {e}")

    def create_alert(
        self,
        title: str,
        message: str,
        market_id: Optional[str] = None,
        severity: Severity = Severity.INFO,
        data: Optional[dict] = None,
    ) -> Alert:
        """
        Create an alert instance.

        Args:
            title: Alert title
            message: Alert message
            market_id: Optional market ID
            severity: Alert severity
            data: Optional additional data

        Returns:
            Alert instance
        """
        return Alert(
            alert_type=self.alert_type,
            title=title,
            message=message,
            market_id=market_id,
            severity=severity,
            data=data,
            created_at=datetime.utcnow(),
        )
