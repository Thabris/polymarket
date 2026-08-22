#!/usr/bin/env python3
"""Phase 0 probe: capture raw CLOB market-channel WebSocket traffic.

Sends the documented handshake {"assets_ids": [...], "type": "market"} and
records every raw frame for --seconds. Also records whether the server sends
pings, whether frames are JSON objects or arrays, and which event_type values
appear. Optionally sends an app-level "PING" text frame every 10s (--ping) so
we can determine whether it is required to keep the socket alive.

Usage:
    python scripts/probe_ws.py --seconds 120
    python scripts/probe_ws.py --seconds 120 --no-ping   # test keepalive need
"""

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def load_token_ids(n: int = 20) -> list[str]:
    src = FIXTURES / "gamma_markets_active.json"
    if not src.exists():
        raise SystemExit("run probe_gamma.py first")
    markets = json.loads(src.read_text(encoding="utf-8"))
    if isinstance(markets, dict):
        markets = markets.get("data", [])
    out = []
    for m in markets:
        raw = m.get("clobTokenIds")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if isinstance(raw, list):
            out.extend(str(t) for t in raw)  # YES and NO — more traffic for the probe
        if len(out) >= n:
            break
    return out[:n]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--no-ping", action="store_true", help="do not send app-level PING frames")
    args = ap.parse_args()

    token_ids = load_token_ids()
    print(f"subscribing {len(token_ids)} assets for {args.seconds}s (app-ping={'off' if args.no_ping else 'on'})")

    frames: list[dict] = []
    shapes = Counter()
    event_types = Counter()
    t0 = time.monotonic()

    async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
        await ws.send(json.dumps({"assets_ids": token_ids, "type": "market"}))

        async def app_ping():
            while True:
                await asyncio.sleep(10)
                await ws.send("PING")

        ping_task = None if args.no_ping else asyncio.create_task(app_ping())
        try:
            while time.monotonic() - t0 < args.seconds:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                ts = round(time.monotonic() - t0, 3)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    shapes["non-json"] += 1
                    frames.append({"t": ts, "raw": raw[:500]})
                    continue
                if isinstance(parsed, list):
                    shapes["array"] += 1
                    for item in parsed:
                        if isinstance(item, dict):
                            event_types[item.get("event_type", "?")] += 1
                else:
                    shapes["object"] += 1
                    event_types[parsed.get("event_type", "?")] += 1
                # keep the first 3 full samples of each event_type, truncate the rest
                frames.append({"t": ts, "frame": parsed})
        finally:
            if ping_task:
                ping_task.cancel()

    # write a trimmed capture: full frames for first occurrences, counts for the rest
    seen: Counter = Counter()
    trimmed = []
    for f in frames:
        frame = f.get("frame")
        if frame is None:
            trimmed.append(f)
            continue
        items = frame if isinstance(frame, list) else [frame]
        et = items[0].get("event_type", "?") if items and isinstance(items[0], dict) else "?"
        seen[et] += 1
        if seen[et] <= 3:
            trimmed.append(f)

    FIXTURES.mkdir(parents=True, exist_ok=True)
    out = FIXTURES / "ws_capture.json"
    out.write_text(json.dumps({
        "url": WS_URL,
        "handshake": {"assets_ids": f"[{len(token_ids)} ids]", "type": "market"},
        "app_ping_sent": not args.no_ping,
        "duration_s": args.seconds,
        "frame_shapes": dict(shapes),
        "event_types": dict(event_types),
        "samples": trimmed,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {out.name} ({out.stat().st_size:,} bytes)")
    print(f"frame shapes: {dict(shapes)}")
    print(f"event types:  {dict(event_types)}")


if __name__ == "__main__":
    asyncio.run(main())
