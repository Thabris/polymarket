"""Database operations and session management."""

import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator, Optional, Sequence

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import settings
from db.models import (
    Base,
    MarketModel,
    PriceModel,
    AlertModel,
    WatchlistModel,
    AlertConfigModel,
)


class Database:
    """Async database manager."""

    def __init__(self, url: Optional[str] = None):
        self.url = url or settings.database_url
        self.engine = create_async_engine(self.url, echo=False)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def init_db(self) -> None:
        """Create all tables (for development/testing)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Close database connections."""
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get a database session."""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # Market operations
    async def upsert_market(self, market_data: dict) -> MarketModel:
        """Insert or update a market."""
        async with self.session() as session:
            market_id = market_data.get("id")
            result = await session.execute(
                select(MarketModel).where(MarketModel.id == market_id)
            )
            market = result.scalar_one_or_none()

            if market:
                for key, value in market_data.items():
                    if hasattr(market, key):
                        setattr(market, key, value)
            else:
                market = MarketModel(**market_data)
                session.add(market)

            await session.flush()
            return market

    async def get_market(self, market_id: str) -> Optional[MarketModel]:
        """Get a market by ID."""
        async with self.session() as session:
            result = await session.execute(
                select(MarketModel).where(MarketModel.id == market_id)
            )
            return result.scalar_one_or_none()

    async def get_active_markets(self) -> Sequence[MarketModel]:
        """Get all active markets."""
        async with self.session() as session:
            result = await session.execute(
                select(MarketModel).where(MarketModel.active == True)
            )
            return result.scalars().all()

    # Price operations
    async def add_price(
        self,
        market_id: str,
        timestamp: datetime,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float = 0.0,
    ) -> PriceModel:
        """Add a price candle."""
        async with self.session() as session:
            price = PriceModel(
                market_id=market_id,
                timestamp=timestamp,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
            session.add(price)
            await session.flush()
            return price

    async def get_prices(
        self,
        market_id: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 1000,
    ) -> Sequence[PriceModel]:
        """Get price history for a market."""
        async with self.session() as session:
            query = select(PriceModel).where(PriceModel.market_id == market_id)

            if start:
                query = query.where(PriceModel.timestamp >= start)
            if end:
                query = query.where(PriceModel.timestamp <= end)

            query = query.order_by(desc(PriceModel.timestamp)).limit(limit)
            result = await session.execute(query)
            return result.scalars().all()

    async def get_latest_price(self, market_id: str) -> Optional[PriceModel]:
        """Get the most recent price for a market."""
        async with self.session() as session:
            result = await session.execute(
                select(PriceModel)
                .where(PriceModel.market_id == market_id)
                .order_by(desc(PriceModel.timestamp))
                .limit(1)
            )
            return result.scalar_one_or_none()

    # Alert operations
    async def add_alert(
        self,
        alert_type: str,
        title: str,
        message: str,
        market_id: Optional[str] = None,
        severity: str = "info",
        data: Optional[dict] = None,
    ) -> AlertModel:
        """Add a new alert."""
        async with self.session() as session:
            alert = AlertModel(
                market_id=market_id,
                alert_type=alert_type,
                severity=severity,
                title=title,
                message=message,
                data=json.dumps(data) if data else None,
            )
            session.add(alert)
            await session.flush()
            return alert

    async def get_alerts(
        self,
        alert_type: Optional[str] = None,
        market_id: Optional[str] = None,
        acknowledged: Optional[bool] = None,
        limit: int = 100,
    ) -> Sequence[AlertModel]:
        """Get alerts with optional filters."""
        async with self.session() as session:
            query = select(AlertModel)

            conditions = []
            if alert_type:
                conditions.append(AlertModel.alert_type == alert_type)
            if market_id:
                conditions.append(AlertModel.market_id == market_id)
            if acknowledged is not None:
                conditions.append(AlertModel.acknowledged == acknowledged)

            if conditions:
                query = query.where(and_(*conditions))

            query = query.order_by(desc(AlertModel.created_at)).limit(limit)
            result = await session.execute(query)
            return result.scalars().all()

    async def acknowledge_alert(self, alert_id: int) -> bool:
        """Mark an alert as acknowledged."""
        async with self.session() as session:
            result = await session.execute(
                select(AlertModel).where(AlertModel.id == alert_id)
            )
            alert = result.scalar_one_or_none()
            if alert:
                alert.acknowledged = True
                return True
            return False

    # Watchlist operations
    async def add_to_watchlist(
        self,
        market_id: str,
        price_threshold_pct: Optional[float] = None,
        volume_threshold_usd: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> WatchlistModel:
        """Add a market to the watchlist."""
        async with self.session() as session:
            item = WatchlistModel(
                market_id=market_id,
                price_threshold_pct=price_threshold_pct,
                volume_threshold_usd=volume_threshold_usd,
                notes=notes,
            )
            session.add(item)
            await session.flush()
            return item

    async def remove_from_watchlist(self, market_id: str) -> bool:
        """Remove a market from the watchlist."""
        async with self.session() as session:
            result = await session.execute(
                select(WatchlistModel).where(WatchlistModel.market_id == market_id)
            )
            item = result.scalar_one_or_none()
            if item:
                await session.delete(item)
                return True
            return False

    async def get_watchlist(self) -> Sequence[WatchlistModel]:
        """Get all watchlist items."""
        async with self.session() as session:
            result = await session.execute(select(WatchlistModel))
            return result.scalars().all()

    # Config operations
    async def get_config(self, key: str) -> Optional[str]:
        """Get a config value."""
        async with self.session() as session:
            result = await session.execute(
                select(AlertConfigModel).where(AlertConfigModel.key == key)
            )
            config = result.scalar_one_or_none()
            return config.value if config else None

    async def set_config(self, key: str, value: str) -> None:
        """Set a config value."""
        async with self.session() as session:
            result = await session.execute(
                select(AlertConfigModel).where(AlertConfigModel.key == key)
            )
            config = result.scalar_one_or_none()

            if config:
                config.value = value
            else:
                config = AlertConfigModel(key=key, value=value)
                session.add(config)


# Global database instance
db = Database()
