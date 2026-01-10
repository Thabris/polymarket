#!/usr/bin/env python3
"""
Polymarket Monitor - Main Entry Point

A Python-based platform to monitor Polymarket betting markets,
detect significant events, and deliver real-time alerts.

Usage:
    python main.py              # Start the full application
    python main.py --api-only   # Start only the API server
    python main.py --no-tray    # Start without system tray
"""

import argparse
import asyncio
import logging
import signal
import sys
import webbrowser
from datetime import datetime
from typing import Optional

import uvicorn

from api.app import app
from config.settings import settings
from core.events import Event, EventType, event_bus
from core.models import PriceUpdate
from data.gamma_client import gamma_client
from data.polymarket_client import polymarket_client
from data.storage import db
from data.websocket_manager import WebSocketManager
from monitors.price_monitor import PriceMonitor
from monitors.volume_monitor import VolumeMonitor
from monitors.whale_monitor import WhaleMonitor
from notifications.desktop_notifier import ConsoleNotifier, DesktopNotifier
from ui.tray import SystemTray

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


class PolymarketMonitor:
    """Main application orchestrator."""

    def __init__(
        self,
        enable_tray: bool = True,
        api_only: bool = False,
    ):
        self.enable_tray = enable_tray
        self.api_only = api_only

        self._running = False
        self._start_time: Optional[datetime] = None

        # Components
        self._ws_manager: Optional[WebSocketManager] = None
        self._monitors: list = []
        self._notifiers: list = []
        self._tray: Optional[SystemTray] = None
        self._server_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the application."""
        if self._running:
            return

        logger.info("=" * 50)
        logger.info("Starting Polymarket Monitor...")
        logger.info("=" * 50)

        self._running = True
        self._start_time = datetime.utcnow()

        try:
            # Initialize database
            await db.init_db()
            logger.info("Database initialized")

            # Start event bus
            await event_bus.start()
            logger.info("Event bus started")

            # Connect API clients
            await gamma_client.connect()
            logger.info("Gamma API client connected")

            await polymarket_client.connect()
            logger.info("Polymarket client connected")

            if not self.api_only:
                # Start monitoring components
                await self._start_monitoring()

            # Start system tray
            if self.enable_tray and not self.api_only:
                self._start_tray()

            # Start API server
            self._server_task = asyncio.create_task(self._run_api_server())

            logger.info("=" * 50)
            logger.info(f"Polymarket Monitor running!")
            logger.info(f"API: http://{settings.api_host}:{settings.api_port}")
            logger.info(f"Docs: http://{settings.api_host}:{settings.api_port}/docs")
            logger.info("=" * 50)

        except Exception as e:
            logger.error(f"Failed to start: {e}")
            await self.stop()
            raise

    async def _start_monitoring(self) -> None:
        """Start monitoring components."""
        # Initialize WebSocket manager
        self._ws_manager = WebSocketManager(
            on_price_update=self._on_price_update,
        )
        await self._ws_manager.connect()
        logger.info("WebSocket manager started")

        # Initialize monitors
        self._monitors = [
            PriceMonitor(),
            VolumeMonitor(),
            WhaleMonitor(),
        ]

        for monitor in self._monitors:
            await monitor.start()
        logger.info(f"Started {len(self._monitors)} monitors")

        # Initialize notifiers
        self._notifiers = [
            DesktopNotifier(),
            ConsoleNotifier(),
        ]

        for notifier in self._notifiers:
            await notifier.start()
        logger.info(f"Started {len(self._notifiers)} notifiers")

        # Fetch initial markets and subscribe
        await self._subscribe_to_markets()

    async def _subscribe_to_markets(self) -> None:
        """Fetch and subscribe to active markets with pagination."""
        try:
            all_markets = []
            offset = 0
            batch_size = 100
            max_markets = settings.max_markets or 10000  # 0 means fetch all

            while len(all_markets) < max_markets:
                markets = await gamma_client.sync_markets(limit=batch_size)
                if not markets:
                    break

                # Fetch with offset for next batch
                raw_markets = await gamma_client.get_markets(
                    limit=batch_size,
                    offset=offset,
                    active=True,
                )
                if not raw_markets:
                    break

                for m in raw_markets:
                    if not isinstance(m, dict):
                        continue
                    try:
                        market = gamma_client.parse_market(m)
                        all_markets.append(market)
                    except Exception:
                        continue

                logger.info(f"Fetched {len(all_markets)} markets so far...")

                if len(raw_markets) < batch_size:
                    break  # No more markets

                offset += batch_size

                # Respect rate limits
                await asyncio.sleep(0.2)

            logger.info(f"Fetched {len(all_markets)} total markets")

            # Subscribe to all markets
            subscribed = 0
            for market in all_markets:
                if market.token_id_yes:
                    await self._ws_manager.subscribe(market.token_id_yes)
                    subscribed += 1
                if market.token_id_no:
                    await self._ws_manager.subscribe(market.token_id_no)
                    subscribed += 1

                # Store in database
                await db.upsert_market(market.model_dump())

            logger.info(f"Subscribed to {subscribed} tokens from {len(all_markets)} markets")

        except Exception as e:
            logger.error(f"Failed to subscribe to markets: {e}")

    def _on_price_update(self, update: PriceUpdate) -> None:
        """Handle price update from WebSocket."""
        # Emit event for monitors
        event_bus.emit_sync(
            Event(
                type=EventType.PRICE_UPDATE,
                data=update,
                source="websocket",
            )
        )

    def _start_tray(self) -> None:
        """Start the system tray."""
        self._tray = SystemTray(
            on_start=self._on_tray_start,
            on_stop=self._on_tray_stop,
            on_exit=self._on_tray_exit,
            on_open_dashboard=self._on_open_dashboard,
        )
        self._tray.start()
        self._tray.set_monitoring(True)

    def _on_tray_start(self) -> None:
        """Handle start from tray."""
        if self._tray:
            self._tray.set_monitoring(True)
        logger.info("Monitoring started from tray")

    def _on_tray_stop(self) -> None:
        """Handle stop from tray."""
        if self._tray:
            self._tray.set_monitoring(False)
        logger.info("Monitoring stopped from tray")

    def _on_tray_exit(self) -> None:
        """Handle exit from tray."""
        logger.info("Exit requested from tray")
        asyncio.get_event_loop().call_soon_threadsafe(
            lambda: asyncio.create_task(self.stop())
        )

    def _on_open_dashboard(self) -> None:
        """Open dashboard in browser."""
        url = f"http://{settings.api_host}:{settings.api_port}/docs"
        webbrowser.open(url)

    async def _run_api_server(self) -> None:
        """Run the API server."""
        config = uvicorn.Config(
            app,
            host=settings.api_host,
            port=settings.api_port,
            log_level=settings.log_level.lower(),
            access_log=False,
        )
        server = uvicorn.Server(config)
        await server.serve()

    async def stop(self) -> None:
        """Stop the application."""
        if not self._running:
            return

        logger.info("Stopping Polymarket Monitor...")
        self._running = False

        # Stop tray
        if self._tray:
            self._tray.stop()

        # Stop monitors
        for monitor in self._monitors:
            await monitor.stop()

        # Stop notifiers
        for notifier in self._notifiers:
            await notifier.stop()

        # Stop WebSocket
        if self._ws_manager:
            await self._ws_manager.disconnect()

        # Stop event bus
        await event_bus.stop()

        # Close clients
        await gamma_client.close()
        await polymarket_client.close()

        # Close database
        await db.close()

        # Cancel API server
        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass

        logger.info("Polymarket Monitor stopped")


async def main(args: argparse.Namespace) -> None:
    """Main async entry point."""
    monitor = PolymarketMonitor(
        enable_tray=not args.no_tray,
        api_only=args.api_only,
    )

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    def shutdown_handler():
        logger.info("Shutdown signal received")
        asyncio.create_task(monitor.stop())

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, shutdown_handler)
        loop.add_signal_handler(signal.SIGTERM, shutdown_handler)

    try:
        await monitor.start()

        # Keep running until stopped
        while monitor._running:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        await monitor.stop()


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Polymarket Monitor - Market monitoring and alerts",
    )

    parser.add_argument(
        "--api-only",
        action="store_true",
        help="Run only the API server without monitoring",
    )

    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Disable system tray icon",
    )

    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="API server host (default: from settings)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="API server port (default: from settings)",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # Override settings if provided
    if args.host:
        settings.api_host = args.host
    if args.port:
        settings.api_port = args.port

    # Run the application
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
