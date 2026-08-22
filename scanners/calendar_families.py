"""Deadline-family discovery and resolution-text comparison.

A calendar family = markets on the same underlying event with nested
CUMULATIVE deadlines ("by August 31" / "by September 30"). Only
by/before/until/through tails are cumulative; "in September" markets
(Fed decision windows, monthly touch markets) are NOT nested and must never
be monotonicity-checked — the constraint simply doesn't apply to windows.

Verified live (Aug 2026): deadline tranches usually live in SEPARATE Gamma
events (e.g. strait-of-hormuz-...-by-august-31 vs -by-september-30), so
question-stem normalization is the primary grouping key.

The MicroStrategy May-31 precedent: tranches of the "same" event can resolve
inconsistently on wording details — description comparison gates every pair.
"""

from __future__ import annotations

import re
from typing import Optional

from core.models import Market

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
)

# Cumulative deadline tail: "by August 31", "before 2027", "until March 1, 2027",
# "by end of Q3", "by August 22, 2026", optionally with trailing "?" / ","
_CUMULATIVE_TAIL = re.compile(
    rf"[,\s]+\b(by|before|until|through)\s+(?:the\s+)?(?:end\s+of\s+)?"
    rf"((?:{_MONTHS})(?:\s+\d{{1,2}})?(?:,?\s*\d{{4}})?|\d{{4}}|q[1-4](?:\s*\d{{4}})?)"
    rf"\s*\??\s*$",
    re.IGNORECASE,
)

_PUNCT = re.compile(r"[^\w\s$%.]")
_WS = re.compile(r"\s+")

# Tokens ignored when comparing resolution descriptions (dates differ by design)
_DATE_TOKENS = re.compile(
    rf"\b(?:{_MONTHS}|\d{{1,2}}(?:st|nd|rd|th)?|\d{{4}}|q[1-4])\b", re.IGNORECASE
)


def parse_deadline_stem(question: str) -> Optional[tuple[str, str, str]]:
    """(normalized_stem, deadline_phrase, direction) for deadline questions.

    direction:
    - "up":   "X happens BY/BEFORE T" — P increases with T (cumulative).
    - "down": "X continues THROUGH/UNTIL T" — survival semantics, P DECREASES
      with T. The monotonicity constraint is mirrored, never absent.

    Returns None for non-deadline questions and for window markets
    ("in September") where no ordering constraint applies.
    """
    if not question:
        return None
    m = _CUMULATIVE_TAIL.search(question)
    if not m:
        return None
    marker = m.group(1).lower()
    direction = "up" if marker in ("by", "before") else "down"
    stem = question[: m.start()]
    stem = _PUNCT.sub(" ", stem.lower())
    stem = _WS.sub(" ", stem).strip()
    if len(stem) < 8:  # too short to be a meaningful stem
        return None
    return stem, m.group(0).strip(" ,?"), direction


def discover_families(markets: list[Market]) -> dict[tuple[str, str], list[Market]]:
    """Group markets into deadline families keyed by (stem, direction).

    Only families with >=2 members holding DISTINCT deadlines qualify —
    negRisk outcome buckets share one deadline and are excluded naturally.
    """
    groups: dict[tuple[str, str], list[Market]] = {}
    for market in markets:
        if not market.end_date:
            continue
        parsed = parse_deadline_stem(market.question)
        if parsed is None:
            continue
        stem, _phrase, direction = parsed
        groups.setdefault((stem, direction), []).append(market)

    families = {}
    for key, members in groups.items():
        # date-granularity: tranches minutes apart are outcome buckets, not deadlines
        distinct_deadlines = {m.end_date.date() for m in members}
        if len(members) >= 2 and len(distinct_deadlines) >= 2:
            families[key] = sorted(members, key=lambda m: m.end_date)
    return families


def _resolution_tokens(description: Optional[str]) -> set[str]:
    text = (description or "").lower()
    text = _DATE_TOKENS.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return {t for t in _WS.split(text) if len(t) > 2}


def description_similarity(near: Market, far: Market) -> tuple[str, dict]:
    """Grade resolution-text fungibility between two tranches.

    Returns (grade, details): grade in {"high", "medium", "MISMATCH"}.
    Different named resolution sources are an automatic MISMATCH — that is
    the exact failure mode that splits tranche resolutions.
    """
    details: dict = {}
    src_near = (near.resolution_source or "").strip().lower()
    src_far = (far.resolution_source or "").strip().lower()
    if src_near and src_far and src_near != src_far:
        details["resolution_sources"] = [src_near[:80], src_far[:80]]
        return "MISMATCH", details

    tokens_near = _resolution_tokens(near.description)
    tokens_far = _resolution_tokens(far.description)
    if not tokens_near or not tokens_far:
        details["reason"] = "missing description"
        return "medium", details

    union = tokens_near | tokens_far
    jaccard = len(tokens_near & tokens_far) / len(union) if union else 0.0
    details["jaccard"] = round(jaccard, 3)
    only_near = list(tokens_near - tokens_far)[:8]
    only_far = list(tokens_far - tokens_near)[:8]
    if only_near or only_far:
        details["diff"] = {"near_only": only_near, "far_only": only_far}

    if jaccard >= 0.85:
        return "high", details
    if jaccard >= 0.55:
        return "medium", details
    return "MISMATCH", details
