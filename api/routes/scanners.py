"""Scanner status API endpoints."""

from fastapi import APIRouter

from core.runtime import runtime
from data.storage import db

router = APIRouter()


@router.get("/status")
async def scanners_status():
    """Per-scanner run history + live component status."""
    runs = await db.get_last_scanner_runs(per_scanner=5)
    return {
        "runs": {
            scanner: [
                {
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                    "ok": r.ok,
                    "items_scanned": r.items_scanned,
                    "signals_emitted": r.signals_emitted,
                    "error": r.error,
                }
                for r in scanner_runs
            ]
            for scanner, scanner_runs in runs.items()
        },
        "runtime": runtime.status(),
    }
