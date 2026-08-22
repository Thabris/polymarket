#!/usr/bin/env python3
"""Phase 0 probe: dump raw Gamma API payloads to tests/fixtures/.

Captures the exact field names and shapes we will parse against:
- active markets page (high volume)
- resolved/closed markets (how outcome is represented)
- events with nested markets (family grouping substrate)
- a negRisk event (multi-outcome / tranche structure)
- "by <date>" deadline-style markets (calendar families)

Read-only; no auth required.
"""

import asyncio
import json
import re
from pathlib import Path

import httpx

GAMMA = "https://gamma-api.polymarket.com"
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

DEADLINE_RE = re.compile(r"\b(by|before)\s+(january|february|march|april|may|june|july|august|september|october|november|december|end of|\d{4})", re.I)


def dump(name: str, payload) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    size = path.stat().st_size
    print(f"  wrote {name} ({size:,} bytes)")


async def main() -> None:
    async with httpx.AsyncClient(base_url=GAMMA, timeout=30.0) as client:
        print("[1/5] active markets (volume-ordered)...")
        r = await client.get("/markets", params={
            "limit": 10, "active": "true", "closed": "false",
            "order": "volume24hr", "ascending": "false",
        })
        r.raise_for_status()
        active = r.json()
        dump("gamma_markets_active.json", active)

        print("[2/5] closed/resolved markets...")
        r = await client.get("/markets", params={
            "limit": 10, "closed": "true", "order": "volume24hr", "ascending": "false",
        })
        r.raise_for_status()
        dump("gamma_markets_closed.json", r.json())

        print("[3/5] events with nested markets...")
        r = await client.get("/events", params={
            "limit": 5, "active": "true", "closed": "false",
            "order": "volume24hr", "ascending": "false",
        })
        r.raise_for_status()
        events = r.json()
        dump("gamma_events.json", events)

        print("[4/5] hunting a negRisk event...")
        neg_risk_event = None
        for offset in range(0, 200, 50):
            r = await client.get("/events", params={
                "limit": 50, "offset": offset, "active": "true", "closed": "false",
                "order": "volume24hr", "ascending": "false",
            })
            r.raise_for_status()
            for ev in r.json():
                if ev.get("negRisk") or ev.get("neg_risk"):
                    neg_risk_event = ev
                    break
            if neg_risk_event:
                break
        if neg_risk_event:
            dump("gamma_event_negrisk.json", neg_risk_event)
        else:
            print("  !! no negRisk event found in first 200 events")

        print('[5/5] hunting "by <date>" deadline-style markets...')
        deadline_markets = []
        for offset in range(0, 500, 100):
            r = await client.get("/markets", params={
                "limit": 100, "offset": offset, "active": "true", "closed": "false",
                "order": "volume24hr", "ascending": "false",
            })
            r.raise_for_status()
            page = r.json()
            if not page:
                break
            for m in page:
                q = m.get("question") or ""
                if DEADLINE_RE.search(q):
                    deadline_markets.append(m)
            if len(deadline_markets) >= 12:
                break
        dump("gamma_markets_deadline.json", deadline_markets[:12])

    # summarize field coverage over the active page for quick eyeballing
    keys = {}
    sample = active if isinstance(active, list) else active.get("data", [])
    for m in sample:
        for k, v in m.items():
            keys.setdefault(k, 0)
            if v is not None:
                keys[k] += 1
    print("\nActive-page field coverage (non-null count / page size {}):".format(len(sample)))
    for k in sorted(keys):
        print(f"  {k}: {keys[k]}")


if __name__ == "__main__":
    asyncio.run(main())
