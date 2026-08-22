"""SQLAlchemy database models for the Polymarket scanner platform.

Datetime convention: all DateTime columns store naive UTC; convert at the
boundary with core.timeutil.to_db / from_db.
"""

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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


class MarketModel(Base):
    """Market information from Polymarket (Gamma)."""

    __tablename__ = "markets"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(100), index=True)
    question: Mapped[str] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    slug: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    accepting_orders: Mapped[bool] = mapped_column(Boolean, default=True)

    # Outcome tokens. Binary markets: index 0 / index 1 (labels in outcomes JSON;
    # usually Yes/No but sports markets use team names).
    token_id_yes: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    token_id_no: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    outcomes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array

    # Cached prices / top of book
    price_yes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_no: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    best_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    best_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spread: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_trade_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    one_day_price_change: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tick_size: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Volume / liquidity
    volume_24h: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    liquidity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Event / family linkage
    event_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    event_slug: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    group_item_title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    neg_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    neg_risk_market_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    # Fees (feeSchedule on the market is authoritative)
    fees_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    fee_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fee_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    # Resolution
    resolution_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    uma_resolution_status: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    resolved_outcome: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # "0"/"1" index
    closed_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array of labels
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

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


class EventModel(Base):
    """Polymarket event (grouping of markets)."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    slug: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    neg_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array of labels
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class PriceModel(Base):
    """Price history with OHLC data (1-minute bars, YES-token mid basis)."""

    __tablename__ = "prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("markets.id"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)

    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)

    market: Mapped["MarketModel"] = relationship(back_populates="prices")

    __table_args__ = (
        Index("ix_prices_market_timestamp", "market_id", "timestamp"),
    )


class AlertModel(Base):
    """Triggered alerts history (toast/audit log)."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[Optional[str]] = mapped_column(
        String(100), ForeignKey("markets.id"), nullable=True, index=True
    )
    alert_type: Mapped[str] = mapped_column(String(50), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="info")
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    market: Mapped[Optional["MarketModel"]] = relationship(back_populates="alerts")

    __table_args__ = (
        Index("ix_alerts_type_created", "alert_type", "created_at"),
    )


class SignalModel(Base):
    """A scanner-emitted trading signal."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dedup_key: Mapped[str] = mapped_column(String(300), unique=True)
    strategy: Mapped[str] = mapped_column(String(50), index=True)
    market_id: Mapped[Optional[str]] = mapped_column(
        String(100), ForeignKey("markets.id"), nullable=True, index=True
    )
    token_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    side: Mapped[str] = mapped_column(String(20))  # buy_yes / buy_no
    grade: Mapped[str] = mapped_column(String(5), default="B")  # A / B / C
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_zone_low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_zone_high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    size_hint: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # open -> filled | expired | cancelled ; filled -> resolved
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON string
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_signals_strategy_status_created", "strategy", "status", "created_at"),
    )


class PaperPositionModel(Base):
    """Hypothetical position opened from a signal by the PaperRouter."""

    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("signals.id"), unique=True
    )
    strategy: Mapped[str] = mapped_column(String(50), index=True)
    market_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    token_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    side: Mapped[str] = mapped_column(String(20))
    entry_price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)  # shares
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open/closed
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # resolution_win / resolution_loss / time_stop / target / manual
    exit_reason: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class RealFillModel(Base):
    """User-entered record of an actually-traded signal."""

    __tablename__ = "real_fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, ForeignKey("signals.id"), index=True)
    side: Mapped[str] = mapped_column(String(20))
    price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)
    fee_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MarketFamilyModel(Base):
    """A group of deadline-tranche markets on the same underlying event."""

    __tablename__ = "market_families"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    family_key: Mapped[str] = mapped_column(String(300), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # event / stem
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    members: Mapped[list["FamilyMemberModel"]] = relationship(
        back_populates="family", cascade="all, delete-orphan"
    )


class FamilyMemberModel(Base):
    """Membership of a market in a deadline family."""

    __tablename__ = "family_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    family_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("market_families.id"), index=True
    )
    market_id: Mapped[str] = mapped_column(String(100), ForeignKey("markets.id"))
    tranche_order: Mapped[int] = mapped_column(Integer, default=0)
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    group_item_title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    family: Mapped["MarketFamilyModel"] = relationship(back_populates="members")

    __table_args__ = (
        UniqueConstraint("family_id", "market_id", name="uq_family_market"),
    )


class ScannerRunModel(Base):
    """Bookkeeping row per scanner execution (powers the status page)."""

    __tablename__ = "scanner_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scanner: Mapped[str] = mapped_column(String(50), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ok: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    items_scanned: Mapped[int] = mapped_column(Integer, default=0)
    signals_emitted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class WatchlistModel(Base):
    """User's watchlist of markets to monitor."""

    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    price_threshold_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume_threshold_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AlertConfigModel(Base):
    """Alert configuration settings."""

    __tablename__ = "alert_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
