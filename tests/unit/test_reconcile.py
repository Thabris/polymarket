"""Tests for voiding the conflicting positions the old router let through."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from core.models import Market
from core.timeutil import utcnow
from data.storage import Database
from execution.paper import PaperRouter
from execution.paper_book import PaperBook
from execution.reconcile import find_conflicts
from signals.models import SignalStatus, SignalSide


class _Pos:
    """Stand-in for a paper_positions row."""

    def __init__(self, id, market_id, strategy, token_id, side="buy_yes", minute=0):
        self.id = id
        self.market_id = market_id
        self.strategy = strategy
        self.token_id = token_id
        self.side = side
        self.opened_at = utcnow() + timedelta(minutes=minute)


class TestFindConflicts:
    def test_opposing_outcomes_void_the_later_leg(self):
        positions = [
            _Pos(1, "m1", "news_fade", "yes-token", minute=0),
            _Pos(2, "m1", "news_fade", "no-token", minute=5),
        ]
        conflicts = find_conflicts(positions)
        assert [c.position_id for c in conflicts] == [2]
        assert conflicts[0].kept_position_id == 1

    def test_earliest_leg_is_kept_regardless_of_input_order(self):
        """The earliest position is what the fixed router would have opened."""
        positions = [
            _Pos(2, "m1", "news_fade", "no-token", minute=5),
            _Pos(1, "m1", "news_fade", "yes-token", minute=0),
        ]
        conflicts = find_conflicts(positions)
        assert [c.position_id for c in conflicts] == [2]

    def test_same_strategy_duplicate_voided(self):
        positions = [
            _Pos(1, "m1", "theta", "yes-token", minute=0),
            _Pos(2, "m1", "theta", "yes-token", minute=7),
        ]
        conflicts = find_conflicts(positions)
        assert [c.position_id for c in conflicts] == [2]
        assert conflicts[0].kind == "same_strategy"

    def test_simultaneous_fills_broken_by_id(self):
        """Both legs of a market can fill on one book update."""
        a = _Pos(11, "m1", "news_fade", "yes-token", minute=0)
        b = _Pos(12, "m1", "news_fade", "no-token", minute=0)
        conflicts = find_conflicts([b, a])
        assert [c.position_id for c in conflicts] == [12]

    def test_three_legs_keep_one(self):
        positions = [
            _Pos(1, "m1", "news_fade", "yes-token", minute=0),
            _Pos(2, "m1", "news_fade", "no-token", minute=1),
            _Pos(3, "m1", "news_fade", "yes-token", minute=2),
        ]
        assert sorted(c.position_id for c in find_conflicts(positions)) == [2, 3]

    def test_same_token_across_strategies_allowed(self):
        """Two strategies agreeing on the same outcome is not a conflict."""
        positions = [
            _Pos(1, "m1", "theta", "yes-token", minute=0),
            _Pos(2, "m1", "news_fade", "yes-token", minute=5),
        ]
        assert find_conflicts(positions) == []

    def test_opposing_across_strategies_voided(self):
        positions = [
            _Pos(1, "m1", "theta", "yes-token", minute=0),
            _Pos(2, "m1", "news_fade", "no-token", minute=5),
        ]
        conflicts = find_conflicts(positions)
        assert [c.position_id for c in conflicts] == [2]
        assert conflicts[0].kind == "opposing_outcome"

    def test_distinct_markets_never_conflict(self):
        positions = [
            _Pos(1, "m1", "news_fade", "yes-token", minute=0),
            _Pos(2, "m2", "news_fade", "no-token", minute=1),
        ]
        assert find_conflicts(positions) == []

    def test_clean_book_yields_nothing(self):
        assert find_conflicts([_Pos(1, "m1", "theta", "yes-token")]) == []

    def test_result_matches_router_rule(self):
        """Whatever survives the replay must itself be conflict-free."""
        positions = [
            _Pos(1, "m1", "news_fade", "yes-token", minute=0),
            _Pos(2, "m1", "news_fade", "no-token", minute=1),
            _Pos(3, "m1", "theta", "no-token", minute=2),
            _Pos(4, "m2", "theta", "yes-token", minute=3),
        ]
        voided = {c.position_id for c in find_conflicts(positions)}
        survivors = [p for p in positions if p.id not in voided]
        assert find_conflicts(survivors) == []


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


@pytest_asyncio.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.init_db()
    await database.upsert_markets([_market(id="m1")])
    yield database
    await database.engine.dispose()


async def _position(db, token_id, fees=0.0, strategy="news_fade"):
    signal = await db.insert_signal(
        {
            "dedup_key": f"fade:m1:{token_id}",
            "strategy": strategy,
            "market_id": "m1",
            "token_id": token_id,
            "side": SignalSide.BUY_YES.value,
            "grade": "B",
            "status": SignalStatus.FILLED.value,
            "entry_price": 0.5,
        }
    )
    return await db.insert_paper_position(
        {
            "signal_id": signal.id,
            "strategy": strategy,
            "market_id": "m1",
            "token_id": token_id,
            "side": "buy_yes",
            "entry_price": 0.5,
            "size": 200.0,
            "fees_paid": fees,
        }
    )


class TestVoidPosition:
    @pytest.mark.asyncio
    async def test_void_books_only_the_fees(self, db):
        pos = await _position(db, "yes-token", fees=1.25)
        router = PaperRouter(db)
        assert await router.void_position(pos.id) is True

        closed = await db.get_paper_positions(status="closed")
        assert len(closed) == 1
        assert closed[0].exit_price == pytest.approx(0.5)  # exits at entry
        assert closed[0].pnl == pytest.approx(-1.25)
        assert closed[0].exit_reason == "voided_conflict"

    @pytest.mark.asyncio
    async def test_void_is_idempotent(self, db):
        pos = await _position(db, "yes-token")
        router = PaperRouter(db)
        assert await router.void_position(pos.id) is True
        assert await router.void_position(pos.id) is False

    @pytest.mark.asyncio
    async def test_voided_excluded_from_win_rate_and_calibration(self, db):
        """A void is neither a win nor a loss, so it must not move win rate."""
        winner = await _position(db, "yes-token")
        voided = await _position(db, "no-token", fees=2.0)
        router = PaperRouter(db)
        await router.close_position(winner.id, "resolution_win", 1.0)
        await router.void_position(voided.id)

        stats = await PaperBook(db, MagicMock(), router).stats()
        entry = stats["strategies"]["news_fade"]
        assert entry["closed_positions"] == 1
        assert entry["voided_positions"] == 1
        assert entry["win_rate"] == pytest.approx(1.0)  # not 0.5
        # one calibration sample, from the resolved position only
        assert sum(c["n"] for c in entry["calibration"].values()) == 1
        # the fee the bug cost is still charged against P&L
        assert entry["total_pnl"] == pytest.approx(100.0 - 2.0)

    @pytest.mark.asyncio
    async def test_voided_position_frees_the_market(self, db):
        """After voiding, the market must accept a position again."""
        pos = await _position(db, "yes-token")
        assert await db.conflicting_open_position("m1", "no-token", "news_fade") is True
        await PaperRouter(db).void_position(pos.id)
        assert await db.conflicting_open_position("m1", "no-token", "news_fade") is False
