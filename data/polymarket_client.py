"""Wrapper around py-clob-client for Polymarket API interactions."""

import logging
from typing import Optional

from config.credentials import get_credential
from config.settings import settings
from core.exceptions import AuthenticationError, ConnectionError

logger = logging.getLogger(__name__)


class PolymarketClient:
    """
    Client for interacting with Polymarket CLOB API.

    Wraps the official py-clob-client library with error handling
    and credential management.
    """

    def __init__(self):
        self._client = None
        self._authenticated = False

    async def connect(self) -> None:
        """Initialize connection to Polymarket API."""
        try:
            from py_clob_client.client import ClobClient

            api_key = get_credential("api_key")
            api_secret = get_credential("api_secret")
            private_key = get_credential("private_key")

            if not api_key or not api_secret:
                logger.warning("API credentials not configured - running in read-only mode")
                # Initialize without authentication for public endpoints
                self._client = ClobClient(
                    host=settings.polymarket_clob_url,
                )
                self._authenticated = False
            else:
                # Initialize with authentication
                self._client = ClobClient(
                    host=settings.polymarket_clob_url,
                    key=api_key,
                    secret=api_secret,
                    funder=private_key if private_key else None,
                )
                self._authenticated = True
                logger.info("Polymarket client initialized with authentication")

        except ImportError:
            raise ConnectionError(
                "py-clob-client not installed. Run: pip install py-clob-client"
            )
        except Exception as e:
            raise ConnectionError(f"Failed to initialize Polymarket client: {e}")

    @property
    def is_authenticated(self) -> bool:
        """Check if client is authenticated for trading."""
        return self._authenticated

    @property
    def client(self):
        """Get the underlying py-clob-client instance."""
        if self._client is None:
            raise ConnectionError("Client not connected. Call connect() first.")
        return self._client

    async def get_midpoint(self, token_id: str) -> Optional[float]:
        """
        Get the midpoint price for a token.

        Args:
            token_id: The token ID to get price for

        Returns:
            Midpoint price or None if not available
        """
        try:
            result = self.client.get_midpoint(token_id)
            return float(result) if result else None
        except Exception as e:
            logger.error(f"Failed to get midpoint for {token_id}: {e}")
            return None

    async def get_price(self, token_id: str, side: str = "buy") -> Optional[float]:
        """
        Get the best price for a token.

        Args:
            token_id: The token ID
            side: "buy" or "sell"

        Returns:
            Best price or None if not available
        """
        try:
            result = self.client.get_price(token_id, side)
            return float(result) if result else None
        except Exception as e:
            logger.error(f"Failed to get price for {token_id}: {e}")
            return None

    async def get_order_book(self, token_id: str) -> Optional[dict]:
        """
        Get the order book for a token.

        Args:
            token_id: The token ID

        Returns:
            Order book data or None if not available
        """
        try:
            return self.client.get_order_book(token_id)
        except Exception as e:
            logger.error(f"Failed to get order book for {token_id}: {e}")
            return None

    async def get_prices_history(
        self,
        token_id: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        interval: str = "1m",
    ) -> Optional[list]:
        """
        Get historical prices for a token.

        Args:
            token_id: The token ID
            start_ts: Start timestamp (Unix seconds)
            end_ts: End timestamp (Unix seconds)
            interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)

        Returns:
            List of price candles or None if not available
        """
        try:
            return self.client.get_prices_history(
                token_id,
                start_ts=start_ts,
                end_ts=end_ts,
                interval=interval,
            )
        except Exception as e:
            logger.error(f"Failed to get price history for {token_id}: {e}")
            return None

    # Trading methods (Phase 2)
    async def create_order(
        self,
        token_id: str,
        side: str,
        size: float,
        price: Optional[float] = None,
    ) -> Optional[dict]:
        """
        Create an order (requires authentication).

        Args:
            token_id: The token ID to trade
            side: "buy" or "sell"
            size: Number of shares
            price: Limit price (None for market order)

        Returns:
            Order result or None if failed
        """
        if not self._authenticated:
            raise AuthenticationError("Trading requires authentication")

        try:
            if price is None:
                # Market order
                return self.client.create_market_order(
                    token_id=token_id,
                    side=side,
                    amount=size,
                )
            else:
                # Limit order
                return self.client.create_order(
                    token_id=token_id,
                    side=side,
                    size=size,
                    price=price,
                )
        except Exception as e:
            logger.error(f"Failed to create order: {e}")
            raise

    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order.

        Args:
            order_id: The order ID to cancel

        Returns:
            True if cancelled successfully
        """
        if not self._authenticated:
            raise AuthenticationError("Trading requires authentication")

        try:
            self.client.cancel(order_id)
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def get_orders(self, market_id: Optional[str] = None) -> list:
        """
        Get active orders.

        Args:
            market_id: Optional market ID to filter

        Returns:
            List of active orders
        """
        if not self._authenticated:
            raise AuthenticationError("Viewing orders requires authentication")

        try:
            return self.client.get_orders(market=market_id)
        except Exception as e:
            logger.error(f"Failed to get orders: {e}")
            return []

    async def close(self) -> None:
        """Close the client connection."""
        self._client = None
        self._authenticated = False


# Global client instance
polymarket_client = PolymarketClient()
