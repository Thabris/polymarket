"""Public CLOB REST client (order books, prices). No auth needed for reads.

Book shape (verified fixture, Aug 2026): {bids: [{price, size}...],
asks: [...], tick_size, neg_risk, min_order_size}. Levels are NOT guaranteed
best-first — compute best bid = max(bids), best ask = min(asks).
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)


class ClobClient:
    """Async client for clob.polymarket.com public endpoints."""

    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.polymarket_clob_url
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(15.0, connect=10.0),
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

    async def get_book(self, token_id: str) -> Optional[dict]:
        """Raw order book for a token, or None on failure."""
        try:
            r = await self.client.get("/book", params={"token_id": token_id})
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            logger.warning(f"CLOB book fetch failed for {token_id[:16]}...: {e}")
            return None

    async def best_ask(self, token_id: str) -> Optional[tuple[float, float]]:
        """(best ask price, size at that level) for a token."""
        book = await self.get_book(token_id)
        if not book:
            return None
        asks = book.get("asks") or []
        best: Optional[tuple[float, float]] = None
        for level in asks:
            try:
                price, size = float(level["price"]), float(level["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if best is None or price < best[0]:
                best = (price, size)
        return best

    async def best_bid(self, token_id: str) -> Optional[tuple[float, float]]:
        """(best bid price, size at that level) for a token."""
        book = await self.get_book(token_id)
        if not book:
            return None
        bids = book.get("bids") or []
        best: Optional[tuple[float, float]] = None
        for level in bids:
            try:
                price, size = float(level["price"]), float(level["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if best is None or price > best[0]:
                best = (price, size)
        return best


# Global client instance
clob_client = ClobClient()
