"""Database operations and session management."""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator, Optional, Sequence

from sqlalchemy import and_, delete, desc, event, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import settings
from core.models import Market, Price
from core.timeutil import to_db, utcnow
from db.models import (
    AlertConfigModel,
    AlertModel,
    Base,
    EventModel,
    FamilyMemberModel,
    MarketFamilyModel,
    MarketModel,
    PaperPositionModel,
    PriceModel,
    RealFillModel,
    ScannerRunModel,
    SignalModel,
    WatchlistModel,
)

logger = logging.getLogger(__name__)


class Database:
    """Async database manager."""

    def __init__(self, url: Optional[str] = None):
        self.url = url or settings.database_url
        self.engine = create_async_engine(self.url, echo=False)
        if self.url.startswith("sqlite"):
            @event.listens_for(self.engine.sync_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @property
    def _is_memory(self) -> bool:
        return ":memory:" in self.url

    async def init_db(self) -> None:
        """Create tables for in-memory test DBs; otherwise verify migrations ran."""
        if self._is_memory:
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return
        async with self.engine.connect() as conn:
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")
            )
            if result.first() is None:
                raise RuntimeError(
                    "Database schema missing. Run: python -m alembic upgrade head"
                )

    async def close(self) -> None:
        """Close database connections."""
        await self.engine.dispose()

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get a database session."""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # ------------------------------------------------------------------ markets
    async def upsert_markets(self, markets: list[Market]) -> int:
        """Bulk upsert parsed markets; returns count. Sets last_synced_at."""
        now = to_db(utcnow())
        count = 0
        async with self.session() as session:
            ids = [m.id for m in markets]
            result = await session.execute(
                select(MarketModel).where(MarketModel.id.in_(ids))
            )
            existing = {m.id: m for m in result.scalars().all()}
            seen: set[str] = set()
            for m in markets:
                if m.id in seen:
                    continue
                seen.add(m.id)
                values = {
                    "condition_id": m.condition_id,
                    "question": m.question,
                    "description": m.description,
                    "category": m.category,
                    "slug": m.slug,
                    "end_date": to_db(m.end_date),
                    "start_date": to_db(m.start_date),
                    "active": m.active,
                    "closed": m.closed,
                    "accepting_orders": m.accepting_orders,
                    "token_id_yes": m.token_id_yes,
                    "token_id_no": m.token_id_no,
                    "outcomes": json.dumps(m.outcomes) if m.outcomes else None,
                    "price_yes": m.price_yes,
                    "price_no": m.price_no,
                    "best_bid": m.best_bid,
                    "best_ask": m.best_ask,
                    "spread": m.spread,
                    "last_trade_price": m.last_trade_price,
                    "one_day_price_change": m.one_day_price_change,
                    "tick_size": m.tick_size,
                    "volume_24h": m.volume_24h,
                    "liquidity": m.liquidity,
                    "event_id": m.event_id,
                    "event_slug": m.event_slug,
                    "group_item_title": m.group_item_title,
                    "neg_risk": m.neg_risk,
                    "neg_risk_market_id": m.neg_risk_market_id,
                    "fees_enabled": m.fees_enabled,
                    "fee_rate": m.fee_rate,
                    "fee_type": m.fee_type,
                    "resolution_source": m.resolution_source,
                    "uma_resolution_status": m.uma_resolution_status,
                    "resolved_outcome": m.resolved_outcome,
                    "closed_time": to_db(m.closed_time),
                    "tags": json.dumps(m.tags) if m.tags else None,
                    "last_synced_at": now,
                }
                row = existing.get(m.id)
                if row:
                    for key, value in values.items():
                        setattr(row, key, value)
                else:
                    session.add(MarketModel(id=m.id, **values))
                count += 1
        return count

    async def set_market_tags(self, market_id: str, tags: list[str], category: Optional[str]) -> None:
        """Update tags/category on a market (tags come from the events sync)."""
        async with self.session() as session:
            await session.execute(
                update(MarketModel)
                .where(MarketModel.id == market_id)
                .values(tags=json.dumps(tags) if tags else None, category=category)
            )

    async def get_market(self, market_id: str) -> Optional[MarketModel]:
        """Get a market by ID."""
        async with self.session() as session:
            result = await session.execute(
                select(MarketModel).where(MarketModel.id == market_id)
            )
            return result.scalar_one_or_none()

    async def get_markets_by_ids(self, ids: list[str]) -> Sequence[MarketModel]:
        """Get markets by id list."""
        if not ids:
            return []
        async with self.session() as session:
            result = await session.execute(
                select(MarketModel).where(MarketModel.id.in_(ids))
            )
            return result.scalars().all()

    async def get_active_markets(
        self,
        min_liquidity: float = 0.0,
        limit: int = 5000,
    ) -> Sequence[MarketModel]:
        """Get active, order-accepting, not-closed markets."""
        async with self.session() as session:
            query = (
                select(MarketModel)
                .where(
                    MarketModel.active.is_(True),
                    MarketModel.closed.is_(False),
                    MarketModel.accepting_orders.is_(True),
                )
                .order_by(desc(MarketModel.liquidity))
                .limit(limit)
            )
            if min_liquidity > 0:
                query = query.where(MarketModel.liquidity >= min_liquidity)
            result = await session.execute(query)
            return result.scalars().all()

    async def upsert_event(
        self,
        event_id: str,
        slug: Optional[str],
        title: Optional[str],
        neg_risk: bool,
        tags: list[str],
        end_date: Optional[datetime],
    ) -> None:
        """Insert or update an event row."""
        async with self.session() as session:
            result = await session.execute(
                select(EventModel).where(EventModel.id == event_id)
            )
            row = result.scalar_one_or_none()
            values = {
                "slug": slug,
                "title": title,
                "neg_risk": neg_risk,
                "tags": json.dumps(tags) if tags else None,
                "end_date": to_db(end_date),
            }
            if row:
                for key, value in values.items():
                    setattr(row, key, value)
            else:
                session.add(EventModel(id=event_id, **values))

    async def get_event_tags(self) -> dict[str, list[str]]:
        """Map event_id -> tag labels."""
        async with self.session() as session:
            result = await session.execute(select(EventModel))
            out = {}
            for ev in result.scalars().all():
                try:
                    out[ev.id] = json.loads(ev.tags) if ev.tags else []
                except json.JSONDecodeError:
                    out[ev.id] = []
            return out

    # ------------------------------------------------------------------- prices
    async def add_bars(self, bars: list[Price]) -> int:
        """Batch-insert completed 1-minute bars."""
        if not bars:
            return 0
        async with self.session() as session:
            session.add_all(
                PriceModel(
                    market_id=b.market_id,
                    timestamp=to_db(b.timestamp),
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                )
                for b in bars
            )
        return len(bars)

    async def get_prices(
        self,
        market_id: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 1000,
    ) -> Sequence[PriceModel]:
        """Get price history for a market (newest first)."""
        async with self.session() as session:
            query = select(PriceModel).where(PriceModel.market_id == market_id)
            if start:
                query = query.where(PriceModel.timestamp >= to_db(start))
            if end:
                query = query.where(PriceModel.timestamp <= to_db(end))
            query = query.order_by(desc(PriceModel.timestamp)).limit(limit)
            result = await session.execute(query)
            return result.scalars().all()

    async def prune_bars(self, older_than: datetime) -> int:
        """Delete bars older than the cutoff; returns rows deleted."""
        async with self.session() as session:
            result = await session.execute(
                delete(PriceModel).where(PriceModel.timestamp < to_db(older_than))
            )
            return result.rowcount or 0

    # ------------------------------------------------------------------- alerts
    async def add_alert(
        self,
        alert_type: str,
        title: str,
        message: str,
        market_id: Optional[str] = None,
        severity: str = "info",
        data: Optional[dict] = None,
    ) -> AlertModel:
        """Add a new alert."""
        async with self.session() as session:
            alert = AlertModel(
                market_id=market_id,
                alert_type=alert_type,
                severity=severity,
                title=title,
                message=message,
                data=json.dumps(data) if data else None,
            )
            session.add(alert)
            await session.flush()
            return alert

    async def get_alerts(
        self,
        alert_type: Optional[str] = None,
        market_id: Optional[str] = None,
        acknowledged: Optional[bool] = None,
        limit: int = 100,
    ) -> Sequence[AlertModel]:
        """Get alerts with optional filters."""
        async with self.session() as session:
            query = select(AlertModel)
            conditions = []
            if alert_type:
                conditions.append(AlertModel.alert_type == alert_type)
            if market_id:
                conditions.append(AlertModel.market_id == market_id)
            if acknowledged is not None:
                conditions.append(AlertModel.acknowledged == acknowledged)
            if conditions:
                query = query.where(and_(*conditions))
            query = query.order_by(desc(AlertModel.created_at)).limit(limit)
            result = await session.execute(query)
            return result.scalars().all()

    async def acknowledge_alert(self, alert_id: int) -> bool:
        """Mark an alert as acknowledged."""
        async with self.session() as session:
            result = await session.execute(
                select(AlertModel).where(AlertModel.id == alert_id)
            )
            alert = result.scalar_one_or_none()
            if alert:
                alert.acknowledged = True
                return True
            return False

    # ------------------------------------------------------------------ signals
    async def insert_signal(self, values: dict) -> Optional[SignalModel]:
        """Insert a signal; returns None if the dedup_key already exists."""
        try:
            async with self.session() as session:
                signal = SignalModel(**values)
                session.add(signal)
                await session.flush()
                await session.refresh(signal)
                return signal
        except IntegrityError:
            return None

    async def has_signal_with_dedup(self, dedup_key: str) -> bool:
        """True if any signal (any status) exists with this dedup key."""
        async with self.session() as session:
            result = await session.execute(
                select(SignalModel.id).where(SignalModel.dedup_key == dedup_key)
            )
            return result.first() is not None

    async def get_signal(self, signal_id: int) -> Optional[SignalModel]:
        """Get a signal by id."""
        async with self.session() as session:
            result = await session.execute(
                select(SignalModel).where(SignalModel.id == signal_id)
            )
            return result.scalar_one_or_none()

    async def get_signals(
        self,
        strategy: Optional[str] = None,
        status: Optional[str] = None,
        grade: Optional[str] = None,
        limit: int = 200,
    ) -> Sequence[SignalModel]:
        """List signals, newest first."""
        async with self.session() as session:
            query = select(SignalModel)
            if strategy:
                query = query.where(SignalModel.strategy == strategy)
            if status:
                query = query.where(SignalModel.status == status)
            if grade:
                query = query.where(SignalModel.grade == grade)
            query = query.order_by(desc(SignalModel.created_at)).limit(limit)
            result = await session.execute(query)
            return result.scalars().all()

    async def get_signals_by_status(self, statuses: list[str]) -> Sequence[SignalModel]:
        """List signals in any of the given statuses."""
        async with self.session() as session:
            result = await session.execute(
                select(SignalModel).where(SignalModel.status.in_(statuses))
            )
            return result.scalars().all()

    async def update_signal(self, signal_id: int, **values) -> None:
        """Update fields on a signal."""
        async with self.session() as session:
            await session.execute(
                update(SignalModel).where(SignalModel.id == signal_id).values(**values)
            )

    # ------------------------------------------------------------- paper trades
    async def insert_paper_position(self, values: dict) -> PaperPositionModel:
        """Insert a paper position."""
        async with self.session() as session:
            pos = PaperPositionModel(**values)
            session.add(pos)
            await session.flush()
            await session.refresh(pos)
            return pos

    async def get_paper_positions(
        self,
        strategy: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 500,
    ) -> Sequence[PaperPositionModel]:
        """List paper positions, newest first."""
        async with self.session() as session:
            query = select(PaperPositionModel)
            if strategy:
                query = query.where(PaperPositionModel.strategy == strategy)
            if status:
                query = query.where(PaperPositionModel.status == status)
            query = query.order_by(desc(PaperPositionModel.opened_at)).limit(limit)
            result = await session.execute(query)
            return result.scalars().all()

    async def conflicting_open_position(
        self, market_id: Optional[str], token_id: Optional[str], strategy: Optional[str]
    ) -> bool:
        """True if opening this position would conflict with one already open.

        Two distinct conflicts, both of which have to be caught by market rather
        than by market+token — buy_yes and buy_no carry DIFFERENT token ids, so a
        token-keyed check lets a strategy hold both sides of one market at once:

        - same strategy, same market, either token: one thesis per market;
        - any strategy, same market, the opposite token: holding both outcomes
          is a guaranteed wash minus fees, whatever the two theses were.
        """
        if not market_id:
            return False
        async with self.session() as session:
            result = await session.execute(
                select(PaperPositionModel.strategy, PaperPositionModel.token_id).where(
                    PaperPositionModel.market_id == market_id,
                    PaperPositionModel.status == "open",
                )
            )
            for open_strategy, open_token in result.all():
                if strategy is not None and open_strategy == strategy:
                    return True
                if token_id and open_token and open_token != token_id:
                    return True
        return False

    async def get_paper_position_by_signal(self, signal_id: int) -> Optional[PaperPositionModel]:
        """Get the paper position tied to a signal."""
        async with self.session() as session:
            result = await session.execute(
                select(PaperPositionModel).where(PaperPositionModel.signal_id == signal_id)
            )
            return result.scalar_one_or_none()

    async def close_paper_position(
        self,
        position_id: int,
        exit_price: float,
        exit_reason: str,
        pnl: float,
    ) -> None:
        """Close a paper position."""
        async with self.session() as session:
            await session.execute(
                update(PaperPositionModel)
                .where(PaperPositionModel.id == position_id)
                .values(
                    status="closed",
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    pnl=pnl,
                    closed_at=to_db(utcnow()),
                )
            )

    # --------------------------------------------------------------- real fills
    async def insert_real_fill(self, values: dict) -> RealFillModel:
        """Insert a user-entered real fill."""
        async with self.session() as session:
            fill = RealFillModel(**values)
            session.add(fill)
            await session.flush()
            await session.refresh(fill)
            return fill

    async def get_real_fills(
        self,
        signal_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 500,
    ) -> Sequence[RealFillModel]:
        """List real fills."""
        async with self.session() as session:
            query = select(RealFillModel)
            if signal_id is not None:
                query = query.where(RealFillModel.signal_id == signal_id)
            if status:
                query = query.where(RealFillModel.status == status)
            query = query.order_by(desc(RealFillModel.created_at)).limit(limit)
            result = await session.execute(query)
            return result.scalars().all()

    async def close_real_fill(self, fill_id: int, exit_price: float, pnl: float) -> None:
        """Close a real fill."""
        async with self.session() as session:
            await session.execute(
                update(RealFillModel)
                .where(RealFillModel.id == fill_id)
                .values(status="closed", exit_price=exit_price, pnl=pnl, closed_at=to_db(utcnow()))
            )

    # ----------------------------------------------------------------- families
    async def upsert_family(
        self,
        family_key: str,
        kind: str,
        title: Optional[str],
        members: list[dict],
    ) -> int:
        """Insert/update a family and replace its members. Returns family id.

        members: [{market_id, tranche_order, deadline, group_item_title}]
        """
        async with self.session() as session:
            result = await session.execute(
                select(MarketFamilyModel).where(MarketFamilyModel.family_key == family_key)
            )
            family = result.scalar_one_or_none()
            if family:
                family.kind = kind
                family.title = title
                await session.execute(
                    delete(FamilyMemberModel).where(FamilyMemberModel.family_id == family.id)
                )
            else:
                family = MarketFamilyModel(family_key=family_key, kind=kind, title=title)
                session.add(family)
                await session.flush()
            for m in members:
                session.add(
                    FamilyMemberModel(
                        family_id=family.id,
                        market_id=m["market_id"],
                        tranche_order=m.get("tranche_order", 0),
                        deadline=to_db(m.get("deadline")),
                        group_item_title=m.get("group_item_title"),
                    )
                )
            return family.id

    async def get_families(self) -> list[tuple[MarketFamilyModel, list[FamilyMemberModel]]]:
        """List families with their members (tranche order)."""
        async with self.session() as session:
            result = await session.execute(select(MarketFamilyModel))
            families = result.scalars().all()
            out = []
            for fam in families:
                members = await session.execute(
                    select(FamilyMemberModel)
                    .where(FamilyMemberModel.family_id == fam.id)
                    .order_by(FamilyMemberModel.tranche_order)
                )
                out.append((fam, list(members.scalars().all())))
            return out

    async def delete_stale_families(self, active_keys: set[str]) -> int:
        """Remove families whose key is no longer discovered."""
        async with self.session() as session:
            result = await session.execute(select(MarketFamilyModel))
            removed = 0
            for fam in result.scalars().all():
                if fam.family_key not in active_keys:
                    await session.execute(
                        delete(FamilyMemberModel).where(FamilyMemberModel.family_id == fam.id)
                    )
                    await session.delete(fam)
                    removed += 1
            return removed

    # ------------------------------------------------------------- scanner runs
    async def start_scanner_run(self, scanner: str) -> int:
        """Open a scanner_runs row; returns its id."""
        async with self.session() as session:
            run = ScannerRunModel(scanner=scanner, started_at=to_db(utcnow()))
            session.add(run)
            await session.flush()
            return run.id

    async def finish_scanner_run(
        self,
        run_id: int,
        ok: bool,
        items_scanned: int = 0,
        signals_emitted: int = 0,
        error: Optional[str] = None,
    ) -> None:
        """Close a scanner_runs row."""
        async with self.session() as session:
            await session.execute(
                update(ScannerRunModel)
                .where(ScannerRunModel.id == run_id)
                .values(
                    finished_at=to_db(utcnow()),
                    ok=ok,
                    items_scanned=items_scanned,
                    signals_emitted=signals_emitted,
                    error=error,
                )
            )

    async def get_last_scanner_runs(self, per_scanner: int = 5) -> dict[str, list[ScannerRunModel]]:
        """Last N runs per scanner."""
        async with self.session() as session:
            result = await session.execute(
                select(ScannerRunModel).order_by(desc(ScannerRunModel.started_at)).limit(200)
            )
            out: dict[str, list[ScannerRunModel]] = {}
            for run in result.scalars().all():
                bucket = out.setdefault(run.scanner, [])
                if len(bucket) < per_scanner:
                    bucket.append(run)
            return out

    async def prune_scanner_runs(self, keep_per_scanner: int) -> None:
        """Keep only the newest N runs per scanner."""
        async with self.session() as session:
            result = await session.execute(
                select(ScannerRunModel.scanner).distinct()
            )
            for (scanner,) in result.all():
                ids = await session.execute(
                    select(ScannerRunModel.id)
                    .where(ScannerRunModel.scanner == scanner)
                    .order_by(desc(ScannerRunModel.started_at))
                    .offset(keep_per_scanner)
                )
                stale = [i for (i,) in ids.all()]
                if stale:
                    await session.execute(
                        delete(ScannerRunModel).where(ScannerRunModel.id.in_(stale))
                    )

    # ---------------------------------------------------------------- watchlist
    async def add_to_watchlist(
        self,
        market_id: str,
        price_threshold_pct: Optional[float] = None,
        volume_threshold_usd: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> WatchlistModel:
        """Add a market to the watchlist."""
        async with self.session() as session:
            item = WatchlistModel(
                market_id=market_id,
                price_threshold_pct=price_threshold_pct,
                volume_threshold_usd=volume_threshold_usd,
                notes=notes,
            )
            session.add(item)
            await session.flush()
            return item

    async def remove_from_watchlist(self, market_id: str) -> bool:
        """Remove a market from the watchlist."""
        async with self.session() as session:
            result = await session.execute(
                select(WatchlistModel).where(WatchlistModel.market_id == market_id)
            )
            item = result.scalar_one_or_none()
            if item:
                await session.delete(item)
                return True
            return False

    async def get_watchlist(self) -> Sequence[WatchlistModel]:
        """Get all watchlist items."""
        async with self.session() as session:
            result = await session.execute(select(WatchlistModel))
            return result.scalars().all()

    # ------------------------------------------------------------------- config
    async def get_config(self, key: str) -> Optional[str]:
        """Get a config value."""
        async with self.session() as session:
            result = await session.execute(
                select(AlertConfigModel).where(AlertConfigModel.key == key)
            )
            config = result.scalar_one_or_none()
            return config.value if config else None

    async def set_config(self, key: str, value: str) -> None:
        """Set a config value."""
        async with self.session() as session:
            result = await session.execute(
                select(AlertConfigModel).where(AlertConfigModel.key == key)
            )
            config = result.scalar_one_or_none()
            if config:
                config.value = value
            else:
                config = AlertConfigModel(key=key, value=value)
                session.add(config)


# Global database instance
db = Database()
