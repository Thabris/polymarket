"""Resolution-clarity grading for theta candidates.

The real edge in theta farming is knowing WHICH near-certain markets are safe
— i.e., resolution-criteria due diligence. This grader is advisory (research:
every documented theta blowup was the resolution layer, not the event):

- A: named authoritative source AND a mechanical criterion AND an explicit
     time/timezone cutoff — the market resolves itself.
- B: objective but incomplete (source or mechanical criterion, not all three).
- C: subjective wording ("consensus of credible reporting"-class) — listed on
     the dashboard but never toasted or papered.

Matched evidence is returned so the snapshot records WHY a grade was given.
"""

from __future__ import annotations

import re

# Subjective phrasing that puts resolution in interpretive territory
_SUBJECTIVE_PATTERNS = [
    r"consensus of credible reporting",
    r"credible reporting",
    r"credible sources",
    r"widely reported",
    r"majority of (?:sources|reports)",
    r"at the discretion",
    r"in the (?:sole )?judgment",
    r"deemed",
    r"generally (?:accepted|understood|recognized)",
    r"common understanding",
    r"substantively",
    r"in spirit",
    r"attempts? to",
    r"seriously considers?",
    r"signals? (?:an? )?intent",
]

# Named authoritative sources
_SOURCE_PATTERNS = [
    r"https?://\S+",
    r"official(?:ly)?[\s-](?:announce|report|statement|website|data|source)",
    r"according to (?:the )?[A-Z][\w.]+",
    r"as reported by (?:the )?[A-Z][\w.]+",
    r"\b(?:AP|Reuters|Bloomberg|NOAA|NWS|BLS|BEA|CDC|FRED|SEC|FEC|Fed(?:eral Reserve)?|"
    r"CoinGecko|CoinMarketCap|Binance|Chainlink|NYSE|Nasdaq|ESPN|HLTV|UN|NATO|WHO)\b",
    r"resolution source (?:is|will be)",
]

# Mechanical criteria: numbers, thresholds, exact comparisons
_MECHANICAL_PATTERNS = [
    r"(?:greater|less) than(?: or equal to)?\s+[\d$.,%]+",
    r"(?:at least|at most|exceeds?|reach(?:es)?|above|below)\s+[\d$.,%]+",
    r"[\d.,]+\s*(?:%|bps|basis points|USD|\$|°F|°C|points?)",
    r"closing (?:price|value)",
    r"official (?:count|tally|total|result)",
    r"final score",
    r"wins?\b",
]

# Explicit cutoff time / timezone
_TIME_PATTERNS = [
    r"\b(?:ET|EST|EDT|PT|UTC|GMT|CET)\b",
    r"11:59",
    r"\bmidnight\b",
    r"\bnoon\b",
    r"\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)?",
]


def _matches(text: str, patterns: list[str]) -> list[str]:
    found = []
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            found.append(m.group(0)[:60])
    return found


def grade(description: str | None, resolution_source: str | None = None) -> tuple[str, dict]:
    """Grade resolution clarity. Returns (grade, evidence)."""
    text = (description or "").strip()
    if resolution_source:
        text = f"{text}\nresolution source is {resolution_source}"
    if not text:
        return "C", {"reason": "no description"}

    subjective = _matches(text, _SUBJECTIVE_PATTERNS)
    if subjective:
        return "C", {"subjective": subjective}

    sources = _matches(text, _SOURCE_PATTERNS)
    mechanical = _matches(text, _MECHANICAL_PATTERNS)
    times = _matches(text, _TIME_PATTERNS)

    evidence = {"sources": sources, "mechanical": mechanical, "times": times}
    if sources and mechanical and times:
        return "A", evidence
    if sources or mechanical:
        return "B", evidence
    return "C", {**evidence, "reason": "no objective anchor found"}
