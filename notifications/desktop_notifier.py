"""Desktop notification delivery using win10toast."""

import asyncio
import logging
import sys
from typing import Optional

from core.models import Alert, Severity
from notifications.base_notifier import BaseNotifier

logger = logging.getLogger(__name__)


class DesktopNotifier(BaseNotifier):
    """
    Desktop notification delivery using Windows toast notifications.

    Uses win10toast library for Windows 10/11 toast notifications.
    Falls back to logging on non-Windows platforms.
    """

    def __init__(self, app_name: str = "Polymarket Monitor", **kwargs):
        super().__init__(name="desktop", **kwargs)
        self.app_name = app_name
        self._toaster = None
        self._initialized = False

    async def start(self) -> None:
        """Start the notifier and initialize toaster."""
        await super().start()

        if sys.platform == "win32":
            try:
                from win10toast import ToastNotifier

                self._toaster = ToastNotifier()
                self._initialized = True
                logger.info("Desktop notifications initialized (win10toast)")
            except ImportError:
                logger.warning(
                    "win10toast not installed. Desktop notifications disabled."
                )
            except Exception as e:
                logger.warning(f"Failed to initialize win10toast: {e}")
        else:
            logger.info("Desktop notifications not supported on this platform")

    async def send(self, alert: Alert) -> bool:
        """Send a desktop notification."""
        if not self._initialized or self._toaster is None:
            # Fall back to logging
            logger.info(f"[ALERT] {alert.title}: {alert.message}")
            return True

        try:
            # Get icon based on severity
            icon_path = self._get_icon_path(alert.severity)

            # Determine duration based on severity
            duration = self._get_duration(alert.severity)

            # Run in thread to avoid blocking
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._toaster.show_toast(
                    title=alert.title,
                    msg=alert.message[:256],  # Windows has message length limits
                    icon_path=icon_path,
                    duration=duration,
                    threaded=True,
                ),
            )

            return True

        except Exception as e:
            logger.error(f"Failed to send desktop notification: {e}")
            return False

    def _get_icon_path(self, severity: Severity) -> Optional[str]:
        """Get icon path based on alert severity."""
        # For now, return None to use default icon
        # In future, could return custom icons per severity
        return None

    def _get_duration(self, severity: Severity) -> int:
        """Get notification duration in seconds based on severity."""
        if severity == Severity.CRITICAL:
            return 10
        elif severity == Severity.WARNING:
            return 7
        return 5


class ConsoleNotifier(BaseNotifier):
    """
    Console/log-based notification for debugging and non-Windows platforms.
    """

    def __init__(self, **kwargs):
        super().__init__(name="console", **kwargs)

    async def send(self, alert: Alert) -> bool:
        """Log the alert to console."""
        severity_emoji = {
            Severity.INFO: "ℹ️",
            Severity.WARNING: "⚠️",
            Severity.CRITICAL: "🚨",
        }

        emoji = severity_emoji.get(alert.severity, "ℹ️")
        print(f"\n{emoji} [{alert.severity.value.upper()}] {alert.title}")
        print(f"   {alert.message}")

        if alert.market_id:
            print(f"   Market: {alert.market_id}")

        return True
