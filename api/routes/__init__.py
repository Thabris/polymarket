"""API routes for the Polymarket scanner platform."""

from .alerts import router as alerts_router
from .families import router as families_router
from .markets import router as markets_router
from .paper import router as paper_router
from .risk import router as risk_router
from .scanners import router as scanners_router
from .settings import router as settings_router
from .signals import router as signals_router

__all__ = [
    "markets_router",
    "alerts_router",
    "settings_router",
    "signals_router",
    "paper_router",
    "scanners_router",
    "families_router",
    "risk_router",
]
