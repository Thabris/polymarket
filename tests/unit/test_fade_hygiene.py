"""Regression tests for the two news-fade book-hygiene bugs.

1. Duplicate side: the scanner faded a market up, then faded the retrace it had
   itself predicted, and the router let both positions open because buy_yes and
   buy_no carry different token ids.
2. Stale signals: a signal whose market resolved inside its entry window stayed
   `open` until its time expiry, keeping a resting zone order armed and
   inflating the fill-rate denominator.
"""

from collections import deque
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from core.events import Event, EventType
from core.models import Market
from core.timeutil import to_db, utcnow
from data.storage import Database
from execution.paper import PaperRouter
from execution.paper_book import PaperBook
from scanners.news_fade import NewsFadeScanner
from signals.models import Signal, SignalGrade, SignalSide, SignalStatus


def _market(**kwargs) -> Market:
    base = {
        "id": "m1",
        "condition_id": "0x1",
        "question": "Will the thing happen?",
        "liquidity": 200000.0,
        "category": "politics",
        "token_id_yes": "yes-token",
        "token_id_no": "no-token",
        "end_date": utcnow() + timedelta(days=30),
    }
    base.update(kwargs)
    return Market(**base)


def _event(asset_id: str, mid: float) -> Event:
    return Event(type=EventType.BOOK_TOP, data={"asset_id": asset_id, "mid": mid})


def _signal_row(market_id="m1", status=SignalStatus.OPEN.value, zone=True, **extra) -> dict:
    row = {
        "dedup_key": f"fade:{market_id}:up:1",
        "strategy": "news_fade",
        "market_id": market_id,
        "token_id": "no-token",
        "side": SignalSide.BUY_NO.value,
        "grade": "B",
        "status": status,
        "entry_price": 0.4,
    }
    if zone:
        row["entry_zone_low"] = 0.38
        row["entry_zone_high"] = 0.45
        # entry window still wide open — only resolution should end it
        row["expires_at"] = to_db(utcnow() + timedelta(hours=2))
    row.update(extra)
    return row


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.init_db()
    # signals.market_id is a FK and foreign_keys=ON, so the markets must exist
    await database.upsert_markets([_market(id=mid) for mid in ("m0", "m1", "m2")])
    yield database
    await database.engine.dispose()


# --------------------------------------------------------------- duplicate side
class TestDirectionLock:
    def _scanner(self) -> NewsFadeScanner:
        universe = MagicMock()
        universe.market_for_asset.return_value = _market()
        return NewsFadeScanner(universe, MagicMock(), MagicMock())

    @pytest.mark.asyncio
    async def test_opposite_direction_suppressed_after_fade(self):
        """The exact overnight failure: fade the up-spike, then fade the retrace."""
        scanner = self._scanner()
        out = []
        for price in [0.35] * 10 + [0.40, 0.50, 0.62]:
            out = await scanner.on_stream_event(_event("yes-token", price))
        assert len(out) == 1
        assert out[0].side == SignalSide.BUY_NO
        assert out[0].snapshot["direction"] == "up"

        # The retrace the first signal predicted now reads as a down-spike:
        # below the 0.485 midpoint of the window, spike_down wins and the
        # scanner would emit buy_yes against its own live buy_no.
        emitted = []
        for price in [0.48, 0.40, 0.36]:
            emitted += await scanner.on_stream_event(_event("yes-token", price))
        assert emitted == [], "opposite-direction fade must be suppressed by the lock"

        # sanity: without the lock that same retrace does produce a buy_yes
        scanner._locks.clear()
        rescued = await scanner.on_stream_event(_event("yes-token", 0.36))
        assert len(rescued) == 1
        assert rescued[0].side == SignalSide.BUY_YES
        assert rescued[0].snapshot["direction"] == "down"

    @pytest.mark.asyncio
    async def test_lock_expires(self):
        scanner = self._scanner()
        for price in [0.35] * 10 + [0.62]:
            await scanner.on_stream_event(_event("yes-token", price))
        assert "m1" in scanner._locks
        direction, _ = scanner._locks["m1"]
        scanner._locks["m1"] = (direction, utcnow() - timedelta(seconds=1))
        assert scanner._direction_locked("m1", "down") is False

    def test_same_direction_not_blocked_by_lock(self):
        """Only the opposite side is locked; same-direction churn is the dedup
        key's job, not the lock's."""
        scanner = self._scanner()
        scanner._locks["m1"] = ("up", utcnow() + timedelta(hours=1))
        assert scanner._direction_locked("m1", "up") is False
        assert scanner._direction_locked("m1", "down") is True

    @pytest.mark.asyncio
    async def test_locks_reseed_from_db(self, db):
        """A restart must not forget which way a market was already faded."""
        await db.insert_signal(_signal_row(zone=False))
        scanner = NewsFadeScanner(MagicMock(), MagicMock(), db)
        await scanner.seed_locks()
        assert scanner._direction_locked("m1", "down") is True
        assert scanner._direction_locked("m1", "up") is False


