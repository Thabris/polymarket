"""Risk overlay API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.runtime import runtime

router = APIRouter()


class LimitUpdate(BaseModel):
    """Set one risk limit at runtime (persisted across restarts)."""

    name: str
    value: float = Field(gt=0)


class KillSwitchRequest(BaseModel):
    """Engage or release the manual kill switch."""

    enabled: bool


def _engine():
    engine = runtime.get("risk")
    if engine is None:
        raise HTTPException(status_code=503, detail="Risk engine not running (daemon mode only)")
    return engine


@router.get("")
async def get_risk():
    """Full risk snapshot: exposures, VaR, limits, utilization, blocks."""
    return await _engine().snapshot()


@router.put("/limits")
async def set_limit(request: LimitUpdate):
    """Update a limit (total_deployed, per_strategy_deployed,
    per_event_deployed, per_market_deployed, var95, daily_loss)."""
    engine = _engine()
    try:
        await engine.set_limit(request.name, request.value)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"unknown limit: {request.name}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"success": True, "limits": engine.limits}


@router.post("/kill")
async def set_kill_switch(request: KillSwitchRequest):
    """Manual kill switch: while engaged, no new positions open (any strategy)."""
    engine = _engine()
    await engine.set_manual_kill(request.enabled)
    return {"success": True, "manual_kill": engine.manual_kill}
