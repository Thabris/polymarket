"""Timezone-aware time helpers.

Convention: everything in application code is tz-aware UTC. The SQLite
boundary stores naive-UTC datetimes (SQLAlchemy DateTime without timezone),
so `to_db` / `from_db` convert at the edge. Gamma timestamps come in several
formats ("2026-08-22T20:10:00Z", "2026-08-22 12:57:13+00") — `parse_dt`
handles all of them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def utcnow() -> datetime:
    """Current time, tz-aware UTC."""
    return datetime.now(timezone.utc)


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse a Gamma/CLOB datetime string to tz-aware UTC.

    Handles ISO-8601 with 'Z', with offsets, and the space-separated
    "+00" suffix form seen on closedTime. Returns None on missing/bad input.
    """
    if not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # "2026-08-22 12:57:13+00" -> "+00:00"; also normalize the space separator
    if len(s) > 3 and (s[-3] == "+" or s[-3] == "-") and s[-2:].isdigit():
        s = s + ":00"
    s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_epoch_ms(value) -> Optional[datetime]:
    """Parse a millisecond epoch (int or str) to tz-aware UTC."""
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def to_db(dt: Optional[datetime]) -> Optional[datetime]:
    """Convert to the naive-UTC form stored in SQLite."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def from_db(dt: Optional[datetime]) -> Optional[datetime]:
    """Re-attach UTC to a naive datetime read from SQLite."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def days_until(dt: Optional[datetime]) -> Optional[float]:
    """Days from now until dt (tz-aware); negative if past, None if missing."""
    if dt is None:
        return None
    return (dt - utcnow()) / timedelta(days=1)
