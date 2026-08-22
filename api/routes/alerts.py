"""Alert management API endpoints."""

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from data.storage import db

router = APIRouter()


class AlertResponse(BaseModel):
    """Alert response model."""

    id: int
    market_id: Optional[str] = None
    alert_type: str
    severity: str
    title: str
    message: str
    data: Optional[dict] = None
    acknowledged: bool
    created_at: datetime


def _to_response(alert) -> AlertResponse:
    data = None
    if alert.data:
        try:
            data = json.loads(alert.data)
        except json.JSONDecodeError:
            data = None
    return AlertResponse(
        id=alert.id,
        market_id=alert.market_id,
        alert_type=alert.alert_type,
        severity=alert.severity,
        title=alert.title,
        message=alert.message,
        data=data,
        acknowledged=alert.acknowledged,
        created_at=alert.created_at,
    )


@router.get("", response_model=list[AlertResponse])
async def list_alerts(
    alert_type: Optional[str] = Query(default=None),
    market_id: Optional[str] = Query(default=None),
    acknowledged: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List alerts with optional filters (alert_type: signal, arbitrage, system)."""
    try:
        alerts = await db.get_alerts(
            alert_type=alert_type,
            market_id=market_id,
            acknowledged=acknowledged,
            limit=limit,
        )
        return [_to_response(a) for a in alerts]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/unread", response_model=list[AlertResponse])
async def get_unread_alerts(limit: int = Query(default=50, ge=1, le=200)):
    """List unacknowledged alerts."""
    try:
        alerts = await db.get_alerts(acknowledged=False, limit=limit)
        return [_to_response(a) for a in alerts]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int):
    """Mark an alert as acknowledged."""
    try:
        success = await db.acknowledge_alert(alert_id)
        if not success:
            raise HTTPException(status_code=404, detail="Alert not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
