"""Runtime component registry.

Components register themselves (or are registered by main.py) so API routes
can report real state without importing module globals that the daemon may
have bypassed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from core.timeutil import utcnow


class Runtime:
    """Registry of live components and their status callables."""

    def __init__(self) -> None:
        self.started_at: datetime = utcnow()
        self._components: dict[str, Any] = {}
        self._task_status: dict[str, dict] = {}

    def register(self, name: str, component: Any) -> None:
        """Register a component under a name."""
        self._components[name] = component

    def get(self, name: str) -> Optional[Any]:
        """Get a registered component."""
        return self._components.get(name)

    def set_task_status(self, name: str, **fields) -> None:
        """Update supervised-task status fields (crashes, last_error...)."""
        entry = self._task_status.setdefault(name, {})
        entry.update(fields)

    def status(self) -> dict:
        """Aggregate status of all components exposing a status() method."""
        out: dict = {
            "started_at": self.started_at.isoformat(),
            "uptime_seconds": (utcnow() - self.started_at).total_seconds(),
            "tasks": self._task_status,
            "components": {},
        }
        for name, comp in self._components.items():
            status_fn = getattr(comp, "status", None)
            if callable(status_fn):
                try:
                    out["components"][name] = status_fn()
                except Exception as e:  # noqa: BLE001 — status must never raise
                    out["components"][name] = {"error": repr(e)}
        return out


# Global runtime instance (populated by main.py; api reads it)
runtime = Runtime()
