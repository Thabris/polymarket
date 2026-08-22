"""Repair a paper book that accumulated positions the router now rejects.

The router's conflict rule (one position per market per strategy, never both
outcomes of a market) landed after positions had already been opened without it.
This module reconstructs what the fixed router would have done to the existing
book: replay the open positions in the order they were opened, keep the ones a
conflict-checking router would have accepted, and report the rest as voidable.

Keeping the EARLIEST leg is what preserves information — it is the position the
fixed router would have opened, so its eventual resolution is real strategy
performance. The later legs are the ones it would have skipped.
"""

from __future__ import annotations

from typing import Iterable, NamedTuple, Optional


class Conflict(NamedTuple):
    """A position that a conflict-checking router would have refused to open."""

    position_id: int
    market_id: str
    strategy: str
    side: str
    kept_position_id: int
    kind: str  # "same_strategy" | "opposing_outcome"

    @property
    def description(self) -> str:
        if self.kind == "opposing_outcome":
            return f"holds the outcome opposing position {self.kept_position_id}"
        return f"duplicates position {self.kept_position_id} in the same strategy"


class _Kept(NamedTuple):
    position_id: int
    strategy: str
    token_id: Optional[str]


def find_conflicts(positions: Iterable) -> list[Conflict]:
    """Replay open positions chronologically; return the ones to void.

    `positions` are ORM rows or any objects exposing id, market_id, strategy,
    token_id, side and opened_at. Order of the input does not matter — the
    replay sorts by opened_at, with id as the tie-break for the simultaneous
    fills that a single book update can produce.
    """
    ordered = sorted(
        positions,
        key=lambda p: (p.opened_at is None, p.opened_at, p.id),
    )
    kept_by_market: dict[str, list[_Kept]] = {}
    conflicts: list[Conflict] = []

    for pos in ordered:
        if not pos.market_id:
            continue
        kept = kept_by_market.setdefault(pos.market_id, [])
        conflict = _first_conflict(kept, pos)
        if conflict is None:
            kept.append(_Kept(pos.id, pos.strategy, pos.token_id))
            continue
        kept_id, kind = conflict
        conflicts.append(
            Conflict(
                position_id=pos.id,
                market_id=pos.market_id,
                strategy=pos.strategy,
                side=pos.side,
                kept_position_id=kept_id,
                kind=kind,
            )
        )
    return conflicts


def _first_conflict(kept: list[_Kept], pos) -> Optional[tuple[int, str]]:
    """Mirror of Database.conflicting_open_position, over the replay state."""
    for other in kept:
        if other.strategy == pos.strategy:
            return other.position_id, "same_strategy"
        if pos.token_id and other.token_id and other.token_id != pos.token_id:
            return other.position_id, "opposing_outcome"
    return None
