"""Market data API endpoints."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from data.gamma_client import gamma_client
from data.storage import db

router = APIRouter()


class MarketResponse(BaseModel):
    """Market response model."""

    id: str
    condition_id: str
    question: str
    description: Optional[str] = None
    category: Optional[str] = None
    end_date: Optional[datetime] = None
    active: bool = True
    price_yes: Optional[float] = None
    price_no: Optional[float] = None
    volume_24h: Optional[float] = None
    liquidity: Optional[float] = None


class PriceResponse(BaseModel):
    """Price candle response model."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class WatchlistItemRequest(BaseModel):
    """Request to add market to watchlist."""

    market_id: str
    price_threshold_pct: Optional[float] = None
    volume_threshold_usd: Optional[float] = None
    notes: Optional[str] = None


class WatchlistItemResponse(BaseModel):
    """Watchlist item response."""

    id: int
    market_id: str
    price_threshold_pct: Optional[float] = None
    volume_threshold_usd: Optional[float] = None
    notes: Optional[str] = None
    created_at: datetime


@router.get("", response_model=list[MarketResponse])
async def list_markets(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    active: bool = Query(default=True),
):
    """
    List markets from Polymarket.

    Returns markets ordered by 24h volume.
    """
    try:
        markets = await gamma_client.get_markets(
            limit=limit,
            offset=offset,
            active=active,
        )

        # Handle empty response
        if not markets:
            return []

        # Handle case where API returns a wrapper object
        if isinstance(markets, dict):
            markets = markets.get("data", markets.get("markets", []))

        result = []
        for m in markets:
            if not isinstance(m, dict):
                continue
            try:
                result.append(_parse_market_response(m))
            except Exception:
                continue

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search", response_model=list[MarketResponse])
async def search_markets(
    q: str = Query(..., min_length=2),
    limit: int = Query(default=20, ge=1, le=50),
):
    """Search markets by text query."""
    try:
        markets = await gamma_client.search_markets(query=q, limit=limit)

        result = []
        for m in markets:
            if not isinstance(m, dict):
                continue
            try:
                result.append(_parse_market_response(m))
            except Exception:
                continue
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trending", response_model=list[MarketResponse])
async def get_trending_markets(
    limit: int = Query(default=20, ge=1, le=50),
):
    """Get trending markets by volume."""
    try:
        markets = await gamma_client.get_trending(limit=limit)

        result = []
        for m in markets:
            if not isinstance(m, dict):
                continue
            try:
                result.append(_parse_market_response(m))
            except Exception:
                continue
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{market_id}", response_model=MarketResponse)
async def get_market(market_id: str):
    """Get a single market by ID."""
    try:
        market = await gamma_client.get_market(market_id)
        if not market:
            raise HTTPException(status_code=404, detail="Market not found")

        return _parse_market_response(market)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{market_id}/prices", response_model=list[PriceResponse])
async def get_market_prices(
    market_id: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = Query(default=100, ge=1, le=1000),
):
    """Get price history for a market."""
    try:
        prices = await db.get_prices(
            market_id=market_id,
            start=start,
            end=end,
            limit=limit,
        )

        return [
            PriceResponse(
                timestamp=p.timestamp,
                open=p.open,
                high=p.high,
                low=p.low,
                close=p.close,
                volume=p.volume,
            )
            for p in prices
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Watchlist endpoints
@router.get("/watchlist", response_model=list[WatchlistItemResponse])
async def get_watchlist():
    """Get the watchlist."""
    try:
        items = await db.get_watchlist()
        return [
            WatchlistItemResponse(
                id=item.id,
                market_id=item.market_id,
                price_threshold_pct=item.price_threshold_pct,
                volume_threshold_usd=item.volume_threshold_usd,
                notes=item.notes,
                created_at=item.created_at,
            )
            for item in items
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/watchlist", response_model=WatchlistItemResponse)
async def add_to_watchlist(request: WatchlistItemRequest):
    """Add a market to the watchlist."""
    try:
        item = await db.add_to_watchlist(
            market_id=request.market_id,
            price_threshold_pct=request.price_threshold_pct,
            volume_threshold_usd=request.volume_threshold_usd,
            notes=request.notes,
        )

        return WatchlistItemResponse(
            id=item.id,
            market_id=item.market_id,
            price_threshold_pct=item.price_threshold_pct,
            volume_threshold_usd=item.volume_threshold_usd,
            notes=item.notes,
            created_at=item.created_at,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/watchlist/{market_id}")
async def remove_from_watchlist(market_id: str):
    """Remove a market from the watchlist."""
    try:
        success = await db.remove_from_watchlist(market_id)
        if not success:
            raise HTTPException(status_code=404, detail="Market not in watchlist")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _parse_market_response(m: dict) -> MarketResponse:
    """Parse raw market data into MarketResponse."""
    import json

    # Get prices from outcomePrices (JSON string or list)
    outcome_prices = m.get("outcomePrices", [])
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

    # Get volume
    volume = m.get("volumeNum") or m.get("volume24hr") or m.get("volume")
    if volume and isinstance(volume, str):
        try:
            volume = float(volume)
        except (ValueError, TypeError):
            volume = None

    # Get liquidity
    liquidity = m.get("liquidityNum") or m.get("liquidity")
    if liquidity and isinstance(liquidity, str):
        try:
            liquidity = float(liquidity)
        except (ValueError, TypeError):
            liquidity = None

    return MarketResponse(
        id=m.get("id", m.get("conditionId", "")),
        condition_id=m.get("conditionId", m.get("id", "")),
        question=m.get("question", ""),
        description=m.get("description"),
        category=m.get("category"),
        end_date=m.get("endDate"),
        active=m.get("active", True),
        price_yes=price_yes,
        price_no=price_no,
        volume_24h=volume,
        liquidity=liquidity,
    )
