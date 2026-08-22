"""Desktop notification delivery using winotify (Windows 10/11 toasts)."""

import asyncio
import logging
from typing import Optional

from config.settings import settings
from core.models import Alert, Severity
from notifications.base_notifier import BaseNotifier

logger = logging.getLogger(__name__)


class DesktopNotifier(BaseNotifier):
    """Windows toast notifications via winotify.

    winotify builds the toast synchronously (fast) — run it in an executor
    to keep the event loop clean. Clicking a toast opens the signals page.
    """

    def __init__(self):
        super().__init__(name="desktop")
        self._available: Optional[bool] = None

    def _ensure_available(self) -> bool:
        if self._available is None:
            try:
                import winotify  # noqa: F401
                self._available = True
                logger.info("Desktop notifications initialized (winotify)")
            except ImportError:
                self._available = False
                logger.warning("winotify not installed; desktop notifications disabled")
        return self._available

    async def send(self, alert: Alert) -> bool:
        """Send a Windows toast for an alert."""
        if not self._ensure_available():
            return False
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._show_toast, alert)
            return True
        except Exception as e:  # noqa: BLE001 — notification failure is non-fatal
            logger.error(f"Toast failed: {e}")
            return False

    def _show_toast(self, alert: Alert) -> None:
        from winotify import Notification, audio

        toast = Notification(
            app_id="Polymarket Scanner",
            title=alert.title,
            msg=alert.message[:200],
            launch=f"http://{settings.api_host}:{settings.api_port}/static/signals.html",
        )
        if alert.severity in (Severity.WARNING, Severity.CRITICAL):
            toast.set_audio(audio.Default, loop=False)
        toast.show()


class ConsoleNotifier(BaseNotifier):
    """Console fallback notifier (always available)."""

    SEVERITY_MARKS = {
        Severity.INFO: "[i]",
        Severity.WARNING: "[!]",
        Severity.CRITICAL: "[!!]",
    }

    def __init__(self):
        super().__init__(name="console")

    async def send(self, alert: Alert) -> bool:
        """Log the alert to the console."""
        mark = self.SEVERITY_MARKS.get(alert.severity, "[?]")
        logger.info(f"{mark} ALERT: {alert.title} — {alert.message}")
        return True
