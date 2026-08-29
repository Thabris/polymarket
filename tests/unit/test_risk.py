"""RiskEngine tests: VaR math, correlation semantics, limit gate."""

import pytest
import pytest_asyncio

from core.models import Market
from data.storage import Database
from execution.risk import RiskEngine


def _entry(strategy="theta", market_id="m1", group="ev:1", notional=100.0,
           entry=0.5, p_win=0.5, fees=0.0):
    return {
        "strategy": strategy,
        "market_id": market_id,
        "group": group,
        "notional": notional,
        "size": notional / entry,
        "entry": entry,
        "fees": fees,
        "p_win": p_win,
    }


class TestMcVar:
    def test_empty_book(self):
        var = RiskEngine.mc_var([])
        assert var["var"] == 0.0
        assert var["worst_case"] == 0.0

    def test_single_coinflip_var_is_premium(self):
        # 50/50 position: losing is far more likely than 5%, so the 95% VaR
        # is the full premium
        var = RiskEngine.mc_var([_entry(p_win=0.5, notional=100)], sims=4000, seed=7)
        assert var["var"] == pytest.approx(100.0, abs=1)
        assert var["worst_case"] == pytest.approx(100.0)

    def test_near_certain_win_var_zero(self):
        # p_win = 0.99: loss probability 1% < 5% tail -> VaR95 ~ 0 (the 95th
        # percentile outcome is a win)
        var = RiskEngine.mc_var([_entry(p_win=0.99, entry=0.99, notional=99)], sims=4000, seed=7)
        assert var["var"] == pytest.approx(0.0, abs=1)

    def test_comonotone_event_raises_var(self):
        # two positions, p_win 0.9 each, premium 100 each.
        # independent events: P(both lose) = 1% < 5%, P(>=1 loses) = 19% >= 5%
        #   -> VaR95 = one premium
        # same event (comonotone): P(both lose together) = 10% >= 5%
        #   -> VaR95 = both premiums
        independent = [
            _entry(market_id="a", group="ev:a", p_win=0.9, entry=0.9, notional=90),
            _entry(market_id="b", group="ev:b", p_win=0.9, entry=0.9, notional=90),
        ]
        together = [
            _entry(market_id="a", group="ev:x", p_win=0.9, entry=0.9, notional=90),
            _entry(market_id="b", group="ev:x", p_win=0.9, entry=0.9, notional=90),
        ]
        var_ind = RiskEngine.mc_var(independent, sims=6000, seed=11)
        var_tog = RiskEngine.mc_var(together, sims=6000, seed=11)
        # independent 95th-pct scenario: one leg loses (-90), the other wins (+10)
        assert var_ind["var"] == pytest.approx(80.0, abs=4)
        assert var_tog["var"] > var_ind["var"]
        # comonotone: both lose together with 10% probability -> full 180
        assert var_tog["var"] == pytest.approx(180.0, abs=2)

    def test_fees_are_sunk_losses(self):
        no_fees = RiskEngine.mc_var([_entry(p_win=0.5, notional=100)], sims=2000, seed=3)
        with_fees = RiskEngine.mc_var([_entry(p_win=0.5, notional=100, fees=5)], sims=2000, seed=3)
        assert with_fees["var"] == pytest.approx(no_fees["var"] + 5, abs=1)
        assert with_fees["worst_case"] == pytest.approx(105.0)


@pytest_asyncio.fixture
async def mem_db():
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.init_db()
    yield db
    await db.close()


