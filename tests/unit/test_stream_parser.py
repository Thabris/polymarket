"""WS frame parser tests against the recorded live capture."""

import json

import pytest
from pathlib import Path

from data.stream import parse_frame

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class TestParseFrame:
    def test_pong_ignored(self):
        assert parse_frame("PONG") == []
        assert parse_frame("") == []
        assert parse_frame("not json {") == []

    def test_book_object(self):
        frame = json.dumps({
            "event_type": "book",
            "asset_id": "123",
            "market": "0xabc",
            "bids": [{"price": "0.001", "size": "5"}, {"price": "0.45", "size": "100"}],
            "asks": [{"price": "0.99", "size": "10"}, {"price": "0.46", "size": "50"}],
            "timestamp": 1787412105078,
        })
        out = parse_frame(frame)
        assert len(out) == 1
        top = out[0]
        assert top["kind"] == "book_top"
        # levels are NOT best-first: best bid = max, best ask = min
        assert top["bid"] == 0.45
        assert top["ask"] == 0.46
        assert top["mid"] == 0.455
        assert top["bid_size"] == 100
        assert top["ask_size"] == 50

    def test_price_change_carries_top_of_book(self):
        frame = json.dumps({
            "event_type": "price_change",
            "market": "0xabc",
            "timestamp": "1787412134300",
            "price_changes": [
                {"asset_id": "a1", "price": "0.08", "size": "8020", "side": "BUY",
                 "best_bid": "0.33", "best_ask": "0.35"},
                {"asset_id": "a2", "price": "0.92", "size": "8020", "side": "SELL",
                 "best_bid": "0.65", "best_ask": "0.67"},
            ],
        })
        out = parse_frame(frame)
        assert len(out) == 2
        assert out[0]["asset_id"] == "a1"
        assert out[0]["mid"] == pytest.approx(0.34)
        assert out[1]["bid"] == 0.65

    def test_last_trade_price(self):
        frame = json.dumps({
            "event_type": "last_trade_price",
            "asset_id": "a1",
            "market": "0xabc",
            "price": "0.45",
            "size": "539.99",
            "side": "BUY",
            "timestamp": "1787412137210",
        })
        out = parse_frame(frame)
        assert len(out) == 1
        assert out[0]["kind"] == "trade_tick"
        assert out[0]["price"] == 0.45
        assert out[0]["size"] == 539.99

    def test_array_batched_frames(self):
        frame = json.dumps([
            {"event_type": "last_trade_price", "asset_id": "a1", "price": "0.5",
             "timestamp": "1787412137210"},
            {"event_type": "tick_size_change", "asset_id": "a1"},
        ])
        out = parse_frame(frame)
        assert len(out) == 1  # tick_size_change ignored

    def test_recorded_capture_parses(self):
        """Every frame from the live capture must parse without raising."""
        path = FIXTURES / "ws_capture.json"
        if not path.exists():
            return
        capture = json.loads(path.read_text(encoding="utf-8"))
        parsed_count = 0
        for sample in capture.get("samples", []):
            if "raw" in sample:
                assert parse_frame(sample["raw"]) == []
                continue
            frame = sample.get("frame")
            out = parse_frame(json.dumps(frame))
            parsed_count += len(out)
        assert parsed_count > 0
