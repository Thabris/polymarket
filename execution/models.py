"""Execution value objects."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from core.timeutil import utcnow


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class Order(BaseModel):
    """An order a router would place (paper or live)."""

    market_id: str
    token_id: str
    side: str  # buy / sell
    size: float  # shares
    price: Optional[float] = None
    order_type: OrderType = OrderType.LIMIT
    created_at: datetime = Field(default_factory=utcnow)


class Position(BaseModel):
    """A held position (paper or live)."""

    market_id: str
    token_id: str
    side: str
    size: float
    entry_price: float
    fees_paid: float = 0.0
    opened_at: datetime = Field(default_factory=utcnow)
