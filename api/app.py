"""FastAPI application setup."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.routes import alerts_router, markets_router, settings_router
from api.websocket import router as ws_router
from config.settings import settings
from core.events import event_bus
from data.gamma_client import gamma_client
from data.storage import db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler for startup/shutdown."""
    # Startup
    logger.info("Starting Polymarket Monitor API...")

    # Initialize database
    await db.init_db()
    logger.info("Database initialized")

    # Start event bus
    await event_bus.start()
    logger.info("Event bus started")

    # Connect to Gamma API
    await gamma_client.connect()
    logger.info("Gamma API client connected")

    yield

    # Shutdown
    logger.info("Shutting down Polymarket Monitor API...")

    await gamma_client.close()
    await event_bus.stop()
    await db.close()

    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Polymarket Monitor",
        description="API for monitoring Polymarket prediction markets",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(markets_router, prefix="/api/markets", tags=["markets"])
    app.include_router(alerts_router, prefix="/api/alerts", tags=["alerts"])
    app.include_router(settings_router, prefix="/api/settings", tags=["settings"])
    app.include_router(ws_router, tags=["websocket"])

    @app.get("/api/status")
    async def get_status():
        """Get system status."""
        from data.websocket_manager import ws_manager

        return {
            "running": True,
            "websocket_connected": ws_manager.is_connected,
            "api_host": settings.api_host,
            "api_port": settings.api_port,
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

    @app.get("/whales")
    async def whales_page():
        """Serve the whale alerts page."""
        whales_path = static_dir / "whales.html"
        if whales_path.exists():
            return FileResponse(str(whales_path))
        return RedirectResponse(url="/")

    return app


# Create app instance
app = create_app()
