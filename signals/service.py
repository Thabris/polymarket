"""SignalService: dedup, persist, broadcast, notify, route to execution."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import timedelta

from config.settings import settings
from core.events import Event, EventType, event_bus
from core.models import AlertType, Severity
from core.timeutil import to_db, utcnow
from data.storage import Database
from signals.models import Signal, SignalGrade, SignalStatus

logger = logging.getLogger(__name__)


class SignalService:
    """Single entry point for every signal a scanner emits."""

    def __init__(self, database: Database, router) -> None:
        self.db = database
        self.router = router
        self._toast_times: deque = deque(maxlen=100)
        self._published = 0
        self._deduped = 0

    async def publish(self, signal: Signal) -> bool:
        """Publish a signal. Returns True if it was new (not deduped)."""
        if await self.db.has_signal_with_dedup(signal.dedup_key):
            self._deduped += 1
            return False

        row = await self.db.insert_signal(
            {
                "dedup_key": signal.dedup_key,
                "strategy": signal.strategy,
                "market_id": signal.market_id,
                "token_id": signal.token_id,
                "side": signal.side.value,
                "grade": signal.grade.value,
                "score": signal.score,
                "entry_price": signal.entry_price,
                "entry_zone_low": signal.entry_zone_low,
                "entry_zone_high": signal.entry_zone_high,
                "size_hint": signal.size_hint,
                "status": SignalStatus.OPEN.value,
                "expires_at": to_db(signal.expires_at),
                "snapshot": json.dumps(signal.snapshot, default=str),
            }
        )
        if row is None:  # dedup race
            self._deduped += 1
            return False
        signal.id = row.id
        self._published += 1

        await event_bus.emit(
            Event(type=EventType.SIGNAL_CREATED, data=signal.public_dict(), source="signal_service")
        )

        if self._should_toast(signal):
            await self._notify(signal)

        try:
            await self.router.on_signal(signal)
        except Exception as e:  # noqa: BLE001 — router errors must not kill scanners
            logger.error(f"router failed on signal {signal.id}: {e!r}")
        return True

    # ------------------------------------------------------------ notifications
    def _should_toast(self, signal: Signal) -> bool:
        """Notification policy (plan §Notification policy)."""
        if signal.grade == SignalGrade.C:
            return False  # shadow/flagged signals never toast, any strategy
        if signal.strategy == "manual":
            return False
        if signal.strategy == "calendar_arb":
            return True
        if signal.strategy == "theta":
            min_grade = settings.notify_theta_min_grade
            annualized = float(signal.snapshot.get("annualized_yield", 0) or 0)
            grade_ok = signal.grade.value <= min_grade  # "A" <= "A"; "B" <= "A" is False
            return grade_ok and annualized >= settings.notify_theta_min_annualized
        if signal.strategy == "news_fade":
            magnitude = abs(float(signal.snapshot.get("spike_pp", 0) or 0))
            liquidity = float(signal.snapshot.get("liquidity", 0) or 0)
            return (
                magnitude >= settings.fade_toast_pp
                and liquidity >= settings.fade_toast_min_liquidity
            )
        return False

    def _under_rate_limit(self) -> bool:
        cutoff = utcnow() - timedelta(hours=1)
        recent = sum(1 for t in self._toast_times if t > cutoff)
        return recent < settings.notify_max_toasts_per_hour

    async def _notify(self, signal: Signal) -> None:
        """Create an alert row + emit ALERT_CREATED (notifiers subscribe)."""
        if not self._under_rate_limit():
            logger.info("toast rate limit hit; signal recorded without notification")
            return
        self._toast_times.append(utcnow())
        question = signal.snapshot.get("question", signal.market_id or "")
        title = f"[{signal.strategy}] {signal.grade.value} signal"
        message = f"{question[:120]} — {signal.side.value} @ {signal.entry_price}"
        alert_row = await self.db.add_alert(
            alert_type=AlertType.ARBITRAGE.value if signal.strategy == "calendar_arb" else AlertType.SIGNAL.value,
            title=title,
            message=message,
            market_id=signal.market_id,
            severity=Severity.WARNING.value if signal.strategy == "calendar_arb" else Severity.INFO.value,
            data={"signal_id": signal.id, "strategy": signal.strategy},
        )
        await self.db.update_signal(signal.id, notified=True)
        from core.models import Alert
        await event_bus.emit(
            Event(
                type=EventType.ALERT_CREATED,
                data=Alert(
                    id=alert_row.id,
                    market_id=signal.market_id,
                    alert_type=AlertType.ARBITRAGE if signal.strategy == "calendar_arb" else AlertType.SIGNAL,
                    severity=Severity.WARNING if signal.strategy == "calendar_arb" else Severity.INFO,
                    title=title,
                    message=message,
                    data={"signal_id": signal.id, "strategy": signal.strategy},
                ),
                source="signal_service",
            )
        )

    def status(self) -> dict:
        return {"published": self._published, "deduped": self._deduped}


async def build_signal_stack(universe, runtime) -> list:
    """Assemble SignalService + PaperRouter + PaperBook (+ scanners when their
    modules exist). Called by main.py; returns [(task_name, factory), ...]."""
    from data.gamma_client import gamma_client
    from data.storage import db
    from execution.paper import PaperRouter
    from execution.paper_book import PaperBook
    from execution.risk import RiskEngine
    from notifications.desktop_notifier import ConsoleNotifier, DesktopNotifier

    from data.portfolio_client import portfolio_client
    from execution.real_portfolio import RealPortfolio

    risk_engine = RiskEngine(db)
    await risk_engine.load_overrides()
    await portfolio_client.connect()
    real_portfolio = RealPortfolio(db, risk_engine, portfolio_client)
    await real_portfolio.load_wallet()
    runtime.register("real_portfolio", real_portfolio)
    router = PaperRouter(db, risk_engine=risk_engine)
    await router.load_pending()
    service = SignalService(db, router)
    book = PaperBook(db, gamma_client, router)

    runtime.register("risk", risk_engine)
    runtime.register("signals", service)
    runtime.register("paper_router", router)
    runtime.register("paper_book", book)

    # Notifiers subscribe to ALERT_CREATED on start
    for notifier in (DesktopNotifier(), ConsoleNotifier()):
        await notifier.start()

    tasks: list = [
        ("paper_book", book.run_forever),
        ("real_portfolio", real_portfolio.run_forever),
    ]

    # Scanners attach as their modules land (Phases 3-5)
    try:
        from scanners.theta import ThetaScanner
        theta = ThetaScanner(universe, service, db)
        runtime.register("theta", theta)
        tasks.append(("theta", theta.run_forever))
    except ImportError:
        logger.info("theta scanner not present yet")

    try:
        from scanners.calendar import CalendarScanner
        calendar = CalendarScanner(universe, service, db)
        runtime.register("calendar", calendar)
        tasks.append(("calendar", calendar.run_forever))
    except ImportError:
        logger.info("calendar scanner not present yet")

    try:
        from data.stream import MarketStream
        from data.bar_recorder import BarRecorder
        stream = MarketStream()
        bars = BarRecorder(db, universe)
        runtime.register("stream", stream)
        runtime.register("bars", bars)
        universe.on_universe_change = stream.set_universe
        if universe.universe_asset_ids:
            stream.set_universe(universe.universe_asset_ids)
        tasks.append(("stream", stream.run_forever))
        tasks.append(("bars", bars.run_forever))
        try:
            from scanners.news_fade import NewsFadeScanner
            fade = NewsFadeScanner(universe, service, db)
            await fade.seed_baselines()
            runtime.register("news_fade", fade)
            tasks.append(("news_fade", fade.run_forever))
        except ImportError:
            logger.info("news-fade scanner not present yet")
    except ImportError:
        logger.info("market stream not present yet")

    return tasks
