#!/usr/bin/env python3
"""Phase 0 probe: dump public CLOB REST payloads (order book, midpoint, price).

Pulls token ids from the gamma_markets_active.json fixture written by
probe_gamma.py (run that first). Read-only, no auth.
"""

import asyncio
import json
from pathlib import Path

import httpx

CLOB = "https://clob.polymarket.com"
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def extract_token_ids(markets: list, n: int = 3) -> list[str]:
    out = []
    for m in markets:
        raw = m.get("clobTokenIds")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if isinstance(raw, list) and raw:
            out.append(str(raw[0]))  # YES token
        if len(out) >= n:
            break
    return out


async def main() -> None:
    src = FIXTURES / "gamma_markets_active.json"
    if not src.exists():
        raise SystemExit("run probe_gamma.py first")
    markets = json.loads(src.read_text(encoding="utf-8"))
    if isinstance(markets, dict):
        markets = markets.get("data", [])
    token_ids = extract_token_ids(markets)
    print(f"probing {len(token_ids)} YES tokens: {[t[:16] + '...' for t in token_ids]}")

    results = {}
    async with httpx.AsyncClient(base_url=CLOB, timeout=30.0) as client:
        for tid in token_ids:
            entry = {}
            for name, path, params in [
                ("book", "/book", {"token_id": tid}),
                ("midpoint", "/midpoint", {"token_id": tid}),
                ("price_buy", "/price", {"token_id": tid, "side": "buy"}),
                ("price_sell", "/price", {"token_id": tid, "side": "sell"}),
            ]:
                try:
                    r = await client.get(path, params=params)
                    entry[name] = {"status": r.status_code, "body": r.json()}
                except Exception as e:  # noqa: BLE001 — probe records everything
                    entry[name] = {"error": repr(e)}
                await asyncio.sleep(0.2)
            results[tid] = entry

    FIXTURES.mkdir(parents=True, exist_ok=True)
    out = FIXTURES / "clob_books.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"wrote {out.name} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