class TestPositionConflict:
    async def _open(self, db, strategy, token_id, market_id="m1"):
        parent = await db.insert_signal(
            _signal_row(market_id=market_id, status=SignalStatus.FILLED.value, zone=False)
            | {"dedup_key": f"{strategy}:{market_id}:{token_id}", "strategy": strategy}
        )
        return await db.insert_paper_position(
            {
                "signal_id": parent.id,
                "strategy": strategy,
                "market_id": market_id,
                "token_id": token_id,
                "side": "buy_yes",
                "entry_price": 0.5,
                "size": 100.0,
                "fees_paid": 0.0,
            }
        )

    @pytest.mark.asyncio
    async def test_opposite_token_conflicts(self, db):
        """The token-keyed check missed this: different tokens, same market."""
        await self._open(db, "news_fade", "yes-token")
        assert await db.conflicting_open_position("m1", "no-token", "news_fade") is True

    @pytest.mark.asyncio
    async def test_same_token_same_strategy_conflicts(self, db):
        await self._open(db, "news_fade", "yes-token")
        assert await db.conflicting_open_position("m1", "yes-token", "news_fade") is True

    @pytest.mark.asyncio
    async def test_opposite_token_conflicts_across_strategies(self, db):
        """Holding both outcomes is a wash whoever's thesis it was."""
        await self._open(db, "theta", "yes-token")
        assert await db.conflicting_open_position("m1", "no-token", "news_fade") is True

    @pytest.mark.asyncio
    async def test_same_token_other_strategy_allowed(self, db):
        await self._open(db, "theta", "yes-token")
        assert await db.conflicting_open_position("m1", "yes-token", "news_fade") is False

    @pytest.mark.asyncio
    async def test_other_market_allowed(self, db):
        await self._open(db, "news_fade", "yes-token", market_id="m2")
        assert await db.conflicting_open_position("m1", "no-token", "news_fade") is False

    @pytest.mark.asyncio
    async def test_closed_position_does_not_conflict(self, db):
        pos = await self._open(db, "news_fade", "yes-token")
        await db.close_paper_position(
            pos.id, exit_price=1.0, exit_reason="resolution_win", pnl=50.0
        )
        assert await db.conflicting_open_position("m1", "no-token", "news_fade") is False

    @pytest.mark.asyncio
    async def test_router_skips_conflicting_signal(self, db):
        router = PaperRouter(db)
        await self._open(db, "news_fade", "yes-token")
        signal = Signal(
            id=1,
            strategy="news_fade",
            market_id="m1",
            token_id="no-token",
            side=SignalSide.BUY_NO,
            grade=SignalGrade.B,
            entry_price=0.4,
            dedup_key="fade:m1:up:1",
            snapshot={"fee_rate": 0.0},
        )
        await router.on_signal(signal)
        positions = await db.get_paper_positions(status="open")
        assert len(positions) == 1, "the offsetting position must not open"
        assert router._skips == 1

    @pytest.mark.asyncio
    async def test_resting_order_rechecked_at_fill_time(self, db):
        """Two opposite zone orders can both arm before either fills."""
        row = await db.insert_signal(_signal_row())
        router = PaperRouter(db)
        await router.load_pending()
        assert router.pending_signal_ids() == {row.id}
        # a position on the other outcome opens while this order rests
        await self._open(db, "news_fade", "yes-token")

        # zone is in NO space (0.38-0.45), watched inverted on the YES book
        await router._check_zones(_event("no-token", 0.40))

        assert await db.get_paper_positions(status="open") != []
        assert len(await db.get_paper_positions(status="open")) == 1
        after = await db.get_signal(row.id)
        assert after.status == SignalStatus.CANCELLED.value


# --------------------------------------------------------------- stale signals
class TestResolvedMarketCancellation:
    @pytest.mark.asyncio
    async def test_open_signal_cancelled_when_market_resolves(self, db):
        """A signal whose market closed inside its entry window is cancelled,
        not left `open` until its time expiry."""
        row = await db.insert_signal(_signal_row())
        book = PaperBook(db, MagicMock(), PaperRouter(db))
        book._refresh_markets = AsyncMock(
            return_value={"m1": _market(closed=True, resolved_outcome="0")}
        )
        await book.check_once()

        after = await db.get_signal(row.id)
        assert after.status == SignalStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_resting_zone_order_dropped_on_resolution(self, db):
        row = await db.insert_signal(_signal_row())
        router = PaperRouter(db)
        await router.load_pending()
        assert router.pending_signal_ids() == {row.id}
        assert router.pending_market_ids() == {"m1"}

        book = PaperBook(db, MagicMock(), router)
        book._refresh_markets = AsyncMock(
            return_value={"m1": _market(closed=True, resolved_outcome="0")}
        )
        await book.check_once()

        assert router.pending_signal_ids() == set()
        after = await db.get_signal(row.id)
        assert after.status == SignalStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_signal_only_market_is_refreshed(self, db):
        """A market with a resting order but no position must still be checked."""
        await db.insert_signal(_signal_row())
        router = PaperRouter(db)
        await router.load_pending()
        book = PaperBook(db, MagicMock(), router)
        book._refresh_markets = AsyncMock(return_value={})
        await book.check_once()
        book._refresh_markets.assert_awaited_once()
        assert book._refresh_markets.await_args.args[0] == {"m1"}

    @pytest.mark.asyncio
    async def test_open_market_signal_left_alone(self, db):
        row = await db.insert_signal(_signal_row())
        router = PaperRouter(db)
        await router.load_pending()
        book = PaperBook(db, MagicMock(), router)
        book._refresh_markets = AsyncMock(return_value={"m1": _market(closed=False)})
        await book.check_once()

        after = await db.get_signal(row.id)
        assert after.status == SignalStatus.OPEN.value
        assert router.pending_signal_ids() == {row.id}

    @pytest.mark.asyncio
    async def test_cancelled_signals_excluded_from_fill_rate(self, db):
        """Two emitted, one cancelled, one filled -> 100%, not 50%."""
        for i, status in enumerate((SignalStatus.CANCELLED.value, SignalStatus.FILLED.value)):
            await db.insert_signal(_signal_row(market_id=f"m{i}", status=status, zone=False))
        book = PaperBook(db, MagicMock(), PaperRouter(db))
        stats = await book.stats()
        assert stats["strategies"]["news_fade"]["fill_rate"] == pytest.approx(1.0)
