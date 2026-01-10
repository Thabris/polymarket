"""API routes for Polymarket Monitor."""

from .markets import router as markets_router
from .alerts import router as alerts_router
from .settings import router as settings_router

__all__ = ["markets_router", "alerts_router", "settings_router"]