async def _seed_position(db, market_id="m1", event_id="ev1", strategy="theta",
                         entry=0.5, size=200.0, token="yes"):
    market = Market(
        id=market_id, condition_id="0x" + market_id, question=f"Q {market_id}?",
        event_id=event_id, token_id_yes=f"{market_id}-yes", token_id_no=f"{market_id}-no",
        best_bid=entry - 0.01, best_ask=entry + 0.01,
    )
    await db.upsert_markets([market])
    signal = await db.insert_signal({
        "dedup_key": f"t:{market_id}:{strategy}:{size}", "strategy": strategy,
        "market_id": market_id, "token_id": f"{market_id}-{token}",
        "side": "buy_yes", "grade": "B", "status": "filled",
    })
    await db.insert_paper_position({
        "signal_id": signal.id, "strategy": strategy, "market_id": market_id,
        "token_id": f"{market_id}-{token}", "side": "buy_yes",
        "entry_price": entry, "size": size, "fees_paid": 0.0,
    })


class TestAllow:
    @pytest.mark.asyncio
    async def test_manual_kill_blocks_everything(self, mem_db):
        engine = RiskEngine(mem_db)
        await engine.set_manual_kill(True)
        allowed, reason = await engine.allow("theta", "m1", 100.0)
        assert not allowed and reason == "manual_kill"

    @pytest.mark.asyncio
    async def test_total_deployed_cap(self, mem_db):
        engine = RiskEngine(mem_db)
        engine.limits["total_deployed"] = 150.0
        await _seed_position(mem_db, "m1", entry=0.5, size=200)  # $100 deployed
        allowed, reason = await engine.allow("theta", "m2", 100.0)
        assert not allowed and reason.startswith("total_deployed")
        allowed, _ = await engine.allow("theta", "m2", 40.0)
        assert allowed

    @pytest.mark.asyncio
    async def test_per_event_correlation_cap(self, mem_db):
        engine = RiskEngine(mem_db)
        engine.limits["per_event_deployed"] = 150.0
        await _seed_position(mem_db, "m1", event_id="shared", entry=0.5, size=200)
        m2 = Market(id="m2", condition_id="0xm2", question="Q m2?", event_id="shared",
                    token_id_yes="m2-yes", token_id_no="m2-no", best_bid=0.49, best_ask=0.51)
        await mem_db.upsert_markets([m2])
        allowed, reason = await engine.allow("news_fade", "m2", 100.0)
        assert not allowed and reason.startswith("per_event_deployed")

    @pytest.mark.asyncio
    async def test_var_cap(self, mem_db):
        engine = RiskEngine(mem_db)
        engine.limits["var95"] = 120.0
        await _seed_position(mem_db, "m1", entry=0.5, size=200)  # coinflip, VaR ~100
        # a second coinflip pushes VaR95 well past 120
        allowed, reason = await engine.allow("theta", "m9", 100.0, p_win=0.5)
        assert not allowed and reason.startswith("var95")

    @pytest.mark.asyncio
    async def test_daily_loss_kill(self, mem_db):
        engine = RiskEngine(mem_db)
        engine.limits["daily_loss"] = 50.0
        await _seed_position(mem_db, "m1", entry=0.5, size=200)
        positions = await mem_db.get_paper_positions(status="open")
        await mem_db.close_paper_position(positions[0].id, exit_price=0.0,
                                          exit_reason="resolution_loss", pnl=-100.0)
        allowed, reason = await engine.allow("theta", "m2", 10.0)
        assert not allowed and reason.startswith("daily_loss")

    @pytest.mark.asyncio
    async def test_limit_override_persists(self, mem_db):
        engine = RiskEngine(mem_db)
        await engine.set_limit("total_deployed", 1234.0)
        fresh = RiskEngine(mem_db)
        await fresh.load_overrides()
        assert fresh.limits["total_deployed"] == 1234.0

    @pytest.mark.asyncio
    async def test_unknown_limit_rejected(self, mem_db):
        engine = RiskEngine(mem_db)
        with pytest.raises(KeyError):
            await engine.set_limit("nonsense", 1.0)


class TestSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_shape(self, mem_db):
        engine = RiskEngine(mem_db)
        await _seed_position(mem_db, "m1", strategy="theta", entry=0.5, size=200)
        snap = await engine.snapshot()
        assert snap["deployed"]["total"]["current"] == pytest.approx(100.0)
        assert "theta" in snap["deployed"]["by_strategy"]
        assert snap["var"]["var"] >= 0
        assert snap["open_positions"] == 1
        assert not snap["kill_switch"]["manual"]


