"""Polymarket Data API client — READ-ONLY view of a real account.

Positions are public on-chain data keyed by the proxy-wallet ADDRESS; no API
key, no signature, no private key is ever involved here. Schema captured in
tests/fixtures/dataapi_positions.json (Aug 2026).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def valid_address(address: str) -> bool:
    """True for a well-formed EVM address."""
    return bool(ADDRESS_RE.match(address or ""))


class PortfolioClient:
    """Async client for data-api.polymarket.com (public reads only)."""

    def __init__(self, base_url: str = DATA_API):
        self.base_url = base_url
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={"Accept": "application/json", "User-Agent": "PolymarketScanner/1.0"},
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self._client

    async def get_positions(self, user: str, page_size: int = 500) -> list[dict]:
        """All current positions for a wallet (paginated until exhausted)."""
        out: list[dict] = []
        offset = 0
        while True:
            r = await self.client.get(
                "/positions",
                params={"user": user, "limit": page_size, "offset": offset},
            )
            r.raise_for_status()
            batch = r.json()
            if not isinstance(batch, list) or not batch:
                break
            out.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
            if offset >= 5000:  # sanity ceiling, loud
                logger.error("real portfolio truncated at 5000 positions")
                break
        return out

    async def get_value(self, user: str) -> Optional[float]:
        """Total current portfolio value in USDC, or None on failure."""
        try:
            r = await self.client.get("/value", params={"user": user})
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data:
                return float(data[0].get("value", 0.0))
        except (httpx.HTTPError, ValueError, KeyError) as e:
            logger.warning(f"portfolio value fetch failed: {e}")
        return None


# Global client instance
portfolio_client = PortfolioClient()
