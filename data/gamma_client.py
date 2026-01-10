"""Client for Polymarket Gamma API (market metadata)."""

import json
import logging
from datetime import datetime
from typing import Optional

import httpx

from config.settings import settings
from core.models import Market

logger = logging.getLogger(__name__)


class GammaClient:
    """
    Client for Polymarket Gamma API.

    Provides market metadata, search, and discovery features.
    """

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.polymarket_gamma_url
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        """Initialize the HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "Accept": "application/json",
                "User-Agent": "PolymarketMonitor/1.0",
            },
            follow_redirects=True,
        )

        # Test connection
        try:
            response = await self._client.get("/markets", params={"limit": 1})
            response.raise_for_status()
            logger.info(f"Gamma API connection verified: {self.base_url}")
        except Exception as e:
            logger.warning(f"Gamma API connection test failed: {e}")

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get the HTTP client, initializing if needed."""
        if self._client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self._client

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
        order: str = "volume24hr",
        ascending: bool = False,
    ) -> list[dict]:
        """
        Get a list of markets.

        Args:
            limit: Maximum number of markets to return
            offset: Pagination offset
            active: Include active markets
            closed: Include closed markets
            order: Sort field (volume24hr, liquidity, startDate, endDate)
            ascending: Sort direction

        Returns:
            List of market data dictionaries
        """
        try:
            params = {
                "limit": limit,
                "offset": offset,
                "active": str(active).lower(),
                "closed": str(closed).lower(),
                "order": order,
                "ascending": str(ascending).lower(),
            }

            response = await self.client.get("/markets", params=params)
            response.raise_for_status()
            data = response.json()

            # Handle different response formats
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                # API might wrap results in a data/markets key
                return data.get("data", data.get("markets", data.get("results", [])))
            else:
                logger.warning(f"Unexpected response type: {type(data)}")
                return []

        except Exception as e:
            logger.error(f"Failed to get markets: {e}")
            return []

    async def get_market(self, market_id: str) -> Optional[dict]:
        """
        Get a single market by ID.

        Args:
            market_id: The market/condition ID

        Returns:
            Market data or None if not found
        """
        try:
            response = await self.client.get(f"/markets/{market_id}")
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            logger.error(f"Failed to get market {market_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get market {market_id}: {e}")
            return None

    async def search_markets(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict]:
        """
        Search markets by text query.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of matching markets
        """
        try:
            params = {"query": query, "limit": limit}
            response = await self.client.get("/markets/search", params=params)
            response.raise_for_status()
            return response.json()

        except Exception as e:
            logger.error(f"Failed to search markets: {e}")
            return []

    async def get_events(self, limit: int = 50) -> list[dict]:
        """
        Get events (groups of related markets).

        Args:
            limit: Maximum events to return

        Returns:
            List of events
        """
        try:
            params = {"limit": limit}
            response = await self.client.get("/events", params=params)
            response.raise_for_status()
            return response.json()

        except Exception as e:
            logger.error(f"Failed to get events: {e}")
            return []

    async def get_trending(self, limit: int = 20) -> list[dict]:
        """
        Get trending/popular markets.

        Args:
            limit: Maximum markets to return

        Returns:
            List of trending markets
        """
        # Trending = high volume in recent period
        return await self.get_markets(
            limit=limit,
            order="volume24hr",
            ascending=False,
        )

    def parse_market(self, data: dict) -> Market:
        """
        Parse API response into Market model.

        Args:
            data: Raw API response

        Returns:
            Market model instance
        """
        # Parse end date
        end_date = None
        if data.get("endDate"):
            try:
                end_date = datetime.fromisoformat(
                    data["endDate"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        # Get token IDs from clobTokenIds (JSON string or list)
        clob_tokens = data.get("clobTokenIds", [])
        if isinstance(clob_tokens, str):
            try:
                clob_tokens = json.loads(clob_tokens)
            except (json.JSONDecodeError, TypeError):
                clob_tokens = []

        token_yes = clob_tokens[0] if len(clob_tokens) > 0 else None
        token_no = clob_tokens[1] if len(clob_tokens) > 1 else None

        # Get prices from outcomePrices (JSON string or list)
        outcome_prices = data.get("outcomePrices", [])
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, TypeError):
                outcome_prices = []

        price_yes = None
        price_no = None

        if len(outcome_prices) > 0:
            try:
                price_yes = float(outcome_prices[0])
            except (ValueError, TypeError):
                pass
        if len(outcome_prices) > 1:
            try:
                price_no = float(outcome_prices[1])
            except (ValueError, TypeError):
                pass

        # Get volume - try different field names
        volume = data.get("volumeNum") or data.get("volume24hr") or data.get("volume")
        if volume and isinstance(volume, str):
            try:
                volume = float(volume)
            except (ValueError, TypeError):
                volume = None

        # Get liquidity
        liquidity = data.get("liquidityNum") or data.get("liquidity")
        if liquidity and isinstance(liquidity, str):
            try:
                liquidity = float(liquidity)
            except (ValueError, TypeError):
                liquidity = None

        return Market(
            id=data.get("id", data.get("conditionId", "")),
            condition_id=data.get("conditionId", data.get("id", "")),
            question=data.get("question", ""),
            description=data.get("description"),
            category=data.get("category"),
            end_date=end_date,
            active=data.get("active", True),
            token_id_yes=token_yes,
            token_id_no=token_no,
            price_yes=price_yes,
            price_no=price_no,
            volume_24h=volume,
            liquidity=liquidity,
        )

    async def sync_markets(self, limit: int = 100) -> list[Market]:
        """
        Fetch and parse markets from API.

        Args:
            limit: Maximum markets to fetch

        Returns:
            List of Market models
        """
        raw_markets = await self.get_markets(limit=limit)
        markets = []
        for m in raw_markets:
            # Skip non-dict items (API sometimes returns strings)
            if not isinstance(m, dict):
                logger.debug(f"Skipping non-dict market item: {type(m)}")
                continue
            try:
                markets.append(self.parse_market(m))
            except Exception as e:
                logger.debug(f"Failed to parse market: {e}")
                continue
        return markets


# Global client instance
gamma_client = GammaClient()
