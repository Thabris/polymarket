"""Unit tests for monitors."""

from datetime import datetime, timedelta

import pytest

from core.events import Event, EventType
from core.models import AlertType, PriceUpdate, Severity, Trade


class TestPriceMonitor:
    """Tests for PriceMonitor."""

    @pytest.mark.asyncio
    async def test_no_alert_below_threshold(self):
        """Should not alert when price change is below threshold."""
        from monitors.price_monitor import PriceMonitor

        monitor = PriceMonitor(threshold_pct=10.0, window_minutes=5)

        # First price update
        event1 = Event(
            type=EventType.PRICE_UPDATE,
            data=PriceUpdate(
                market_id="test",
                token_id="token-yes",
                price=0.50,
                timestamp=datetime.utcnow() - timedelta(minutes=2),
            ),
        )
        alert = await monitor.analyze(event1)
        assert alert is None

        # Second price update (5% change - below 10% threshold)
        event2 = Event(
            type=EventType.PRICE_UPDATE,
            data=PriceUpdate(
                market_id="test",
                token_id="token-yes",
                price=0.525,  # 5% increase
                timestamp=datetime.utcnow(),
            ),
        )
        alert = await monitor.analyze(event2)
        assert alert is None

    @pytest.mark.asyncio
    async def test_alert_above_threshold(self):
        """Should alert when price change exceeds threshold."""
        from monitors.price_monitor import PriceMonitor

        monitor = PriceMonitor(threshold_pct=10.0, window_minutes=5)

        # First price update
        event1 = Event(
            type=EventType.PRICE_UPDATE,
            data=PriceUpdate(
                market_id="test",
                token_id="token-yes",
                price=0.50,
                timestamp=datetime.utcnow() - timedelta(minutes=2),
            ),
        )
        await monitor.analyze(event1)

        # Second price update (20% change - above threshold)
        event2 = Event(
            type=EventType.PRICE_UPDATE,
            data=PriceUpdate(
                market_id="test",
                token_id="token-yes",
                price=0.60,  # 20% increase
                timestamp=datetime.utcnow(),
            ),
        )
        alert = await monitor.analyze(event2)

        assert alert is not None
        assert alert.alert_type == AlertType.PRICE_CHANGE
        assert alert.market_id == "test"


class TestVolumeMonitor:
    """Tests for VolumeMonitor."""

    @pytest.mark.asyncio
    async def test_no_alert_below_threshold(self):
        """Should not alert when volume is below threshold."""
        from monitors.volume_monitor import VolumeMonitor

        monitor = VolumeMonitor(threshold_usd=10000, window_minutes=5)

        event = Event(
            type=EventType.TRADE,
            data=Trade(
                id="trade-1",
                market_id="test",
                timestamp=datetime.utcnow(),
                side="buy",
                outcome="yes",
                price=0.50,
                size=100,
                value_usd=50,  # Below threshold
            ),
        )

        alert = await monitor.analyze(event)
        assert alert is None

    @pytest.mark.asyncio
    async def test_alert_above_threshold(self):
        """Should alert when cumulative volume exceeds threshold."""
        from monitors.volume_monitor import VolumeMonitor

        monitor = VolumeMonitor(threshold_usd=100, window_minutes=5)

        # Multiple trades that sum above threshold
        for i in range(5):
            event = Event(
                type=EventType.TRADE,
                data=Trade(
                    id=f"trade-{i}",
                    market_id="test",
                    timestamp=datetime.utcnow(),
                    side="buy",
                    outcome="yes",
                    price=0.50,
                    size=100,
                    value_usd=50,  # 5 * 50 = 250 > 100
                ),
            )
            alert = await monitor.analyze(event)

        assert alert is not None
        assert alert.alert_type == AlertType.VOLUME_SPIKE


class TestWhaleMonitor:
    """Tests for WhaleMonitor."""

    @pytest.mark.asyncio
    async def test_no_alert_below_threshold(self):
        """Should not alert for small trades."""
        from monitors.whale_monitor import WhaleMonitor

        monitor = WhaleMonitor(threshold_usd=5000)

        event = Event(
            type=EventType.TRADE,
            data=Trade(
                id="trade-1",
                market_id="test",
                timestamp=datetime.utcnow(),
                side="buy",
                outcome="yes",
                price=0.50,
                size=100,
                value_usd=100,  # Below threshold
            ),
        )

        alert = await monitor.analyze(event)
        assert alert is None

    @pytest.mark.asyncio
    async def test_alert_for_whale_trade(self):
        """Should alert for large trades."""
        from monitors.whale_monitor import WhaleMonitor

        monitor = WhaleMonitor(threshold_usd=5000)

        event = Event(
            type=EventType.TRADE,
            data=Trade(
                id="trade-1",
                market_id="test",
                timestamp=datetime.utcnow(),
                side="buy",
                outcome="yes",
                price=0.50,
                size=20000,
                value_usd=10000,  # Above threshold
            ),
        )

        alert = await monitor.analyze(event)

        assert alert is not None
        assert alert.alert_type == AlertType.WHALE_TRADE
        assert alert.severity == Severity.WARNING  # 2x threshold
