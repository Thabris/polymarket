#!/usr/bin/env python3
"""Smoke test: show one Windows toast via winotify."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models import Alert, AlertType, Severity  # noqa: E402
from notifications.desktop_notifier import DesktopNotifier  # noqa: E402


async def main() -> None:
    notifier = DesktopNotifier()
    ok = await notifier.send(
        Alert(
            alert_type=AlertType.SIGNAL,
            severity=Severity.WARNING,
            title="[test] Polymarket Scanner",
            message="Toast pipeline works — clicking opens the signals page.",
        )
    )
    print("toast sent" if ok else "toast FAILED (is winotify installed?)")


if __name__ == "__main__":
    asyncio.run(main())
