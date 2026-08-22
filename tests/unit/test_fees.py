"""Fee math and category classification tests."""

import pytest

from core.fees import (
    annualized_yield,
    category_for,
    fee_rate_for,
    net_edge_per_share,
    taker_fee_per_share,
)
from core.models import Market


def _market(**kwargs) -> Market:
    base = {"id": "1", "condition_id": "0x1", "question": "test?"}
    base.update(kwargs)
    return Market(**base)


class TestTakerFee:
    def test_peaks_at_50c(self):
        assert taker_fee_per_share(0.5, 0.05) == pytest.approx(0.0125)

    def test_near_zero_at_extremes(self):
        assert taker_fee_per_share(0.97, 0.05) == pytest.approx(0.05 * 0.97 * 0.03)
        assert taker_fee_per_share(0.97, 0.05) < 0.0015

    def test_zero_rate(self):
        assert taker_fee_per_share(0.5, 0.0) == 0.0

    def test_clamps_price(self):
        assert taker_fee_per_share(1.5, 0.05) == 0.0
        assert taker_fee_per_share(-0.5, 0.05) == 0.0


class TestFeeRateFor:
    def test_fees_disabled_means_free(self):
        # Verified live: geopolitics markets carry feesEnabled=False
        m = _market(fees_enabled=False, fee_rate=None)
        assert fee_rate_for(m) == 0.0

    def test_market_fee_schedule_is_authoritative(self):
        m = _market(fees_enabled=True, fee_rate=0.05)
        assert fee_rate_for(m) == 0.05

    def test_category_fallback(self):
        m = _market(fees_enabled=True, fee_rate=None, category="crypto")
        assert fee_rate_for(m) == 0.07

    def test_default_fallback(self):
        m = _market(fees_enabled=True, fee_rate=None)
        assert fee_rate_for(m) == 0.05


class TestCategoryFor:
    def test_from_tags(self):
        m = _market(tags=["Esports", "Dota 2"])
        assert category_for(m) == "sports"

    def test_from_fee_type(self):
        m = _market(fee_type="economics_fees")
        assert category_for(m) == "economics"

    def test_explicit_category_wins(self):
        m = _market(category="Politics", tags=["Crypto"])
        assert category_for(m) == "politics"

    def test_unknown(self):
        m = _market()
        assert category_for(m) is None


class TestThetaEconomics:
    def test_net_edge(self):
        # buy at 95c, 5% fee category: edge = 5c - fee(0.05*0.95*0.05)
        edge = net_edge_per_share(0.95, 0.05)
        assert edge == pytest.approx(0.05 - 0.0023750)

    def test_annualized(self):
        # 2c net on 96c over 30 days ~ 25.3%/yr
        y = annualized_yield(0.02, 0.96, 30)
        assert y == pytest.approx((0.02 / 0.96) * (365 / 30))

    def test_degenerate_inputs(self):
        assert annualized_yield(0.02, 0.0, 30) == 0.0
        assert annualized_yield(0.02, 0.96, 0) == 0.0
