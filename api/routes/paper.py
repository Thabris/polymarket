"""Paper book API endpoints."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.runtime import runtime
from data.storage import db

router = APIRouter()


class PaperPositionResponse(BaseModel):
    """Paper position response model."""

    id: int
    signal_id: int
    strategy: str
    market_id: Optional[str] = None
    side: str
    entry_price: float
    size: float
    fees_paid: float
    status: str
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None
    opened_at: datetime
    closed_at: Optional[datetime] = None


@router.get("/positions", response_model=list[PaperPositionResponse])
async def list_positions(
    strategy: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """List paper positions, newest first."""
    positions = await db.get_paper_positions(strategy=strategy, status=status, limit=limit)
    return [
        PaperPositionResponse(
            id=p.id,
            signal_id=p.signal_id,
            strategy=p.strategy,
            market_id=p.market_id,
            side=p.side,
            entry_price=p.entry_price,
            size=p.size,
            fees_paid=p.fees_paid,
            status=p.status,
            exit_price=p.exit_price,
            exit_reason=p.exit_reason,
            pnl=p.pnl,
            opened_at=p.opened_at,
            closed_at=p.closed_at,
        )
        for p in positions
    ]


@router.get("/stats")
async def get_stats():
    """Per-strategy paper performance stats."""
    book = runtime.get("paper_book")
    if book is None:
        raise HTTPException(status_code=503, detail="Paper book not running (daemon mode only)")
    return await book.stats()


@router.get("/fills")
async def list_real_fills(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """List real (user-marked) fills."""
    fills = await db.get_real_fills(status=status, limit=limit)
    return [
        {
            "id": f.id,
            "signal_id": f.signal_id,
            "side": f.side,
            "price": f.price,
            "size": f.size,
            "fee_paid": f.fee_paid,
            "status": f.status,
            "exit_price": f.exit_price,
            "pnl": f.pnl,
            "note": f.note,
            "created_at": f.created_at,
        }
        for f in fills
    ]
