"""Application settings with environment variable and keyring support."""

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

    # Polymarket API Credentials (loaded via credentials.py for security)
    polymarket_api_key: Optional[str] = Field(default=None)
    polymarket_api_secret: Optional[str] = Field(default=None)
    polymarket_private_key: Optional[str] = Field(default=None)

    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/polymarket.db",
        description="Database connection URL",
    )

    # API Server
    api_host: str = Field(default="127.0.0.1", description="API server host")
    api_port: int = Field(default=8000, description="API server port")

    # Alert Thresholds
    price_change_threshold_pct: float = Field(
        default=5.0,
        description="Price change percentage to trigger alert",
    )
    price_change_window_minutes: int = Field(
        default=5,
        description="Time window for price change detection (minutes)",
    )
    volume_spike_threshold_usd: float = Field(
        default=10000.0,
        description="Volume threshold in USD to trigger alert",
    )
    volume_spike_window_minutes: int = Field(
        default=5,
        description="Time window for volume spike detection (minutes)",
    )
    whale_trade_threshold_usd: float = Field(
        default=5000.0,
        description="Single trade size in USD to trigger whale alert",
    )

    # Polymarket API URLs
    polymarket_clob_url: str = Field(
        default="https://clob.polymarket.com",
        description="Polymarket CLOB API URL",
    )
    polymarket_gamma_url: str = Field(
        default="https://gamma-api.polymarket.com",
        description="Polymarket Gamma API URL",
    )
    polymarket_ws_url: str = Field(
        default="wss://ws-subscriptions-clob.polymarket.com/ws/market",
        description="Polymarket WebSocket URL",
    )

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")

    # WebSocket Settings
    ws_reconnect_delay: float = Field(
        default=1.0,
        description="Initial WebSocket reconnection delay (seconds)",
    )
    ws_max_reconnect_delay: float = Field(
        default=60.0,
        description="Maximum WebSocket reconnection delay (seconds)",
    )
    ws_ping_interval: float = Field(
        default=20.0,
        description="WebSocket ping interval (seconds)",
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance
settings = get_settings()
