"""Price change monitor - detects significant price movements."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from config.settings import settings
from core.events import Event, EventType
from core.models import Alert, AlertType, PriceUpdate, Severity
from monitors.base_monitor import BaseMonitor

logger = logging.getLogger(__name__)


class PriceMonitor(BaseMonitor):
    """
    Monitors price changes and triggers alerts for significant movements.

    Tracks price history for each market and detects when price
    changes exceed the configured threshold within the time window.
    """

    def __init__(
        self,
        threshold_pct: Optional[float] = None,
        window_minutes: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(name="price_monitor", **kwargs)

        self.threshold_pct = threshold_pct or settings.price_change_threshold_pct
        self.window_minutes = window_minutes or settings.price_change_window_minutes

        # Price history: market_id -> list of (timestamp, price)
        self._price_history: Dict[str, List[Tuple[datetime, float]]] = defaultdict(list)

        # Track last alert time to avoid spam
        self._last_alert: Dict[str, datetime] = {}
        self._alert_cooldown = timedelta(minutes=5)

    @property
    def alert_type(self) -> AlertType:
        return AlertType.PRICE_CHANGE

    @property
    def subscribed_events(self) -> list[EventType]:
        return [EventType.PRICE_UPDATE]

    async def analyze(self, event: Event) -> Optional[Alert]:
        """Analyze price update for significant changes."""
        if not isinstance(event.data, PriceUpdate):
            return None

        update: PriceUpdate = event.data
        market_id = update.market_id
        current_price = update.price
        current_time = update.timestamp

        # Add to history
        self._add_price(market_id, current_time, current_price)

        # Clean old entries
        self._cleanup_history(market_id, current_time)

        # Check for significant change
        change = self._calculate_change(market_id)
        if change is None:
            return None

        change_pct, old_price, direction = change

        # Check threshold
        if abs(change_pct) < self.threshold_pct:
            return None

        # Check cooldown
        if self._in_cooldown(market_id, current_time):
            return None

        self._last_alert[market_id] = current_time

        # Create alert
        severity = self._get_severity(abs(change_pct))
        direction_str = "up" if direction > 0 else "down"
        emoji = "📈" if direction > 0 else "📉"

        return self.create_alert(
            title=f"{emoji} Price {direction_str} {abs(change_pct):.1f}%",
            message=(
                f"Market {market_id[:8]}... price moved from "
                f"${old_price:.4f} to ${current_price:.4f} "
                f"({change_pct:+.1f}%) in {self.window_minutes} minutes"
            ),
            market_id=market_id,
            severity=severity,
            data={
                "old_price": old_price,
                "new_price": current_price,
                "change_pct": change_pct,
                "window_minutes": self.window_minutes,
            },
        )

    def _add_price(self, market_id: str, timestamp: datetime, price: float) -> None:
        """Add a price point to history."""
        self._price_history[market_id].append((timestamp, price))

    def _cleanup_history(self, market_id: str, current_time: datetime) -> None:
        """Remove old price points outside the window."""
        cutoff = current_time - timedelta(minutes=self.window_minutes * 2)
        self._price_history[market_id] = [
            (ts, price)
            for ts, price in self._price_history[market_id]
            if ts > cutoff
        ]

    def _calculate_change(
        self, market_id: str
    ) -> Optional[Tuple[float, float, int]]:
        """
        Calculate price change within window.

        Returns:
            Tuple of (change_pct, old_price, direction) or None
        """
        history = self._price_history[market_id]
        if len(history) < 2:
            return None

        current_time, current_price = history[-1]
        window_start = current_time - timedelta(minutes=self.window_minutes)

        # Find the price at window start
        old_price = None
        for ts, price in history:
            if ts >= window_start:
                old_price = price
                break

        if old_price is None or old_price == 0:
            return None

        change_pct = ((current_price - old_price) / old_price) * 100
        direction = 1 if current_price > old_price else -1

        return change_pct, old_price, direction

    def _in_cooldown(self, market_id: str, current_time: datetime) -> bool:
        """Check if we're in alert cooldown period."""
        last = self._last_alert.get(market_id)
        if last is None:
            return False
        return (current_time - last) < self._alert_cooldown

    def _get_severity(self, change_pct: float) -> Severity:
        """Determine alert severity based on change magnitude."""
        if change_pct >= 20:
            return Severity.CRITICAL
        elif change_pct >= 10:
            return Severity.WARNING
        return Severity.INFO

    def update_threshold(self, threshold_pct: float) -> None:
        """Update the price change threshold."""
        self.threshold_pct = threshold_pct
        logger.info(f"Price threshold updated to {threshold_pct}%")

    def update_window(self, window_minutes: int) -> None:
        """Update the time window."""
        self.window_minutes = window_minutes
        logger.info(f"Price window updated to {window_minutes} minutes")
