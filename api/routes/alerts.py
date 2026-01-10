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


class AlertConfigResponse(BaseModel):
    """Alert configuration response."""

    price_change_threshold_pct: float
    price_change_window_minutes: int
    volume_spike_threshold_usd: float
    volume_spike_window_minutes: int
    whale_trade_threshold_usd: float


class AlertConfigUpdate(BaseModel):
    """Alert configuration update request."""

    price_change_threshold_pct: Optional[float] = None
    price_change_window_minutes: Optional[int] = None
    volume_spike_threshold_usd: Optional[float] = None
    volume_spike_window_minutes: Optional[int] = None
    whale_trade_threshold_usd: Optional[float] = None


@router.get("", response_model=list[AlertResponse])
async def list_alerts(
    alert_type: Optional[str] = Query(default=None),
    market_id: Optional[str] = Query(default=None),
    acknowledged: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """
    List alerts with optional filters.

    Args:
        alert_type: Filter by alert type (price_change, volume_spike, whale_trade)
        market_id: Filter by market ID
        acknowledged: Filter by acknowledgement status
        limit: Maximum number of alerts to return
    """
    try:
        alerts = await db.get_alerts(
            alert_type=alert_type,
            market_id=market_id,
            acknowledged=acknowledged,
            limit=limit,
        )

        return [
            AlertResponse(
                id=alert.id,
                market_id=alert.market_id,
                alert_type=alert.alert_type,
                severity=alert.severity,
                title=alert.title,
                message=alert.message,
                data=json.loads(alert.data) if alert.data else None,
                acknowledged=alert.acknowledged,
                created_at=alert.created_at,
            )
            for alert in alerts
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/unread", response_model=list[AlertResponse])
async def list_unread_alerts(
    limit: int = Query(default=20, ge=1, le=100),
):
    """Get unacknowledged alerts."""
    return await list_alerts(acknowledged=False, limit=limit)


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


@router.get("/config", response_model=AlertConfigResponse)
async def get_alert_config():
    """Get current alert configuration."""
    from config.settings import settings

    # Try to get from database first, fall back to settings
    price_pct = await db.get_config("price_change_threshold_pct")
    price_window = await db.get_config("price_change_window_minutes")
    volume_usd = await db.get_config("volume_spike_threshold_usd")
    volume_window = await db.get_config("volume_spike_window_minutes")
    whale_usd = await db.get_config("whale_trade_threshold_usd")

    return AlertConfigResponse(
        price_change_threshold_pct=float(price_pct)
        if price_pct
        else settings.price_change_threshold_pct,
        price_change_window_minutes=int(price_window)
        if price_window
        else settings.price_change_window_minutes,
        volume_spike_threshold_usd=float(volume_usd)
        if volume_usd
        else settings.volume_spike_threshold_usd,
        volume_spike_window_minutes=int(volume_window)
        if volume_window
        else settings.volume_spike_window_minutes,
        whale_trade_threshold_usd=float(whale_usd)
        if whale_usd
        else settings.whale_trade_threshold_usd,
    )


@router.put("/config", response_model=AlertConfigResponse)
async def update_alert_config(config: AlertConfigUpdate):
    """Update alert configuration."""
    try:
        if config.price_change_threshold_pct is not None:
            await db.set_config(
                "price_change_threshold_pct",
                str(config.price_change_threshold_pct),
            )

        if config.price_change_window_minutes is not None:
            await db.set_config(
                "price_change_window_minutes",
                str(config.price_change_window_minutes),
            )

        if config.volume_spike_threshold_usd is not None:
            await db.set_config(
                "volume_spike_threshold_usd",
                str(config.volume_spike_threshold_usd),
            )

        if config.volume_spike_window_minutes is not None:
            await db.set_config(
                "volume_spike_window_minutes",
                str(config.volume_spike_window_minutes),
            )

        if config.whale_trade_threshold_usd is not None:
            await db.set_config(
                "whale_trade_threshold_usd",
                str(config.whale_trade_threshold_usd),
            )

        return await get_alert_config()

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
