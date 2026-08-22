"""News-fade scanner: detect spike overreactions, emit fade CANDIDATES.

The resolving-vs-non-resolving judgment stays with the human — every signal
carries the question and a deep link for fast triage. The paper simulation is
honest: a fade only "fills" if price actually touches the suggested maker
zone within its validity window.

Research grounding: 58% of hype markets showed next-day mean reversion;
the catastrophic failure mode is fading a true information reveal, so
near-resolution markets are excluded and crypto (bot-dominated) is excluded
by default. Entry sits near the extreme; the 50-61.8% retracement of the
spike is the take-profit REFERENCE stored in the snapshot.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import timedelta

from config.settings import settings
from core.events import Event, EventType
from core.fees import category_for
from core.timeutil import from_db, utcnow
from scanners.base import StreamScanner
from signals.models import Signal, SignalGrade, SignalSide

logger = logging.getLogger(__name__)

ENTRY_DEPTH = 0.25  # entry zone spans this fraction of the spike, from the extreme


class NewsFadeScanner(StreamScanner):
    """Real-time spike detection over the streamed universe."""

    name = "news_fade"
    subscribed_events = [EventType.BOOK_TOP, EventType.TRADE_TICK]

    def __init__(self, universe, signal_service, database) -> None:
        super().__init__(universe, signal_service, database)
        # market_id -> deque[(ts, mid)]
        self._history: dict[str, deque] = {}
        self._window = timedelta(minutes=settings.fade_window_minutes)
        # market_id -> (direction, locked_until): one directional thesis per
        # market at a time — see _direction_locked
        self._locks: dict[str, tuple[str, object]] = {}

    async def seed_baselines(self) -> None:
        """Seed price history from recorded bars so baselines survive restarts."""
        seeded = 0
        for market in self.universe.markets()[:500]:
            bars = await self.db.get_prices(
                market.id, start=utcnow() - self._window, limit=settings.fade_window_minutes + 5
            )
            if not bars:
                continue
            dq = deque(maxlen=settings.fade_window_minutes * 60)
            for bar in reversed(bars):  # newest-first -> chronological
                dq.append((from_db(bar.timestamp), bar.close))
            self._history[market.id] = dq
            seeded += 1
        if seeded:
            logger.info(f"news-fade seeded baselines for {seeded} markets")
        await self.seed_locks()

    async def seed_locks(self) -> None:
        """Re-arm directional locks after a restart.

        Without this a restart forgets which way each market was already faded,
        and the opposite-side signal the lock exists to prevent slips through.
        """
        lock = timedelta(hours=settings.fade_direction_lock_hours)
        now = utcnow()
        recent = await self.db.get_signals(strategy=self.name, limit=500)
        for sig in recent:
            if not sig.market_id:
                continue
            created = from_db(sig.created_at)
            if created is None or now - created > lock:
                continue
            direction = "up" if sig.side == SignalSide.BUY_NO.value else "down"
            existing = self._locks.get(sig.market_id)
            # get_signals is newest-first; the newest signal owns the lock
            if existing is None:
                self._locks[sig.market_id] = (direction, created + lock)
        if self._locks:
            logger.info(f"news-fade re-armed {len(self._locks)} directional locks")

    def _direction_locked(self, market_id: str, direction: str) -> bool:
        """True if this market was recently faded the OTHER way.

        Fading a spike predicts a retrace; that retrace then looks like a fresh
        spike in the opposite direction. Acting on it opens an offsetting
        position in the same market and books a guaranteed wash minus fees.
        Same-direction re-fires are left to the dedup key.
        """
        entry = self._locks.get(market_id)
        if entry is None:
            return False
        locked_direction, until = entry
        if utcnow() >= until:
            del self._locks[market_id]
            return False
        return locked_direction != direction

    async def on_stream_event(self, event: Event) -> list[Signal]:
        data = event.data
        asset_id = data.get("asset_id")
        price = data.get("mid") if event.type == EventType.BOOK_TOP else data.get("price")
        if asset_id is None or price is None:
            return []
        market = self.universe.market_for_asset(asset_id)
        if market is None:
            return []

        now = utcnow()
        dq = self._history.setdefault(market.id, deque(maxlen=settings.fade_window_minutes * 60))
        dq.append((now, price))
        cutoff = now - self._window
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        if len(dq) < 5:
            return []

        candidate = self._detect(market, dq, price)
        if candidate is None:
            return []
        direction = candidate.snapshot["direction"]
        if self._direction_locked(market.id, direction):
            logger.debug(
                f"news-fade: {market.id} locked {self._locks[market.id][0]}; "
                f"suppressing {direction} fade"
            )
            return []
        self._locks[market.id] = (
            direction,
            now + timedelta(hours=settings.fade_direction_lock_hours),
        )
        return [candidate]

    def _detect(self, market, dq, price: float):
        """Pure-ish spike detection over the current window."""
        prices = [p for _, p in dq]
        window_min = min(prices)
        window_max = max(prices)
        spike_up = price - window_min
        spike_down = window_max - price

        if max(spike_up, spike_down) < settings.fade_spike_pp:
            return None
        # Filters (all research-derived)
        if (market.liquidity or 0) < settings.fade_min_liquidity:
            return None
        category = market.category or category_for(market)
        if category in settings.fade_excluded_categories:
            return None
        if market.end_date is not None:
            hours_left = (market.end_date - utcnow()).total_seconds() / 3600
            if hours_left < settings.fade_near_end_exclusion_hours:
                return None  # near-resolution moves are usually real

        now = utcnow()
        window_bucket = int(now.timestamp() // (settings.fade_window_minutes * 60))

        if spike_up >= spike_down:
            # spike UP on YES -> fade by buying NO; zone expressed in NO price,
            # watched on the YES stream with inverted comparison
            direction = "up"
            magnitude = spike_up
            baseline = window_min
            extreme = price
            yes_zone_high = extreme
            yes_zone_low = extreme - ENTRY_DEPTH * magnitude
            zone_low, zone_high = 1 - yes_zone_high, 1 - yes_zone_low
            side = SignalSide.BUY_NO
            token_id = market.token_id_no
            invert = True
            target_yes = extreme - magnitude * settings.fade_retrace_high, extreme - magnitude * settings.fade_retrace_low
            target = (1 - target_yes[1], 1 - target_yes[0])
        else:
            # spike DOWN on YES -> fade by buying YES near the trough
            direction = "down"
            magnitude = spike_down
            baseline = window_max
            extreme = price
            zone_low = extreme
            zone_high = extreme + ENTRY_DEPTH * magnitude
            side = SignalSide.BUY_YES
            token_id = market.token_id_yes
            invert = False
            target = (extreme + magnitude * settings.fade_retrace_low,
                      extreme + magnitude * settings.fade_retrace_high)

        if token_id is None:
            return None
        # same convention both directions; the paper fill itself happens at the
        # first OBSERVED in-zone price, this is just the display reference
        entry_ref = (zone_low + zone_high) / 2

        return Signal(
            strategy="news_fade",
            market_id=market.id,
            token_id=token_id,
            side=side,
            grade=SignalGrade.B,
            score=round(magnitude, 4),
            entry_price=round(entry_ref, 4),
            entry_zone_low=round(zone_low, 4),
            entry_zone_high=round(zone_high, 4),
            expires_at=now + timedelta(hours=settings.fade_entry_validity_hours),
            dedup_key=f"fade:{market.id}:{direction}:{window_bucket}",
            snapshot={
                "question": market.question,
                "slug": market.slug,
                "event_slug": market.event_slug,
                "category": category,
                "spike_pp": round(magnitude if direction == "up" else -magnitude, 4),
                "direction": direction,
                "baseline": round(baseline, 4),
                "extreme": round(extreme, 4),
                "window_minutes": settings.fade_window_minutes,
                "liquidity": market.liquidity,
                "fee_rate": 0.0,  # maker entry
                "target_zone": [round(target[0], 4), round(target[1], 4)],
                "time_stop_hours": settings.fade_time_stop_hours,
                "watch_asset_id": market.token_id_yes,
                "invert": invert,
            },
        )
