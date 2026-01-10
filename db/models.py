"""SQLAlchemy database models for Polymarket Monitor."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


class MarketModel(Base):
    """Market/Event information from Polymarket."""

    __tablename__ = "markets"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(100), index=True)
    question: Mapped[str] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Token IDs for YES/NO outcomes
    token_id_yes: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    token_id_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Current prices (cached)
    price_yes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_no: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Volume
    volume_24h: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    liquidity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    prices: Mapped[list["PriceModel"]] = relationship(
        back_populates="market", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["AlertModel"]] = relationship(
        back_populates="market", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_markets_category", "category"),
        Index("ix_markets_active", "active"),
    )


class PriceModel(Base):
    """Price history with OHLC data (1-minute candles)."""

    __tablename__ = "prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("markets.id"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)

    # OHLC for YES token
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)

    # Volume
    volume: Mapped[float] = mapped_column(Float, default=0.0)

    # Relationship
    market: Mapped["MarketModel"] = relationship(back_populates="prices")

    __table_args__ = (
        Index("ix_prices_market_timestamp", "market_id", "timestamp"),
    )


class AlertModel(Base):
    """Triggered alerts history."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[Optional[str]] = mapped_column(
        String(100), ForeignKey("markets.id"), nullable=True, index=True
    )

    # Alert type: price_change, volume_spike, whale_trade, trending
    alert_type: Mapped[str] = mapped_column(String(50), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="info")  # info, warning, critical

    # Alert details
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)

    # Related data (JSON-serializable)
    data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string

    # Status
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    # Relationship
    market: Mapped[Optional["MarketModel"]] = relationship(back_populates="alerts")

    __table_args__ = (
        Index("ix_alerts_type_created", "alert_type", "created_at"),
    )


class WatchlistModel(Base):
    """User's watchlist of markets to monitor."""

    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)

    # Custom alert thresholds (override defaults)
    price_threshold_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume_threshold_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class AlertConfigModel(Base):
    """Alert configuration settings."""

    __tablename__ = "alert_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
