"""Signal domain models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from core.timeutil import utcnow


class SignalStatus(str, Enum):
    """Signal lifecycle: open -> filled | expired | cancelled; filled -> resolved."""

    OPEN = "open"
    FILLED = "filled"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    RESOLVED = "resolved"


class SignalGrade(str, Enum):
    """Quality grade. C signals are listed but never toasted or papered."""

    A = "A"
    B = "B"
    C = "C"


class SignalSide(str, Enum):
    """Which token the signal proposes buying."""

    BUY_YES = "buy_yes"
    BUY_NO = "buy_no"


class Signal(BaseModel):
    """A scanner-emitted trading signal.

    `entry_price` is the immediate executable price (theta/calendar);
    `entry_zone_*` describe a resting-order zone (news fade) — the paper
    router only fills those if price actually touches the zone in time.
    `snapshot` carries the full strategy-specific context at emit time.
    """

    id: Optional[int] = None
    strategy: str
    market_id: Optional[str] = None
    token_id: Optional[str] = None
    side: SignalSide = SignalSide.BUY_YES
    grade: SignalGrade = SignalGrade.B
    score: Optional[float] = None
    entry_price: Optional[float] = None
    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None
    size_hint: Optional[float] = None
    status: SignalStatus = SignalStatus.OPEN
    expires_at: Optional[datetime] = None
    dedup_key: str
    snapshot: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def has_zone(self) -> bool:
        return self.entry_zone_low is not None and self.entry_zone_high is not None

    def public_dict(self) -> dict:
        """JSON-safe dict for the browser WS / API."""
        d = self.model_dump()
        d["created_at"] = self.created_at.isoformat()
        d["expires_at"] = self.expires_at.isoformat() if self.expires_at else None
        return d
