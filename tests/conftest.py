"""Pytest configuration and fixtures."""

import asyncio
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from db.models import Base


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create an in-memory database session for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def sample_market_data() -> dict:
    """Sample market data for testing."""
    return {
        "id": "test-market-123",
        "conditionId": "test-condition-123",
        "question": "Will Bitcoin reach $100k?",
        "description": "Test market description",
        "category": "Crypto",
        "active": True,
        "outcomes": [
            {"id": "token-yes", "name": "Yes", "price": 0.65},
            {"id": "token-no", "name": "No", "price": 0.35},
        ],
        "volume24hr": 50000.0,
        "liquidity": 100000.0,
    }


@pytest.fixture
def sample_trade_data() -> dict:
    """Sample trade data for testing."""
    return {
        "id": "trade-123",
        "market_id": "test-market-123",
        "side": "buy",
        "outcome": "yes",
        "price": 0.65,
        "size": 1000.0,
        "value_usd": 650.0,
    }
