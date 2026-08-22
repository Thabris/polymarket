#!/usr/bin/env python3
"""Polymarket Scanner — daemon entry point.

Runs migrations, wires all components under a TaskSupervisor, serves the API,
and shuts down gracefully in reverse order.

Usage:
    python main.py              # full daemon (sync + stream + scanners + API)
    python main.py --api-only   # API + market sync only (UI development)
"""

import argparse
import asyncio
import logging
import logging.handlers
import subprocess
import sys
from pathlib import Path
from typing import Awaitable, Callable, Optional

import uvicorn

from config.settings import settings

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


def configure_logging() -> None:
    """Console + rotating file logging."""
    (BASE_DIR / "var").mkdir(exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    file_handler = logging.handlers.RotatingFileHandler(
        BASE_DIR / "var" / "daemon.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def run_migrations() -> None:
    """Run `alembic upgrade head` in a subprocess (env.py uses asyncio.run,
    so it cannot be invoked from inside a running event loop)."""
    logger.info("Running database migrations...")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}")
        raise SystemExit(1)
    logger.info("Migrations up to date")


class TaskSupervisor:
    """Supervises long-running tasks: restart-on-crash with backoff, status."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self._tasks: dict[str, asyncio.Task] = {}
        self._stopping = False

    def spawn(self, name: str, factory: Callable[[], Awaitable[None]]) -> None:
        """Start a supervised task from an async factory."""
        self.runtime.set_task_status(name, crashes=0, last_error=None, running=True)
        self._tasks[name] = asyncio.create_task(self._supervise(name, factory), name=name)

    async def _supervise(self, name: str, factory: Callable[[], Awaitable[None]]) -> None:
        crashes = 0
        delay = 5.0
        while not self._stopping:
            try:
                await factory()
                if not self._stopping:
                    logger.warning(f"task {name} returned unexpectedly; restarting")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — supervisor must survive anything
                crashes += 1
                logger.error(f"task {name} crashed ({crashes}): {e!r}")
                self.runtime.set_task_status(name, crashes=crashes, last_error=repr(e))
            if self._stopping:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)

    async def stop(self) -> None:
        """Cancel all tasks with a per-task timeout."""
        self._stopping = True
        for name, task in reversed(list(self._tasks.items())):
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception:  # noqa: BLE001
                pass
            self.runtime.set_task_status(name, running=False)


async def run_daemon(api_only: bool) -> None:
    """Wire components and run until interrupted."""
    from core.events import event_bus
    from core.runtime import runtime
    from data.gamma_client import gamma_client
    from data.storage import db
    from data.universe import UniverseManager

    runtime.register("daemon", {"api_only": api_only})

    from data.clob_client import clob_client

    await db.init_db()
    await event_bus.start()
    await gamma_client.connect()
    await clob_client.connect()

    universe = UniverseManager(gamma_client, db)
    runtime.register("universe", universe)

    # The initial sync MUST precede component build: news-fade baseline seeding
    # and the stream universe pre-seed both read universe.markets().
    logger.info("Initial market sync (may take ~30s)...")
    try:
        await universe.refresh()
    except Exception as e:  # noqa: BLE001 — boot must survive a bad first sync
        logger.error(f"initial sync failed (continuing, will retry on loop): {e}")

    # SignalService, PaperRouter, MarketStream, BarRecorder, and the scanners
    # attach here. Import lazily so phases stay optional.
    components = await _build_optional_components(api_only, universe, runtime)

    supervisor = TaskSupervisor(runtime)
    runtime.register("supervisor", supervisor)
    supervisor.spawn("universe", universe.run_forever)
    for name, factory in components:
        supervisor.spawn(name, factory)

    # Uvicorn as a supervised task; signal handling stays with us
    from api.app import app
    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    api_task = asyncio.create_task(server.serve(), name="api")

    logger.info(f"API at http://{settings.api_host}:{settings.api_port} — Ctrl+C to stop")

    stop = asyncio.Event()
    try:
        await stop.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down...")
        server.should_exit = True
        try:
            await asyncio.wait_for(api_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            api_task.cancel()
        except BaseException as e:  # noqa: BLE001 — uvicorn may store SystemExit
            # (e.g. port already bound); the remaining cleanup MUST still run
            logger.error(f"api task failed: {e!r}")
        await supervisor.stop()
        for stopper in _component_stoppers(runtime):
            try:
                await stopper()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"shutdown step failed: {e}")
        await clob_client.close()
        await gamma_client.close()
        await event_bus.stop()
        await db.close()
        logger.info("Shutdown complete")


async def _build_optional_components(api_only: bool, universe, runtime) -> list:
    """Attach phase-2+ components when their modules exist. Returns
    [(task_name, factory), ...] for the supervisor."""
    tasks: list = []
    if api_only:
        return tasks
    # Phase 2+: signals/execution; Phase 4: stream + bars + fade; Phase 3/5: scanners.
    try:
        from signals.service import build_signal_stack  # noqa: PLC0415
    except ImportError:
        return tasks
    stack_tasks = await build_signal_stack(universe, runtime)
    tasks.extend(stack_tasks)
    return tasks


def _component_stoppers(runtime) -> list:
    """Async stop() callables of registered components that have one."""
    stoppers = []
    for name in ("stream", "bars"):
        comp = runtime.get(name)
        stop = getattr(comp, "stop", None)
        if comp is not None and callable(stop):
            stoppers.append(stop)
    return stoppers


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket Scanner daemon")
    parser.add_argument("--api-only", action="store_true", help="API + sync only")
    args = parser.parse_args()

    configure_logging()
    run_migrations()

    try:
        asyncio.run(run_daemon(api_only=args.api_only))
    except KeyboardInterrupt:
        # asyncio.run already ran the finally-block cleanup via cancellation
        logging.getLogger(__name__).info("Interrupted — exited")


if __name__ == "__main__":
    main()
