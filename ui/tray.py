"""System tray application using pystray."""

import asyncio
import logging
import sys
import threading
from typing import Callable, Optional

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


class SystemTray:
    """
    System tray application for Polymarket Monitor.

    Provides:
    - Status indicator (running/stopped)
    - Start/Stop controls
    - Quick access to open web dashboard
    - Exit application
    """

    def __init__(
        self,
        on_start: Optional[Callable[[], None]] = None,
        on_stop: Optional[Callable[[], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        on_open_dashboard: Optional[Callable[[], None]] = None,
    ):
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_exit = on_exit
        self.on_open_dashboard = on_open_dashboard

        self._icon = None
        self._running = False
        self._monitoring = False
        self._thread: Optional[threading.Thread] = None

    @property
    def is_monitoring(self) -> bool:
        """Check if monitoring is active."""
        return self._monitoring

    def set_monitoring(self, active: bool) -> None:
        """Update monitoring status and icon."""
        self._monitoring = active
        if self._icon:
            self._icon.icon = self._create_icon(active)
            self._icon.title = self._get_title()

    def _create_icon(self, active: bool = False) -> Image.Image:
        """Create the system tray icon."""
        # Create a simple icon
        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        # Draw circle
        if active:
            # Green when monitoring
            fill_color = (46, 204, 113)  # Green
        else:
            # Gray when stopped
            fill_color = (149, 165, 166)  # Gray

        # Outer circle
        draw.ellipse([4, 4, size - 4, size - 4], fill=fill_color)

        # Inner "P" for Polymarket
        text_color = (255, 255, 255)
        # Simple P shape
        draw.rectangle([20, 16, 28, 48], fill=text_color)  # Vertical bar
        draw.rectangle([28, 16, 44, 24], fill=text_color)  # Top horizontal
        draw.rectangle([28, 24, 44, 32], fill=text_color)  # Middle part
        draw.rectangle([36, 24, 44, 32], fill=text_color)  # Curve right
        draw.ellipse([36, 16, 48, 36], fill=text_color)  # Rounded top
        draw.ellipse([28, 20, 40, 32], fill=fill_color)  # Inner cut

        return image

    def _get_title(self) -> str:
        """Get the tooltip title."""
        status = "Monitoring" if self._monitoring else "Stopped"
        return f"Polymarket Monitor - {status}"

    def _create_menu(self):
        """Create the system tray menu."""
        try:
            import pystray

            def on_start(icon, item):
                if self.on_start:
                    self.on_start()

            def on_stop(icon, item):
                if self.on_stop:
                    self.on_stop()

            def on_dashboard(icon, item):
                if self.on_open_dashboard:
                    self.on_open_dashboard()

            def on_exit(icon, item):
                self._running = False
                icon.stop()
                if self.on_exit:
                    self.on_exit()

            return pystray.Menu(
                pystray.MenuItem(
                    "Start Monitoring",
                    on_start,
                    visible=lambda item: not self._monitoring,
                ),
                pystray.MenuItem(
                    "Stop Monitoring",
                    on_stop,
                    visible=lambda item: self._monitoring,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open Dashboard", on_dashboard),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", on_exit),
            )

        except ImportError:
            logger.error("pystray not installed")
            return None

    def start(self) -> None:
        """Start the system tray in a background thread."""
        if self._running:
            return

        if sys.platform != "win32":
            logger.warning("System tray only supported on Windows")
            return

        try:
            import pystray

            self._running = True

            def run_tray():
                self._icon = pystray.Icon(
                    name="polymarket-monitor",
                    icon=self._create_icon(self._monitoring),
                    title=self._get_title(),
                    menu=self._create_menu(),
                )
                self._icon.run()

            self._thread = threading.Thread(target=run_tray, daemon=True)
            self._thread.start()
            logger.info("System tray started")

        except ImportError:
            logger.warning("pystray not installed. System tray disabled.")
        except Exception as e:
            logger.error(f"Failed to start system tray: {e}")

    def stop(self) -> None:
        """Stop the system tray."""
        self._running = False

        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

        logger.info("System tray stopped")

    def notify(self, title: str, message: str) -> None:
        """Show a notification from the system tray."""
        if self._icon:
            try:
                self._icon.notify(message, title)
            except Exception as e:
                logger.debug(f"Notification not supported: {e}")
