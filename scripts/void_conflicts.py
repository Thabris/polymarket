"""Void paper positions that the router's conflict rule would now reject.

Dry run (default) prints the plan and changes nothing:

    python -m scripts.void_conflicts

Apply it:

    python -m scripts.void_conflicts --apply

Safe to run against a live daemon: SQLite is in WAL mode and the router holds no
cached view of open positions, so it picks up the closures on its next query.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from data.storage import db
from execution.paper import PaperRouter
from execution.reconcile import find_conflicts


async def main(apply: bool) -> int:
    await db.init_db()
    positions = await db.get_paper_positions(status="open", limit=10000)
    by_id = {p.id: p for p in positions}
    conflicts = find_conflicts(positions)

    if not conflicts:
        print(f"{len(positions)} open positions, no conflicts. Nothing to do.")
        return 0

    notional = sum(
        by_id[c.position_id].entry_price * by_id[c.position_id].size for c in conflicts
    )
    fees = sum(by_id[c.position_id].fees_paid or 0.0 for c in conflicts)

    print(f"{len(positions)} open positions, {len(conflicts)} to void:\n")
    for c in sorted(conflicts, key=lambda x: (x.market_id, x.position_id)):
        pos = by_id[c.position_id]
        print(
            f"  #{c.position_id:<4} {c.strategy:<12} {c.side:<8} "
            f"market {c.market_id:<10} @ {pos.entry_price:.3f} "
            f"(${pos.entry_price * pos.size:.0f}) - {c.description}"
        )
    print(f"\n  notional released: ${notional:,.0f}")
    print(f"  fees booked as loss: ${fees:,.2f}")

    if not apply:
        print("\nDry run. Re-run with --apply to void these.")
        return 0

    router = PaperRouter(db)
    voided = 0
    for c in conflicts:
        if await router.void_position(c.position_id):
            voided += 1
    print(f"\nVoided {voided} positions.")

    remaining = find_conflicts(await db.get_paper_positions(status="open", limit=10000))
    if remaining:
        print(f"WARNING: {len(remaining)} conflicts remain", file=sys.stderr)
        return 1
    print("Book is clean: no conflicting open positions remain.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the voids")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.apply)))
