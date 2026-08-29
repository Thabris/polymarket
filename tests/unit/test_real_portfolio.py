"""Real-portfolio mapping tests against the captured Data-API fixture."""

import json
from pathlib import Path

import pytest

from data.portfolio_client import valid_address
from execution.real_portfolio import positions_to_book
from execution.risk import RiskEngine

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class TestAddressValidation:
    def test_valid(self):
        assert valid_address("0x204f72f35326db932158cba6adff0b9a1da95e14")

    def test_invalid(self):
        assert not valid_address("")
        assert not valid_address("0x123")
        assert not valid_address("204f72f35326db932158cba6adff0b9a1da95e14")
        assert not valid_address("0x" + "g" * 40)


class TestPositionsToBook:
    def test_fixture_maps_cleanly(self):
        path = FIXTURES / "dataapi_positions.json"
        if not path.exists():
            pytest.skip("fixture not captured")
        raw = json.loads(path.read_text(encoding="utf-8"))
        book = positions_to_book(raw)
        assert len(book) == len(raw)
        for e in book:
            assert e["strategy"] == "real"
            assert 0.0 <= e["p_win"] <= 1.0
            assert e["notional"] > 0
            assert e["group"].startswith("ev:") or len(e["group"]) > 10
        # the book must be consumable by the SAME VaR engine as paper
        var = RiskEngine.mc_var(book, sims=1000, seed=5)
        assert var["worst_case"] > 0
        assert var["var"] >= 0

    def test_p_win_is_held_token_price_no_inversion(self):
        # The Data API reports the HELD asset's own curPrice — 0.9875 must
        # stay 0.9875 even though outcomeIndex is 1 (a "NO-side" token)
        raw = [{
            "size": 100, "avgPrice": 0.67, "curPrice": 0.9875,
            "initialValue": 67.0, "currentValue": 98.75, "cashPnl": 31.75,
            "eventId": "ev9", "conditionId": "0xc", "outcomeIndex": 1,
            "title": "T", "outcome": "Juventus", "eventSlug": "s",
            "entryFeesUsdc": 0.5, "redeemable": False,
        }]
        book = positions_to_book(raw)
        assert book[0]["p_win"] == pytest.approx(0.9875)
        assert book[0]["group"] == "ev:ev9"
        assert book[0]["fees"] == pytest.approx(0.5)

    def test_garbage_rows_skipped(self):
        raw = [
            {"size": 0, "avgPrice": 0.5},            # zero size
            {"size": "x", "avgPrice": 0.5},           # unparseable
            {"size": 10, "avgPrice": 0.5, "curPrice": 0.6, "conditionId": "0xa"},
        ]
        book = positions_to_book(raw)
        assert len(book) == 1
        assert book[0]["notional"] == pytest.approx(5.0)
