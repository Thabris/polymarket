#!/usr/bin/env python3
"""Inject a synthetic signal into the running daemon via the manual-signal API.

Usage:
    python scripts/emit_test_signal.py                 # top liquid market
    python scripts/emit_test_signal.py --market-id X --price 0.42
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"


def pick_market() -> tuple[str, str]:
    db_path = Path(__file__).resolve().parent.parent / "var" / "polymarket.db"
    c = sqlite3.connect(db_path)
    row = c.execute(
        "SELECT id, question FROM markets WHERE active=1 AND closed=0 AND best_ask IS NOT NULL "
        "ORDER BY liquidity DESC LIMIT 1"
    ).fetchone()
    if not row:
        sys.exit("no markets in DB — run the daemon once to sync")
    return row[0], row[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-id")
    ap.add_argument("--price", type=float)
    ap.add_argument("--side", default="buy_yes", choices=["buy_yes", "buy_no"])
    args = ap.parse_args()

    market_id = args.market_id
    question = "?"
    if not market_id:
        market_id, question = pick_market()

    body = {
        "market_id": market_id,
        "side": args.side,
        "note": "synthetic test signal (emit_test_signal.py)",
    }
    if args.price:
        body["entry_price"] = args.price

    r = httpx.post(f"{BASE}/api/signals/manual", json=body, timeout=15)
    print(f"POST /api/signals/manual -> {r.status_code}: {r.text}")
    if r.status_code == 200:
        print(f"market: {question[:80]}")
        print(f"check {BASE}/static/signals.html and /api/paper/positions")


if __name__ == "__main__":
    main()
