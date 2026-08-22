"""Spike detection tests: synthetic ramps, steps, and exclusion filters."""

from collections import deque
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from core.models import Market
from core.timeutil import utcnow
from scanners.news_fade import NewsFadeScanner
from signals.models import SignalSide


def _market(**kwargs) -> Market:
    base = {
        "id": "m1",
        "condition_id": "0x1",
        "question": "Will the thing happen?",
        "liquidity": 200000.0,
        "category": "politics",
        "token_id_yes": "yes-token",
        "token_id_no": "no-token",
        "end_date": utcnow() + timedelta(days=30),
    }
    base.update(kwargs)
    return Market(**base)


def _scanner() -> NewsFadeScanner:
    return NewsFadeScanner(universe=MagicMock(), signal_service=MagicMock(), database=MagicMock())


def _series(prices: list[float]) -> deque:
    now = utcnow()
    return deque(
        (now - timedelta(seconds=len(prices) - i), p) for i, p in enumerate(prices)
    )


class TestSpikeDetection:
    def test_up_spike_emits_buy_no_with_inverted_zone(self):
        scanner = _scanner()
        dq = _series([0.35] * 10 + [0.40, 0.50, 0.62])
        signal = scanner._detect(_market(), dq, 0.62)
        assert signal is not None
        assert signal.side == SignalSide.BUY_NO
        assert signal.snapshot["direction"] == "up"
        assert signal.snapshot["invert"] is True
        assert signal.snapshot["watch_asset_id"] == "yes-token"
        # NO-space zone: extreme 0.62 -> NO 0.38 at the low end
        assert signal.entry_zone_low == pytest.approx(1 - 0.62, abs=1e-6)
        assert signal.entry_zone_high > signal.entry_zone_low
        # display reference = zone midpoint, same convention both directions
        assert signal.entry_price == pytest.approx(
            (signal.entry_zone_low + signal.entry_zone_high) / 2, abs=1e-4)

    def test_down_spike_emits_buy_yes(self):
        scanner = _scanner()
        dq = _series([0.62] * 10 + [0.55, 0.50, 0.48])
        signal = scanner._detect(_market(), dq, 0.48)
        assert signal is not None
        assert signal.side == SignalSide.BUY_YES
        assert signal.snapshot["direction"] == "down"
        assert signal.entry_zone_low == pytest.approx(0.48, abs=1e-6)

    def test_small_move_ignored(self):
        scanner = _scanner()
        dq = _series([0.50, 0.52, 0.55, 0.58])  # 8pp < 12pp threshold
        assert scanner._detect(_market(), dq, 0.58) is None

    def test_low_liquidity_excluded(self):
        scanner = _scanner()
        dq = _series([0.35] * 10 + [0.62])
        assert scanner._detect(_market(liquidity=1000.0), dq, 0.62) is None

    def test_crypto_excluded(self):
        scanner = _scanner()
        dq = _series([0.35] * 10 + [0.62])
        assert scanner._detect(_market(category="crypto"), dq, 0.62) is None

    def test_near_resolution_excluded(self):
        scanner = _scanner()
        dq = _series([0.35] * 10 + [0.62])
        market = _market(end_date=utcnow() + timedelta(hours=10))
        assert scanner._detect(market, dq, 0.62) is None

    def test_dedup_key_stable_within_window(self):
        scanner = _scanner()
        dq = _series([0.35] * 10 + [0.62])
        s1 = scanner._detect(_market(), dq, 0.62)
        dq.append((utcnow(), 0.63))
        s2 = scanner._detect(_market(), dq, 0.63)
        assert s1.dedup_key == s2.dedup_key  # one signal per spike window
