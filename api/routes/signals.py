"""Signal API endpoints."""

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core.runtime import runtime
from data.storage import db
from signals.models import Signal, SignalGrade, SignalSide

router = APIRouter()


class SignalResponse(BaseModel):
    """Signal response model."""

    id: int
    strategy: str
    market_id: Optional[str] = None
    token_id: Optional[str] = None
    side: str
    grade: str
    score: Optional[float] = None
    entry_price: Optional[float] = None
    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None
    size_hint: Optional[float] = None
    status: str
    expires_at: Optional[datetime] = None
    notified: bool = False
    acknowledged: bool = False
    snapshot: Optional[dict] = None
    created_at: datetime


class RealFillRequest(BaseModel):
    """Mark a signal as actually traded."""

    price: float = Field(gt=0, lt=1)
    size: float = Field(gt=0)
    side: Optional[str] = None
    fee_paid: Optional[float] = None
    note: Optional[str] = None


class ManualSignalRequest(BaseModel):
    """Create a manual signal (e.g. flagged calendar window)."""

    market_id: str
    token_id: Optional[str] = None
    side: str = "buy_yes"
    entry_price: Optional[float] = None
    note: Optional[str] = None
    snapshot: Optional[dict] = None


def _to_response(s) -> SignalResponse:
    snapshot = None
    if s.snapshot:
        try:
            snapshot = json.loads(s.snapshot)
        except json.JSONDecodeError:
            snapshot = None
    return SignalResponse(
        id=s.id,
        strategy=s.strategy,
        market_id=s.market_id,
        token_id=s.token_id,
        side=s.side,
        grade=s.grade,
        score=s.score,
        entry_price=s.entry_price,
        entry_zone_low=s.entry_zone_low,
        entry_zone_high=s.entry_zone_high,
        size_hint=s.size_hint,
        status=s.status,
        expires_at=s.expires_at,
        notified=s.notified,
        acknowledged=s.acknowledged,
        snapshot=snapshot,
        created_at=s.created_at,
    )


@router.get("", response_model=list[SignalResponse])
async def list_signals(
    strategy: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    grade: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    """List signals, newest first."""
    signals = await db.get_signals(strategy=strategy, status=status, grade=grade, limit=limit)
    return [_to_response(s) for s in signals]


@router.get("/{signal_id}", response_model=dict)
async def get_signal(signal_id: int):
    """Signal detail with its paper position and real fills."""
    signal = await db.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    position = await db.get_paper_position_by_signal(signal_id)
    fills = await db.get_real_fills(signal_id=signal_id)
    return {
        "signal": _to_response(signal).model_dump(),
        "paper_position": {
            "id": position.id,
            "entry_price": position.entry_price,
            "size": position.size,
            "fees_paid": position.fees_paid,
            "status": position.status,
            "exit_price": position.exit_price,
            "exit_reason": position.exit_reason,
            "pnl": position.pnl,
        } if position else None,
        "real_fills": [
            {
                "id": f.id,
                "price": f.price,
                "size": f.size,
                "status": f.status,
                "pnl": f.pnl,
                "note": f.note,
            }
            for f in fills
        ],
    }


@router.post("/{signal_id}/ack")
async def acknowledge_signal(signal_id: int):
    """Mark a signal as acknowledged."""
    signal = await db.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    await db.update_signal(signal_id, acknowledged=True)
    return {"success": True}


@router.post("/{signal_id}/fills")
async def add_real_fill(signal_id: int, request: RealFillRequest):
    """Record that you actually traded this signal (tracked separately)."""
    signal = await db.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    fill = await db.insert_real_fill(
        {
            "signal_id": signal_id,
            "side": request.side or signal.side,
            "price": request.price,
            "size": request.size,
            "fee_paid": request.fee_paid,
            "note": request.note,
        }
    )
    return {"success": True, "fill_id": fill.id}


@router.post("/manual")
async def create_manual_signal(request: ManualSignalRequest):
    """Create a manual signal (flag-window flow); enters the paper pipeline."""
    service = runtime.get("signals")
    if service is None:
        raise HTTPException(status_code=503, detail="Signal service not running (daemon mode only)")
    market = await db.get_market(request.market_id)
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found")
    token_id = request.token_id or (
        market.token_id_yes if request.side == "buy_yes" else market.token_id_no
    )
    entry = request.entry_price
    if entry is None:
        entry = market.best_ask if request.side == "buy_yes" else (
            1.0 - market.best_bid if market.best_bid is not None else None
        )
    if entry is None:
        raise HTTPException(status_code=400, detail="No entry price available; pass entry_price")
    snapshot = {
        "question": market.question,
        "note": request.note,
        "fee_rate": market.fee_rate or 0.0,
        **(request.snapshot or {}),
    }
    from core.timeutil import utcnow
    signal = Signal(
        strategy="manual",
        market_id=request.market_id,
        token_id=token_id,
        side=SignalSide(request.side),
        grade=SignalGrade.B,
        entry_price=entry,
        dedup_key=f"manual:{request.market_id}:{request.side}:{utcnow().timestamp():.0f}",
        snapshot=snapshot,
    )
    created = await service.publish(signal)
    return {"success": created, "signal_id": signal.id}
