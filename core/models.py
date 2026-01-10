"""Pydantic data models for Polymarket Monitor."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AlertType(str, Enum):
    """Types of alerts."""

    PRICE_CHANGE = "price_change"
    VOLUME_SPIKE = "volume_spike"
    WHALE_TRADE = "whale_trade"
    TRENDING = "trending"


class Severity(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Market(BaseModel):
    """Market/Event information."""

    id: str
    condition_id: str
    question: str
    description: Optional[str] = None
    category: Optional[str] = None
    end_date: Optional[datetime] = None
    active: bool = True
    token_id_yes: Optional[str] = None
    token_id_no: Optional[str] = None
    price_yes: Optional[float] = None
    price_no: Optional[float] = None
    volume_24h: Optional[float] = None
    liquidity: Optional[float] = None


class Price(BaseModel):
    """Price data point (OHLC candle)."""

    market_id: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class Trade(BaseModel):
    """Individual trade information."""

    id: str
    market_id: str
    timestamp: datetime
    side: str  # "buy" or "sell"
    outcome: str  # "yes" or "no"
    price: float
    size: float  # In shares
    value_usd: float  # In USD
    maker: Optional[str] = None
    taker: Optional[str] = None


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
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WatchlistItem(BaseModel):
    """Watchlist entry."""

    id: Optional[int] = None
    market_id: str
    price_threshold_pct: Optional[float] = None
    volume_threshold_usd: Optional[float] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None


class AlertConfig(BaseModel):
    """Alert configuration."""

    price_change_threshold_pct: float = 5.0
    price_change_window_minutes: int = 5
    volume_spike_threshold_usd: float = 10000.0
    volume_spike_window_minutes: int = 5
    whale_trade_threshold_usd: float = 5000.0


class SystemStatus(BaseModel):
    """System health status."""

    running: bool = False
    websocket_connected: bool = False
    last_data_update: Optional[datetime] = None
    markets_tracked: int = 0
    alerts_24h: int = 0
    uptime_seconds: float = 0.0


class PriceUpdate(BaseModel):
    """Real-time price update from WebSocket."""

    market_id: str
    token_id: str
    price: float
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class OrderBookSummary(BaseModel):
    """Order book summary."""

    market_id: str
    token_id: str
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    spread: Optional[float] = None
    bid_depth: float = 0.0
    ask_depth: float = 0.0
