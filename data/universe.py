"""UniverseManager: periodic market/event sync + stream-universe selection."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from config.settings import settings
from core.models import Market
from core.timeutil import utcnow
from data.gamma_client import GammaClient, parse_event_tags, parse_market
from data.storage import Database

logger = logging.getLogger(__name__)


class UniverseManager:
    """Keeps the local markets/events tables fresh and selects the stream universe.

    - Every `universe_refresh_minutes`: paginate /markets (volume-ordered),
      upsert into the DB, sync /events for tags/negRisk, then recompute the
      top-N stream universe (with hysteresis so membership doesn't flap).
    - `on_universe_change(asset_ids)` is called only when membership changes.
    """

    def __init__(
        self,
        gamma: GammaClient,
        database: Database,
        on_universe_change: Optional[Callable[[list[str]], None]] = None,
    ) -> None:
        self.gamma = gamma
        self.db = database
        self.on_universe_change = on_universe_change
        self._universe_asset_ids: list[str] = []
        self._asset_to_market: dict[str, str] = {}
        self._market_meta: dict[str, Market] = {}
        self._last_sync_at = None
        self._last_sync_count = 0
        self._sync_errors = 0

    # ------------------------------------------------------------------ syncing
    async def sync_once(self) -> int:
        """One full sync pass. Returns number of markets upserted."""
        parsed: list[Market] = []
        async for page in self.gamma.iter_markets(
            max_pages=settings.universe_sync_pages,
            page_size=100,
            active=True,
            closed=False,
            order="volume24hr",
        ):
            for raw in page:
                try:
                    parsed.append(parse_market(raw))
                except Exception as e:  # noqa: BLE001 — one bad market must not kill sync
                    logger.warning(f"parse_market failed for {raw.get('id')}: {e}")

        # Volume-ordered pagination shifts between requests -> the same market
        # can appear on two pages; dedupe by id (last occurrence wins).
        deduped = {m.id: m for m in parsed}
        parsed = list(deduped.values())
        if not parsed:
            # never replace a working universe with emptiness — a failed or
            # empty fetch must keep the previous in-memory state
            raise RuntimeError("market sync returned no markets; keeping previous universe")
        count = await self.db.upsert_markets(parsed)
        self._market_meta = deduped

        # Events sync: tags live only on /events; grab a few pages for taxonomy
        event_tag_count = 0
        try:
            async for page in self.gamma.iter_events(max_pages=5, page_size=100):
                for ev in page:
                    ev_id = str(ev.get("id", ""))
                    if not ev_id:
                        continue
                    tags = parse_event_tags(ev)
                    from core.timeutil import parse_dt
                    await self.db.upsert_event(
                        event_id=ev_id,
                        slug=ev.get("slug"),
                        title=ev.get("title"),
                        neg_risk=bool(ev.get("negRisk", False)),
                        tags=tags,
                        end_date=parse_dt(ev.get("endDate")),
                    )
                    event_tag_count += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"events sync failed (continuing): {e}")

        # Propagate event tags onto markets in memory (category classification)
        event_tags = await self.db.get_event_tags()
        from core.fees import category_for
        for m in parsed:
            tags = event_tags.get(m.event_id or "", [])
            if tags:
                m.tags = tags
                category = category_for(m, tags)
                if category:
                    await self.db.set_market_tags(m.id, tags, category)

        self._last_sync_at = utcnow()
        self._last_sync_count = count
        logger.info(f"universe sync: {count} markets, {event_tag_count} events")
        return count

    def _select_universe(self) -> list[str]:
        """Top-N liquid markets' YES tokens, with hysteresis."""
        candidates = [
            m for m in self._market_meta.values()
            if m.token_id_yes
            and m.active and not m.closed and m.accepting_orders
            and (m.liquidity or 0) >= settings.scan_min_liquidity
            and (m.volume_24h or 0) >= settings.scan_min_volume_24h
        ]
        candidates.sort(key=lambda m: m.liquidity or 0, reverse=True)
        top = candidates[: settings.stream_universe_size]
        top_ids_ordered = [m.token_id_yes for m in top]  # liquidity order

        # Hysteresis: keep current members still within 1.2x the cut, but cap
        # incumbents at 90% of the universe so top newcomers always get in
        # (unconditional incumbent priority starved every new liquid market),
        # and admit newcomers in liquidity order, never set-iteration order.
        extended = {m.token_id_yes for m in candidates[: int(settings.stream_universe_size * 1.2)]}
        kept = [a for a in self._universe_asset_ids if a in extended]
        max_kept = int(settings.stream_universe_size * 0.9)
        kept = kept[:max_kept]
        kept_set = set(kept)
        newcomers = [a for a in top_ids_ordered if a not in kept_set]
        merged = list(dict.fromkeys(kept + newcomers))[: settings.stream_universe_size]
        return merged

    async def refresh(self) -> None:
        """Sync then (maybe) update the stream universe."""
        await self.sync_once()
        new_universe = self._select_universe()
        if set(new_universe) != set(self._universe_asset_ids):
            self._universe_asset_ids = new_universe
            self._asset_to_market = {
                m.token_id_yes: m.id
                for m in self._market_meta.values()
                if m.token_id_yes in set(new_universe)
            }
            logger.info(f"stream universe changed: {len(new_universe)} assets")
            if self.on_universe_change:
                self.on_universe_change(new_universe)

    async def run_forever(self) -> None:
        """Periodic refresh loop."""
        while True:
            try:
                await self.refresh()
                self._sync_errors = 0
            except Exception as e:  # noqa: BLE001 — loop must survive
                self._sync_errors += 1
                logger.error(f"universe refresh failed: {e}")
            await asyncio.sleep(settings.universe_refresh_minutes * 60)

    # ------------------------------------------------------------------ lookups
    @property
    def universe_asset_ids(self) -> list[str]:
        return list(self._universe_asset_ids)

    def market_id_for_asset(self, asset_id: str) -> Optional[str]:
        return self._asset_to_market.get(asset_id)

    def market_for_asset(self, asset_id: str) -> Optional[Market]:
        market_id = self._asset_to_market.get(asset_id)
        return self._market_meta.get(market_id) if market_id else None

    def market(self, market_id: str) -> Optional[Market]:
        return self._market_meta.get(market_id)

    def markets(self) -> list[Market]:
        return list(self._market_meta.values())

    def status(self) -> dict:
        return {
            "last_sync_at": self._last_sync_at.isoformat() if self._last_sync_at else None,
            "markets_cached": len(self._market_meta),
            "last_sync_count": self._last_sync_count,
            "universe_size": len(self._universe_asset_ids),
            "sync_errors": self._sync_errors,
        }
