"""Monotonicity-violation math tests (verified payoff algebra)."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from core.models import Market
from core.timeutil import utcnow
from scanners.calendar import CalendarScanner
from signals.models import SignalGrade


def _market(question, end_days, bid, ask, **kwargs) -> Market:
    base = {
        "id": kwargs.pop("id", question[:24]),
        "condition_id": "0x1",
        "question": question,
        "end_date": utcnow() + timedelta(days=end_days),
        "best_bid": bid,
        "best_ask": ask,
        "token_id_yes": "yes-" + question[:8],
        "token_id_no": "no-" + question[:8],
        "fees_enabled": True,
        "fee_rate": 0.04,
        "category": "politics",
        "tick_size": 0.01,
        "description": "Resolves YES per the official government source at 11:59 PM ET.",
    }
    base.update(kwargs)
    return Market(**base)


def _scanner() -> CalendarScanner:
    return CalendarScanner(universe=MagicMock(), signal_service=MagicMock(), database=MagicMock())


class TestPairCheck:
    def test_documented_violation_signals(self):
        # The verified example: near bid 52c > far ask 47c
        near = _market("X before June?", 9, bid=0.52, ask=0.53, id="near")
        far = _market("X before July?", 39, bid=0.46, ask=0.47, id="far")
        signal = _scanner()._check_pair(near, far)
        assert signal is not None
        snap = signal.snapshot
        # gross 5c; politics fees ~1c/leg on 0.47 and 0.48; 1c tick buffer
        assert snap["gross_edge"] == pytest.approx(0.05)
        fee_far = 0.04 * 0.47 * 0.53
        fee_near = 0.04 * 0.48 * 0.52
        # snapshot values are rounded to 4 decimals
        assert snap["fees"] == pytest.approx(fee_far + fee_near, abs=1e-4)
        assert snap["edge"] == pytest.approx(0.05 - (fee_far + fee_near) - 0.01, abs=1e-4)
        assert signal.grade == SignalGrade.A  # identical descriptions
        assert snap["legs"].startswith("buy near-NO + buy far-YES")
        assert signal.entry_price == pytest.approx(0.47)  # far-YES executable ask

    def test_coherent_pair_no_signal(self):
        near = _market("X before June?", 9, bid=0.10, ask=0.11, id="near")
        far = _market("X before July?", 39, bid=0.24, ask=0.25, id="far")
        assert _scanner()._check_pair(near, far) is None

    def test_sub_fee_violation_filtered(self):
        # 1.5c gross inversion dies to fees + tick buffer
        near = _market("X before June?", 9, bid=0.485, ask=0.50, id="near")
        far = _market("X before July?", 39, bid=0.46, ask=0.47, id="far")
        assert _scanner()._check_pair(near, far) is None

    def test_mismatch_description_grades_c(self):
        near = _market("X before June?", 9, bid=0.52, ask=0.53, id="near",
                       description="Resolves YES if the SEC filing shows the sale by the deadline.",
                       resolution_source="https://sec.gov")
        far = _market("X before July?", 39, bid=0.40, ask=0.41, id="far",
                      description="Resolves by consensus of credible media reporting on the event.",
                      resolution_source="https://apnews.com")
        signal = _scanner()._check_pair(near, far)
        assert signal is not None
        assert signal.grade == SignalGrade.C  # split-risk pair: never papered

    def test_fee_free_geopolitics_keeps_more_edge(self):
        near = _market("X before June?", 9, bid=0.52, ask=0.53, id="near",
                       fees_enabled=False, fee_rate=0.0, category="geopolitics")
        far = _market("X before July?", 39, bid=0.46, ask=0.47, id="far",
                      fees_enabled=False, fee_rate=0.0, category="geopolitics")
        signal = _scanner()._check_pair(near, far)
        assert signal is not None
        assert signal.snapshot["fees"] == 0.0
        assert signal.snapshot["edge"] == pytest.approx(0.05 - 0.01, abs=1e-9)


class TestSurvivalPairs:
    def test_survival_violation_mirrored(self):
        # survival family: coherent means p_near >= p_far; violation is
        # far_bid > near_ask -> buy near-YES + buy far-NO
        near = _market("Ceasefire continues through August 25?", 3, bid=0.40, ask=0.42, id="near")
        far = _market("Ceasefire continues through September 15?", 24, bid=0.50, ask=0.52, id="far")
        signal = _scanner()._check_pair(near, far, direction="down")
        assert signal is not None
        assert signal.side.value == "buy_no"
        assert signal.snapshot["direction"] == "down"
        assert signal.snapshot["gross_edge"] == pytest.approx(0.50 - 0.42)
        assert signal.snapshot["legs"].startswith("buy near-YES + buy far-NO")
        # entry_price must price the PAPERED token (far-NO = 1 - far bid),
        # never the other leg — the pre-review bug priced near-YES here
        assert signal.entry_price == pytest.approx(1.0 - 0.50)

    def test_survival_coherent_no_signal(self):
        near = _market("Ceasefire continues through August 25?", 3, bid=0.60, ask=0.62, id="near")
        far = _market("Ceasefire continues through September 15?", 24, bid=0.45, ask=0.47, id="far")
        assert _scanner()._check_pair(near, far, direction="down") is None
