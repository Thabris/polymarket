"""Volume spike monitor - detects unusual trading volume."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from config.settings import settings
from core.events import Event, EventType
from core.models import Alert, AlertType, Severity, Trade
from monitors.base_monitor import BaseMonitor

logger = logging.getLogger(__name__)


class VolumeMonitor(BaseMonitor):
    """
    Monitors trading volume and triggers alerts for unusual spikes.

    Tracks cumulative volume for each market within a time window
    and alerts when it exceeds the configured threshold.
    """

    def __init__(
        self,
        threshold_usd: Optional[float] = None,
        window_minutes: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(name="volume_monitor", **kwargs)

        self.threshold_usd = threshold_usd or settings.volume_spike_threshold_usd
        self.window_minutes = window_minutes or settings.volume_spike_window_minutes

        # Volume history: market_id -> list of (timestamp, volume_usd)
        self._volume_history: Dict[str, List[Tuple[datetime, float]]] = defaultdict(list)

        # Track last alert time to avoid spam
        self._last_alert: Dict[str, datetime] = {}
        self._alert_cooldown = timedelta(minutes=10)

    @property
    def alert_type(self) -> AlertType:
        return AlertType.VOLUME_SPIKE

    @property
    def subscribed_events(self) -> list[EventType]:
        return [EventType.TRADE, EventType.VOLUME_UPDATE]

    async def analyze(self, event: Event) -> Optional[Alert]:
        """Analyze trade/volume for spikes."""
        market_id = None
        volume_usd = 0.0
        timestamp = event.timestamp

        # Handle trade events
        if isinstance(event.data, Trade):
            trade: Trade = event.data
            market_id = trade.market_id
            volume_usd = trade.value_usd
            timestamp = trade.timestamp

        # Handle volume update events (dict format)
        elif isinstance(event.data, dict):
            market_id = event.data.get("market_id")
            volume_usd = event.data.get("volume_usd", 0.0)

        if not market_id or volume_usd <= 0:
            return None

        # Add to history
        self._add_volume(market_id, timestamp, volume_usd)

        # Clean old entries
        self._cleanup_history(market_id, timestamp)

        # Calculate window volume
        window_volume = self._calculate_window_volume(market_id, timestamp)

        # Check threshold
        if window_volume < self.threshold_usd:
            return None

        # Check cooldown
        if self._in_cooldown(market_id, timestamp):
            return None

        self._last_alert[market_id] = timestamp

        # Create alert
        severity = self._get_severity(window_volume)

        return self.create_alert(
            title=f"💰 Volume spike: ${window_volume:,.0f}",
            message=(
                f"Market {market_id[:8]}... saw ${window_volume:,.0f} "
                f"traded in the last {self.window_minutes} minutes"
            ),
            market_id=market_id,
            severity=severity,
            data={
                "volume_usd": window_volume,
                "window_minutes": self.window_minutes,
                "threshold_usd": self.threshold_usd,
            },
        )

    def _add_volume(
        self, market_id: str, timestamp: datetime, volume_usd: float
    ) -> None:
        """Add a volume point to history."""
        self._volume_history[market_id].append((timestamp, volume_usd))

    def _cleanup_history(self, market_id: str, current_time: datetime) -> None:
        """Remove old volume points outside the window."""
        cutoff = current_time - timedelta(minutes=self.window_minutes * 2)
        self._volume_history[market_id] = [
            (ts, vol)
            for ts, vol in self._volume_history[market_id]
            if ts > cutoff
        ]

    def _calculate_window_volume(
        self, market_id: str, current_time: datetime
    ) -> float:
        """Calculate total volume within the window."""
        window_start = current_time - timedelta(minutes=self.window_minutes)

        total = 0.0
        for ts, vol in self._volume_history[market_id]:
            if ts >= window_start:
                total += vol

        return total

    def _in_cooldown(self, market_id: str, current_time: datetime) -> bool:
        """Check if we're in alert cooldown period."""
        last = self._last_alert.get(market_id)
        if last is None:
            return False
        return (current_time - last) < self._alert_cooldown

    def _get_severity(self, volume_usd: float) -> Severity:
        """Determine alert severity based on volume magnitude."""
        if volume_usd >= self.threshold_usd * 5:
            return Severity.CRITICAL
        elif volume_usd >= self.threshold_usd * 2:
            return Severity.WARNING
        return Severity.INFO

    def update_threshold(self, threshold_usd: float) -> None:
        """Update the volume threshold."""
        self.threshold_usd = threshold_usd
        logger.info(f"Volume threshold updated to ${threshold_usd:,.0f}")

    def update_window(self, window_minutes: int) -> None:
        """Update the time window."""
        self.window_minutes = window_minutes
        logger.info(f"Volume window updated to {window_minutes} minutes")
