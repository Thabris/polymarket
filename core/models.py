"""Pydantic domain models for the Polymarket scanner platform."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from core.timeutil import utcnow


class AlertType(str, Enum):
    """Types of alerts (toast/audit categories)."""

    SIGNAL = "signal"
    ARBITRAGE = "arbitrage"
    SYSTEM = "system"


class Severity(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Market(BaseModel):
    """Market information (parsed from Gamma)."""

    id: str
    condition_id: str
    question: str
    description: Optional[str] = None
    category: Optional[str] = None
    slug: Optional[str] = None
    end_date: Optional[datetime] = None
    start_date: Optional[datetime] = None
    active: bool = True
    closed: bool = False
    accepting_orders: bool = True
    token_id_yes: Optional[str] = None
    token_id_no: Optional[str] = None
    outcomes: Optional[list[str]] = None
    price_yes: Optional[float] = None
    price_no: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    spread: Optional[float] = None
    last_trade_price: Optional[float] = None
    one_day_price_change: Optional[float] = None
    tick_size: Optional[float] = None
    volume_24h: Optional[float] = None
    liquidity: Optional[float] = None
    event_id: Optional[str] = None
    event_slug: Optional[str] = None
    group_item_title: Optional[str] = None
    neg_risk: bool = False
    neg_risk_market_id: Optional[str] = None
    fees_enabled: bool = False
    fee_rate: Optional[float] = None
    fee_type: Optional[str] = None
    resolution_source: Optional[str] = None
    uma_resolution_status: Optional[str] = None
    resolved_outcome: Optional[str] = None
    closed_time: Optional[datetime] = None
    tags: Optional[list[str]] = None
    last_synced_at: Optional[datetime] = None

    @property
    def polymarket_url(self) -> str:
        """Deep link to the market's event page."""
        slug = self.event_slug or self.slug or ""
        return f"https://polymarket.com/event/{slug}"


class Price(BaseModel):
    """Price data point (OHLC bar)."""

    market_id: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class Alert(BaseModel):
    """Alert notification."""

    id: Optional[int] = None
    market_id: Optional[str] = None
    alert_type: AlertType
    severity: Severity = Severity.INFO
    title: str
    message: str
    data: Optional[dict] = None
    acknowledged: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class BookTop(BaseModel):
    """Normalized top-of-book update from the stream."""

    asset_id: str
    market_id: Optional[str] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None
    timestamp: datetime = Field(default_factory=utcnow)


class TradeTick(BaseModel):
    """Normalized last-trade update from the stream."""

    asset_id: str
    market_id: Optional[str] = None
    price: float
    size: float = 0.0
    side: Optional[str] = None
    timestamp: datetime = Field(default_factory=utcnow)


class SystemStatus(BaseModel):
    """System health status."""

    running: bool = False
    websocket_connected: bool = False
    last_data_update: Optional[datetime] = None
    markets_tracked: int = 0
    alerts_24h: int = 0
    uptime_seconds: float = 0.0
