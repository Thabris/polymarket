"""Market data API endpoints."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.models import Market
from data.gamma_client import gamma_client, parse_market
from data.storage import db

router = APIRouter()


class MarketResponse(BaseModel):
    """Market response model."""

    id: str
    condition_id: str
    slug: Optional[str] = None
    event_slug: Optional[str] = None
    question: str
    description: Optional[str] = None
    category: Optional[str] = None
    end_date: Optional[datetime] = None
    active: bool = True
    closed: bool = False
    price_yes: Optional[float] = None
    price_no: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    spread: Optional[float] = None
    volume_24h: Optional[float] = None
    liquidity: Optional[float] = None


class PriceResponse(BaseModel):
    """Price bar response model."""

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


def _to_response(m: Market) -> MarketResponse:
    return MarketResponse(
        id=m.id,
        condition_id=m.condition_id,
        slug=m.slug,
        event_slug=m.event_slug,
        question=m.question,
        description=m.description,
        category=m.category,
        end_date=m.end_date,
        active=m.active,
        closed=m.closed,
        price_yes=m.price_yes,
        price_no=m.price_no,
        best_bid=m.best_bid,
        best_ask=m.best_ask,
        spread=m.spread,
        volume_24h=m.volume_24h,
        liquidity=m.liquidity,
    )


def _parse_page(raw_markets: list) -> list[MarketResponse]:
    out = []
    for m in raw_markets:
        if not isinstance(m, dict):
            continue
        try:
            out.append(_to_response(parse_market(m)))
        except Exception:
            continue
    return out


@router.get("", response_model=list[MarketResponse])
async def list_markets(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    active: bool = Query(default=True),
):
    """List markets from Polymarket, ordered by 24h volume."""
    try:
        markets = await gamma_client.get_markets(limit=limit, offset=offset, active=active)
        return _parse_page(markets)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trending", response_model=list[MarketResponse])
async def get_trending_markets(limit: int = Query(default=20, ge=1, le=50)):
    """Get trending markets by volume."""
    try:
        markets = await gamma_client.get_markets(limit=limit, order="volume24hr")
        return _parse_page(markets)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Watchlist endpoints — declared BEFORE /{market_id} so they are reachable
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


@router.get("/{market_id}", response_model=MarketResponse)
async def get_market(market_id: str):
    """Get a single market by ID."""
    try:
        market = await gamma_client.get_market(market_id)
        if not market:
            raise HTTPException(status_code=404, detail="Market not found")
        return _to_response(parse_market(market))
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
    """Get locally recorded price history for a market."""
    try:
        prices = await db.get_prices(market_id=market_id, start=start, end=end, limit=limit)
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
