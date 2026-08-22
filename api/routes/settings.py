"""Settings and configuration API endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

from config.credentials import has_credentials, has_trading_credentials
from config.settings import settings

router = APIRouter()


class SettingsResponse(BaseModel):
    """Settings response model."""

    # API Server
    api_host: str
    api_port: int

    # Scanner settings (read-only view)
    theta_interval_minutes: int
    theta_hurdle_annualized: float
    fade_spike_pp: float
    calendar_interval_minutes: int
    stream_universe_size: int
    paper_default_notional: float

    # Connection Status
    has_api_credentials: bool
    has_trading_credentials: bool

    # Polymarket URLs
    polymarket_clob_url: str
    polymarket_gamma_url: str

    # Logging
    log_level: str


@router.get("", response_model=SettingsResponse)
async def get_settings():
    """Get current application settings."""
    return SettingsResponse(
        api_host=settings.api_host,
        api_port=settings.api_port,
        theta_interval_minutes=settings.theta_interval_minutes,
        theta_hurdle_annualized=settings.theta_hurdle_annualized,
        fade_spike_pp=settings.fade_spike_pp,
        calendar_interval_minutes=settings.calendar_interval_minutes,
        stream_universe_size=settings.stream_universe_size,
        paper_default_notional=settings.paper_default_notional,
        has_api_credentials=has_credentials(),
        has_trading_credentials=has_trading_credentials(),
        polymarket_clob_url=settings.polymarket_clob_url,
        polymarket_gamma_url=settings.polymarket_gamma_url,
        log_level=settings.log_level,
    )


@router.get("/credentials/status")
async def get_credentials_status():
    """Check credential configuration status."""
    return {
        "api_credentials": has_credentials(),
        "trading_credentials": has_trading_credentials(),
    }
