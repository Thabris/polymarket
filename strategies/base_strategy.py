"""Base strategy class for trading strategies (Phase 2)."""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class StrategyState(str, Enum):
    """Strategy execution state."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class Position(BaseModel):
    """Trading position."""

    market_id: str
    token_id: str
    side: str  # "yes" or "no"
    size: float  # Number of shares
    entry_price: float
    current_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    opened_at: datetime


class Order(BaseModel):
    """Order request."""

    market_id: str
    token_id: str
    side: str  # "buy" or "sell"
    size: float
    price: Optional[float] = None  # None for market order
    order_type: str = "limit"  # "limit" or "market"


class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.

    Implement this class to create custom trading strategies.
    Strategies receive market data and can generate orders.
    """

    def __init__(self, name: str, paper_trading: bool = True):
        self.name = name
        self.paper_trading = paper_trading
        self.state = StrategyState.IDLE
        self.positions: dict[str, Position] = {}
        self._start_time: Optional[datetime] = None

    @abstractmethod
    async def on_price_update(
        self,
        market_id: str,
        price: float,
        timestamp: datetime,
    ) -> Optional[Order]:
        """
        Called on each price update.

        Args:
            market_id: Market identifier
            price: Current price
            timestamp: Update timestamp

        Returns:
            Order if strategy wants to trade, None otherwise
        """
        pass

    @abstractmethod
    async def on_trade(
        self,
        market_id: str,
        side: str,
        price: float,
        size: float,
        value_usd: float,
        timestamp: datetime,
    ) -> Optional[Order]:
        """
        Called on each trade event.

        Args:
            market_id: Market identifier
            side: Trade side (buy/sell)
            price: Trade price
            size: Trade size
            value_usd: Trade value in USD
            timestamp: Trade timestamp

        Returns:
            Order if strategy wants to trade, None otherwise
        """
        pass

    async def start(self) -> None:
        """Start the strategy."""
        self.state = StrategyState.RUNNING
        self._start_time = datetime.utcnow()
        logger.info(f"Strategy '{self.name}' started (paper={self.paper_trading})")

    async def stop(self) -> None:
        """Stop the strategy."""
        self.state = StrategyState.IDLE
        logger.info(f"Strategy '{self.name}' stopped")

    async def pause(self) -> None:
        """Pause the strategy."""
        self.state = StrategyState.PAUSED
        logger.info(f"Strategy '{self.name}' paused")

    async def resume(self) -> None:
        """Resume the strategy."""
        self.state = StrategyState.RUNNING
        logger.info(f"Strategy '{self.name}' resumed")

    def get_position(self, market_id: str) -> Optional[Position]:
        """Get current position for a market."""
        return self.positions.get(market_id)

    def update_position(self, position: Position) -> None:
        """Update a position."""
        self.positions[position.market_id] = position

    def close_position(self, market_id: str) -> Optional[Position]:
        """Close and remove a position."""
        return self.positions.pop(market_id, None)

    @property
    def is_running(self) -> bool:
        """Check if strategy is running."""
        return self.state == StrategyState.RUNNING