class TestReviewFixes:
    @pytest.mark.asyncio
    async def test_daily_pnl_counts_old_opened_positions(self, mem_db):
        """The kill switch must see resolution closes of long-held positions."""
        engine = RiskEngine(mem_db)
        engine.limits["daily_loss"] = 50.0
        await _seed_position(mem_db, "m1", entry=0.5, size=200)
        positions = await mem_db.get_paper_positions(status="open")
        # backdate opened_at far into the past, then close today at a loss
        from sqlalchemy import update as sa_update
        from db.models import PaperPositionModel
        from datetime import timedelta as td
        from core.timeutil import utcnow, to_db
        async with mem_db.session() as session:
            await session.execute(
                sa_update(PaperPositionModel)
                .where(PaperPositionModel.id == positions[0].id)
                .values(opened_at=to_db(utcnow() - td(days=30)))
            )
        await mem_db.close_paper_position(positions[0].id, exit_price=0.0,
                                          exit_reason="resolution_loss", pnl=-100.0)
        assert await engine.realized_pnl_today() == pytest.approx(-100.0)
        allowed, reason = await engine.allow("theta", "m2", 10.0)
        assert not allowed and reason.startswith("daily_loss")

    @pytest.mark.asyncio
    async def test_daily_loss_latches_for_the_day(self, mem_db):
        """A later profitable close must NOT un-trip the kill switch."""
        engine = RiskEngine(mem_db)
        engine.limits["daily_loss"] = 50.0
        await _seed_position(mem_db, "m1", entry=0.5, size=200)
        positions = await mem_db.get_paper_positions(status="open")
        await mem_db.close_paper_position(positions[0].id, exit_price=0.0,
                                          exit_reason="resolution_loss", pnl=-100.0)
        allowed, _ = await engine.allow("theta", "m2", 10.0)  # trips + latches
        assert not allowed
        # a big win brings the day positive — the latch must hold anyway
        await _seed_position(mem_db, "m3", entry=0.5, size=200)
        positions = await mem_db.get_paper_positions(status="open")
        await mem_db.close_paper_position(positions[0].id, exit_price=1.0,
                                          exit_reason="resolution_win", pnl=+500.0)
        allowed, reason = await engine.allow("theta", "m4", 10.0)
        assert not allowed and reason == "daily_loss_latched"
        # and the latch survives a restart
        fresh = RiskEngine(mem_db)
        await fresh.load_overrides()
        allowed, reason = await fresh.allow("theta", "m4", 10.0)
        assert not allowed and reason == "daily_loss_latched"

    def test_mc_var_zero_sims_guard(self):
        assert RiskEngine.mc_var([_entry()], sims=-1)["var"] == 0.0

    @pytest.mark.asyncio
    async def test_infinite_limit_rejected(self, mem_db):
        engine = RiskEngine(mem_db)
        with pytest.raises(ValueError):
            await engine.set_limit("total_deployed", float("inf"))

    @pytest.mark.asyncio
    async def test_p_win_side_fallback_on_token_mismatch(self, mem_db):
        """A stale/None token id must never invert the win probability."""
        await _seed_position(mem_db, "m1", entry=0.9, size=100)
        # corrupt the position's token to something matching neither side
        from sqlalchemy import update as sa_update
        from db.models import PaperPositionModel
        async with mem_db.session() as session:
            await session.execute(sa_update(PaperPositionModel).values(token_id="stale-token"))
        engine = RiskEngine(mem_db)
        book = await engine._open_book()
        # side is buy_yes and market mid ~0.9 -> p_win must stay ~0.9, not 0.1
        assert book[0]["p_win"] == pytest.approx(0.9, abs=0.02)
