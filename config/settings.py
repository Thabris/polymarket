"""Application settings with environment variable support.

All scanner parameters are env-overridable (pydantic-settings parses JSON
for dict/list fields, e.g. THETA_EXCLUDED_CATEGORIES='["weather"]').
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Polymarket API credentials (only needed for the future LiveRouter)
    polymarket_api_key: Optional[str] = Field(default=None)
    polymarket_api_secret: Optional[str] = Field(default=None)
    polymarket_private_key: Optional[str] = Field(default=None)

    # Database
    database_url: str = Field(default="sqlite+aiosqlite:///./var/polymarket.db")

    # API server
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8000)

    # Polymarket endpoints
    polymarket_clob_url: str = Field(default="https://clob.polymarket.com")
    polymarket_gamma_url: str = Field(default="https://gamma-api.polymarket.com")
    polymarket_ws_url: str = Field(
        default="wss://ws-subscriptions-clob.polymarket.com/ws/market"
    )

    # Logging
    log_level: str = Field(default="INFO")

    # Universe / market sync
    scan_min_liquidity: float = Field(default=10000.0)
    scan_min_volume_24h: float = Field(default=1000.0)
    stream_universe_size: int = Field(default=300)
    universe_refresh_minutes: int = Field(default=15)
    universe_sync_pages: int = Field(default=15, description="pages of 100 markets per sync")

    # Theta scanner
    theta_interval_minutes: int = Field(default=15)
    theta_ask_min: float = Field(default=0.93)
    theta_ask_max: float = Field(default=0.985)
    theta_max_days: float = Field(default=45.0)
    theta_min_days: float = Field(
        default=0.75,
        description="floor in days — intraday sports/crypto are calibrated (no edge) and annualization is meaningless there",
    )
    theta_hurdle_annualized: float = Field(default=0.12)
    # research: short-horizon weather + entertainment domains are OVERCONFIDENT
    # (theta negative-EV there); our tag map canonicalizes entertainment -> "culture"
    theta_excluded_categories: list[str] = Field(default=["weather", "culture", "mentions"])
    theta_book_refine_top_n: int = Field(default=30)
    usdc_benchmark_apy: float = Field(default=0.045)

    # Fee fallbacks (feeSchedule.rate on the market itself is authoritative;
    # this map is only used when the market carries no fee fields)
    category_fee_rates: dict[str, float] = Field(
        default={
            "crypto": 0.07,
            "sports": 0.05,
            "economics": 0.05,
            "culture": 0.05,
            "weather": 0.05,
            "politics": 0.04,
            "finance": 0.04,
            "tech": 0.04,
            "mentions": 0.04,
            "geopolitics": 0.0,
        }
    )
    default_fee_rate: float = Field(default=0.05)

    # News-fade scanner
    fade_spike_pp: float = Field(default=0.12)
    fade_window_minutes: int = Field(default=30)
    fade_min_liquidity: float = Field(default=25000.0)
    fade_excluded_categories: list[str] = Field(default=["crypto"])
    fade_near_end_exclusion_hours: float = Field(default=48.0)
    fade_retrace_low: float = Field(default=0.50)
    fade_retrace_high: float = Field(default=0.618)
    fade_entry_validity_hours: float = Field(default=2.0)
    fade_time_stop_hours: float = Field(default=24.0)
    # A market gets one directional fade thesis at a time: once faded, the
    # opposite-direction fade is suppressed for this long. The retrace the
    # first signal predicted would otherwise trip the scanner in reverse.
    fade_direction_lock_hours: float = Field(default=24.0)
    fade_toast_pp: float = Field(default=0.15)
    fade_toast_min_liquidity: float = Field(default=100000.0)

    # Calendar scanner
    calendar_interval_minutes: int = Field(default=60)

    # Paper trading
    paper_default_notional: float = Field(default=100.0)
    risk_max_position_per_market: float = Field(default=500.0)
    risk_max_open_per_strategy: int = Field(default=25)
    risk_max_daily_loss: float = Field(default=250.0)

    # WebSocket
    ws_reconnect_delay: float = Field(default=1.0)
    ws_max_reconnect_delay: float = Field(default=60.0)
    ws_assets_per_connection: int = Field(default=400)
    ws_app_ping_seconds: float = Field(default=10.0)
    ws_stale_seconds: float = Field(default=60.0)

    # Bars / retention
    bar_retention_days: int = Field(default=14)
    scanner_run_retention: int = Field(default=500)

    # Notifications
    notify_theta_min_grade: str = Field(default="A")
    notify_theta_min_annualized: float = Field(default=0.15)
    notify_max_toasts_per_hour: int = Field(default=6)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
