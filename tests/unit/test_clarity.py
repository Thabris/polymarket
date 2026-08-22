"""Resolution-clarity grader tests, including real Gamma descriptions."""

import json
from pathlib import Path

from scanners.theta_clarity import grade

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class TestSubjectiveDetection:
    def test_credible_reporting_is_c(self):
        g, ev = grade(
            "This market will resolve YES if there is a consensus of credible reporting "
            "that the event occurred by December 31."
        )
        assert g == "C"
        assert ev["subjective"]

    def test_attempts_is_c(self):
        g, _ = grade("Resolves YES if the administration attempts to pass the bill.")
        assert g == "C"

    def test_empty_is_c(self):
        assert grade(None)[0] == "C"
        assert grade("")[0] == "C"


class TestObjectiveGrading:
    def test_full_mechanical_is_a(self):
        g, ev = grade(
            "This market resolves YES if the closing price of BTC on Binance is "
            "greater than $100,000 at 12:00 PM ET on December 31, 2026.",
            resolution_source="https://www.binance.com",
        )
        assert g == "A"
        assert ev["sources"] and ev["mechanical"] and ev["times"]

    def test_partial_is_b(self):
        g, _ = grade("Resolves YES if the team wins the final match of the tournament.")
        assert g == "B"  # mechanical ('wins') but no named source/time


class TestRealFixtureDescriptions:
    def test_live_market_descriptions_gradeable(self):
        """Every real description must grade without raising, into A/B/C."""
        path = FIXTURES / "gamma_markets_active.json"
        if not path.exists():
            return  # fixtures not captured in this checkout
        markets = json.loads(path.read_text(encoding="utf-8"))
        grades = []
        for m in markets:
            g, _ = grade(m.get("description"), m.get("resolutionSource"))
            assert g in ("A", "B", "C")
            grades.append(g)
        # sports markets with hltv.org resolution sources should not all be C
        assert any(g in ("A", "B") for g in grades)
