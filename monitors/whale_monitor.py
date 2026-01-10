"""Whale activity monitor - detects large individual trades."""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from config.settings import settings
from core.events import Event, EventType
from core.models import Alert, AlertType, Severity, Trade
from monitors.base_monitor import BaseMonitor

logger = logging.getLogger(__name__)


class WhaleMonitor(BaseMonitor):
    """
    Monitors for whale activity (large individual trades).

    Triggers alerts when a single trade exceeds the configured threshold.
    """

    def __init__(
        self,
        threshold_usd: Optional[float] = None,
        **kwargs,
    ):
        super().__init__(name="whale_monitor", **kwargs)

        self.threshold_usd = threshold_usd or settings.whale_trade_threshold_usd

        # Track recent whale alerts to avoid duplicates
        self._recent_alerts: Dict[str, datetime] = {}
        self._alert_cooldown = timedelta(minutes=1)

    @property
    def alert_type(self) -> AlertType:
        return AlertType.WHALE_TRADE

    @property
    def subscribed_events(self) -> list[EventType]:
        return [EventType.TRADE]

    async def analyze(self, event: Event) -> Optional[Alert]:
        """Analyze trade for whale activity."""
        if not isinstance(event.data, Trade):
            # Try to parse dict format
            if isinstance(event.data, dict):
                try:
                    trade = Trade(**event.data)
                except Exception:
                    return None
            else:
                return None
        else:
            trade = event.data

        # Check if trade exceeds threshold
        if trade.value_usd < self.threshold_usd:
            return None

        # Create unique key for deduplication
        trade_key = f"{trade.market_id}:{trade.id}"

        # Check cooldown
        if self._in_cooldown(trade_key, trade.timestamp):
            return None

        self._recent_alerts[trade_key] = trade.timestamp

        # Clean old entries
        self._cleanup_alerts(trade.timestamp)

        # Create alert
        severity = self._get_severity(trade.value_usd)
        side_emoji = "🟢" if trade.side.lower() == "buy" else "🔴"
        outcome = trade.outcome.upper() if trade.outcome else "?"

        return self.create_alert(
            title=f"🐋 Whale trade: ${trade.value_usd:,.0f}",
            message=(
                f"Large {trade.side.upper()} of {outcome} on market "
                f"{trade.market_id[:8]}...\n"
                f"{side_emoji} {trade.size:,.2f} shares @ ${trade.price:.4f} "
                f"= ${trade.value_usd:,.0f}"
            ),
            market_id=trade.market_id,
            severity=severity,
            data={
                "trade_id": trade.id,
                "side": trade.side,
                "outcome": trade.outcome,
                "price": trade.price,
                "size": trade.size,
                "value_usd": trade.value_usd,
                "maker": trade.maker,
                "taker": trade.taker,
            },
        )

    def _in_cooldown(self, trade_key: str, current_time: datetime) -> bool:
        """Check if we recently alerted for this trade."""
        last = self._recent_alerts.get(trade_key)
        if last is None:
            return False
        return (current_time - last) < self._alert_cooldown

    def _cleanup_alerts(self, current_time: datetime) -> None:
        """Remove old alert entries."""
        cutoff = current_time - timedelta(hours=1)
        self._recent_alerts = {
            key: ts
            for key, ts in self._recent_alerts.items()
            if ts > cutoff
        }

    def _get_severity(self, value_usd: float) -> Severity:
        """Determine alert severity based on trade size."""
        if value_usd >= self.threshold_usd * 10:
            return Severity.CRITICAL
        elif value_usd >= self.threshold_usd * 3:
            return Severity.WARNING
        return Severity.INFO

    def update_threshold(self, threshold_usd: float) -> None:
        """Update the whale threshold."""
        self.threshold_usd = threshold_usd
        logger.info(f"Whale threshold updated to ${threshold_usd:,.0f}")
