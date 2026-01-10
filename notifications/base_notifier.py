"""Base notifier class for alert delivery."""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from core.events import Event, EventBus, EventType, event_bus
from core.models import Alert

logger = logging.getLogger(__name__)


class BaseNotifier(ABC):
    """
    Abstract base class for notification delivery.

    Notifiers subscribe to alert events and deliver them
    through various channels (desktop, telegram, webhook, etc).
    """

    def __init__(
        self,
        name: str,
        event_bus: EventBus = event_bus,
    ):
        self.name = name
        self.event_bus = event_bus
        self._running = False

    @abstractmethod
    async def send(self, alert: Alert) -> bool:
        """
        Send a notification for an alert.

        Args:
            alert: The alert to notify about

        Returns:
            True if sent successfully
        """
        pass

    async def start(self) -> None:
        """Start the notifier."""
        if self._running:
            return

        self._running = True
        self.event_bus.subscribe(EventType.ALERT_CREATED, self._handle_alert)
        logger.info(f"Notifier '{self.name}' started")

    async def stop(self) -> None:
        """Stop the notifier."""
        self._running = False
        self.event_bus.unsubscribe(EventType.ALERT_CREATED, self._handle_alert)
        logger.info(f"Notifier '{self.name}' stopped")

    async def _handle_alert(self, event: Event) -> None:
        """Handle an alert event."""
        if not self._running:
            return

        if not isinstance(event.data, Alert):
            return

        try:
            success = await self.send(event.data)
            if success:
                logger.debug(f"Notification sent via {self.name}")
            else:
                logger.warning(f"Failed to send notification via {self.name}")
        except Exception as e:
            logger.error(f"Error in notifier '{self.name}': {e}")
