"""FastAPI application setup."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.routes import (
    alerts_router,
    families_router,
    markets_router,
    paper_router,
    risk_router,
    scanners_router,
    settings_router,
    signals_router,
)
from api.websocket import router as ws_router
from config.settings import settings
from core.events import event_bus
from core.runtime import runtime
from data.gamma_client import gamma_client
from data.storage import db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Standalone-dev lifespan. The daemon (main.py) does its own wiring and
    registers itself in the runtime, in which case this does nothing."""
    standalone = runtime.get("daemon") is None
    if standalone:
        logger.info("API starting standalone (dev mode)")
        await db.init_db()
        await event_bus.start()
        await gamma_client.connect()
    yield
    if standalone:
        await gamma_client.close()
        await event_bus.stop()
        await db.close()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Polymarket Scanner",
        description="Strategy-scanner platform for Polymarket prediction markets",
        version="0.2.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # local, single-user
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(markets_router, prefix="/api/markets", tags=["markets"])
    app.include_router(alerts_router, prefix="/api/alerts", tags=["alerts"])
    app.include_router(settings_router, prefix="/api/settings", tags=["settings"])
    app.include_router(signals_router, prefix="/api/signals", tags=["signals"])
    app.include_router(paper_router, prefix="/api/paper", tags=["paper"])
    app.include_router(scanners_router, prefix="/api/scanners", tags=["scanners"])
    app.include_router(families_router, prefix="/api/families", tags=["families"])
    app.include_router(risk_router, prefix="/api/risk", tags=["risk"])
    app.include_router(ws_router, tags=["websocket"])

    @app.get("/api/status")
    async def get_status():
        """Get system status from the live runtime registry."""
        return {
            "running": True,
            "api_host": settings.api_host,
            "api_port": settings.api_port,
            **runtime.status(),
        }

    @app.get("/api/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy"}

    # Serve static files
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def root():
        """Serve the dashboard."""
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return RedirectResponse(url="/docs")

    return app


# Create app instance
app = create_app()
