"""Client for the Polymarket Gamma API (market/event metadata).

Field shapes are grounded in recorded fixtures (tests/fixtures/gamma_*.json,
captured Aug 2026). Notable realities:
- `category` is no longer populated; tags live only on the /events endpoint.
- `clobTokenIds`, `outcomePrices`, `outcomes` arrive as JSON-encoded strings.
- `feeSchedule` ({"rate": 0.05, ...}) + `feesEnabled` on the market are the
  authoritative fee source; `feesEnabled: false` means fee-free.
- Resolved markets: `closed: true`, `umaResolutionStatus: "resolved"`,
  `outcomePrices` like ["1", "0"]; `closedTime` uses "YYYY-MM-DD HH:MM:SS+00".
- Markets embed `events[]` with id/slug/title/negRisk for family grouping.
"""

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

import httpx

from config.settings import settings
from core.models import Market
from core.timeutil import parse_dt

logger = logging.getLogger(__name__)


def _as_list(value) -> list:
    """Gamma encodes some array fields as JSON strings."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return value if isinstance(value, list) else []


def _as_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_market(data: dict) -> Market:
    """Parse a raw Gamma market dict into the domain model."""
    token_ids = [str(t) for t in _as_list(data.get("clobTokenIds"))]
    outcome_prices = [_as_float(p) for p in _as_list(data.get("outcomePrices"))]
    outcomes = [str(o) for o in _as_list(data.get("outcomes"))]

    events = data.get("events") or []
    event = events[0] if isinstance(events, list) and events else {}

    fee_schedule = data.get("feeSchedule") or {}
    fees_enabled = bool(data.get("feesEnabled"))
    fee_rate = _as_float(fee_schedule.get("rate")) if fees_enabled else 0.0

    # Resolved outcome: index whose price settled at 1
    resolved_outcome = None
    if data.get("closed") and outcome_prices:
        for i, p in enumerate(outcome_prices):
            if p is not None and p >= 0.999:
                resolved_outcome = str(i)
                break

    return Market(
        id=str(data.get("id", "")),
        condition_id=data.get("conditionId", "") or "",
        question=data.get("question", "") or "",
        description=data.get("description"),
        category=data.get("category"),
        slug=data.get("slug"),
        end_date=parse_dt(data.get("endDate")),
        start_date=parse_dt(data.get("startDate")),
        active=bool(data.get("active", True)),
        closed=bool(data.get("closed", False)),
        accepting_orders=bool(data.get("acceptingOrders", True)),
        token_id_yes=token_ids[0] if len(token_ids) > 0 else None,
        token_id_no=token_ids[1] if len(token_ids) > 1 else None,
        outcomes=outcomes or None,
        price_yes=outcome_prices[0] if len(outcome_prices) > 0 else None,
        price_no=outcome_prices[1] if len(outcome_prices) > 1 else None,
        best_bid=_as_float(data.get("bestBid")),
        best_ask=_as_float(data.get("bestAsk")),
        spread=_as_float(data.get("spread")),
        last_trade_price=_as_float(data.get("lastTradePrice")),
        one_day_price_change=_as_float(data.get("oneDayPriceChange")),
        tick_size=_as_float(data.get("orderPriceMinTickSize")),
        volume_24h=_as_float(data.get("volume24hr")),
        liquidity=_as_float(data.get("liquidityNum") or data.get("liquidity")),
        event_id=str(event.get("id")) if event.get("id") else None,
        event_slug=event.get("slug"),
        group_item_title=data.get("groupItemTitle"),
        neg_risk=bool(data.get("negRisk", False)),
        neg_risk_market_id=data.get("negRiskMarketID"),
        fees_enabled=fees_enabled,
        fee_rate=fee_rate,
        fee_type=data.get("feeType"),
        resolution_source=data.get("resolutionSource"),
        uma_resolution_status=data.get("umaResolutionStatus"),
        resolved_outcome=resolved_outcome,
        closed_time=parse_dt(data.get("closedTime")),
        last_synced_at=None,
    )


def parse_event_tags(event: dict) -> list[str]:
    """Extract tag labels from an /events-endpoint event."""
    labels = []
    for t in event.get("tags") or []:
        if isinstance(t, dict) and t.get("label"):
            labels.append(t["label"])
        elif isinstance(t, str):
            labels.append(t)
    return labels


class GammaClient:
    """Async client for the Polymarket Gamma API."""

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
                "User-Agent": "PolymarketScanner/1.0",
            },
            follow_redirects=True,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        return self._client

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: Optional[bool] = True,
        closed: Optional[bool] = False,
        order: str = "volume24hr",
        ascending: bool = False,
        market_ids: Optional[list[str]] = None,
    ) -> list[dict]:
        """Fetch one page of raw market dicts."""
        params: dict = {
            "limit": limit,
            "offset": offset,
            "order": order,
            "ascending": str(ascending).lower(),
        }
        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()
        if market_ids:
            params["id"] = market_ids
        # HTTP errors PROPAGATE: swallowing them here made iter_markets read a
        # transient failure as end-of-pagination and silently truncate the sync
        response = await self.client.get("/markets", params=params)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else data.get("data", [])

    async def get_market(self, market_id: str) -> Optional[dict]:
        """Fetch a single raw market dict by id."""
        try:
            response = await self.client.get(f"/markets/{market_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
            return None

    async def iter_markets(
        self,
        max_pages: int = 10,
        page_size: int = 100,
        active: Optional[bool] = True,
        closed: Optional[bool] = False,
        order: str = "volume24hr",
        delay_s: float = 0.25,
    ) -> AsyncIterator[list[dict]]:
        """Paginate /markets, yielding raw pages until exhausted."""
        for page in range(max_pages):
            batch = await self.get_markets(
                limit=page_size,
                offset=page * page_size,
                active=active,
                closed=closed,
                order=order,
            )
            if not batch:
                return
            yield batch
            if len(batch) < page_size:
                return
            await asyncio.sleep(delay_s)

    async def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        active: Optional[bool] = True,
        closed: Optional[bool] = False,
        order: str = "volume24hr",
        ascending: bool = False,
    ) -> list[dict]:
        """Fetch one page of raw event dicts (markets nested, tags included)."""
        params = {
            "limit": limit,
            "offset": offset,
            "order": order,
            "ascending": str(ascending).lower(),
        }
        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()
        try:
            response = await self.client.get("/events", params=params)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else data.get("data", [])
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch events (offset={offset}): {e}")
            return []

    async def iter_events(
        self,
        max_pages: int = 10,
        page_size: int = 100,
        delay_s: float = 0.25,
    ) -> AsyncIterator[list[dict]]:
        """Paginate /events, yielding raw pages until exhausted."""
        for page in range(max_pages):
            batch = await self.get_events(limit=page_size, offset=page * page_size)
            if not batch:
                return
            yield batch
            if len(batch) < page_size:
                return
            await asyncio.sleep(delay_s)


# Global client instance
gamma_client = GammaClient()
