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

    # Alert Thresholds (from settings)
    price_change_threshold_pct: float
    price_change_window_minutes: int
    volume_spike_threshold_usd: float
    volume_spike_window_minutes: int
    whale_trade_threshold_usd: float

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
        price_change_threshold_pct=settings.price_change_threshold_pct,
        price_change_window_minutes=settings.price_change_window_minutes,
        volume_spike_threshold_usd=settings.volume_spike_threshold_usd,
        volume_spike_window_minutes=settings.volume_spike_window_minutes,
        whale_trade_threshold_usd=settings.whale_trade_threshold_usd,
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
