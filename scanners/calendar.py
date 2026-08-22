"""Calendar scanner: deadline families, monotonicity arbs, window tables.

For cumulative tranches (near, far) of the same event, coherence requires
P(by near) <= P(by far). A genuine executable inversion — bid(near YES) >
ask(far YES) net of both-leg fees — is the (rare) pure-arb signal; it always
toasts. Implied windows and conditional hazards are a browsing surface
computed on read by the families API — judgment trades stay with the human.

Payoff (verified): buy near-NO + buy far-YES, edge e = bid_near − ask_far
net of fees; pays +e if event by near deadline or never, +(1+e) in the
window; the hidden fourth state is a RESOLUTION SPLIT losing ~$1 — which is
why description similarity gates the signal grade (MicroStrategy precedent).
Capital returns no later than the NEAR deadline — annualize to that.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from config.settings import settings
from core.fees import category_for, fee_rate_for, taker_fee_per_share
from core.timeutil import days_until, utcnow
from scanners.base import PeriodicScanner
from scanners.calendar_families import description_similarity, discover_families
from signals.models import Signal, SignalGrade, SignalSide

logger = logging.getLogger(__name__)

MIN_NET_EDGE = 0.005  # half a cent per pair, after fees
SIMILARITY_TO_GRADE = {"high": SignalGrade.A, "medium": SignalGrade.B, "MISMATCH": SignalGrade.C}


class CalendarScanner(PeriodicScanner):
    """Hourly REST scan for deadline families and monotonicity violations."""

    name = "calendar"

    def __init__(self, universe, signal_service, database) -> None:
        super().__init__(universe, signal_service, database)
        self.interval_minutes = settings.calendar_interval_minutes

    async def run_once(self) -> tuple[int, list[Signal]]:
        markets = self.universe.markets()
        families = discover_families(markets)

        # Persist families for the API/dashboard
        active_keys = set()
        for (stem, direction), members in families.items():
            family_key = f"stem:{direction}:{stem[:270]}"
            active_keys.add(family_key)
            await self.db.upsert_family(
                family_key=family_key,
                kind=f"stem_{direction}",
                title=members[0].question,
                members=[
                    {
                        "market_id": m.id,
                        "tranche_order": i,
                        "deadline": m.end_date,
                        "group_item_title": m.group_item_title,
                    }
                    for i, m in enumerate(members)
                ],
            )
        removed = await self.db.delete_stale_families(active_keys)
        if removed:
            logger.info(f"removed {removed} stale families")

        # Monotonicity check on adjacent executable pairs
        signals = []
        pairs_checked = 0
        for (_stem, direction), members in families.items():
            for near, far in zip(members, members[1:]):
                pairs_checked += 1
                signal = self._check_pair(near, far, direction)
                if signal:
                    signals.append(signal)

        logger.info(
            f"calendar: {len(families)} families, {pairs_checked} pairs, "
            f"{len(signals)} violations"
        )
        return pairs_checked, signals

    def _check_pair(self, near, far, direction: str = "up"):
        """Executable inversion test, fee-adjusted, on one adjacent pair.

        direction "up" (by/before): coherent means p_near <= p_far.
          Violation: bid(near YES) > ask(far YES) -> buy near-NO + buy far-YES.
          Cash back no later than the NEAR deadline.
        direction "down" (through/until, survival): coherent means p_near >= p_far.
          Violation: bid(far YES) > ask(near YES) -> buy near-YES + buy far-NO.
          Worst-case cash back at the FAR deadline.
        """
        rate_near = fee_rate_for(near, near.category or category_for(near))
        rate_far = fee_rate_for(far, far.category or category_for(far))
        tick = max(near.tick_size or 0.01, far.tick_size or 0.01)

        if direction == "up":
            if near.best_bid is None or far.best_ask is None:
                return None
            gross = near.best_bid - far.best_ask
            if gross <= 0:
                return None
            leg_a_price = far.best_ask               # buy far-YES
            leg_b_price = 1.0 - near.best_bid        # buy near-NO (complement approx)
            fees = (
                taker_fee_per_share(leg_a_price, rate_far)
                + taker_fee_per_share(leg_b_price, rate_near)
            )
            token_id, side = far.token_id_yes, SignalSide.BUY_YES
            horizon_days = days_until(near.end_date) or 1.0
            legs = "buy near-NO + buy far-YES; split-resolution risk ~= full outlay"
        else:
            if far.best_bid is None or near.best_ask is None:
                return None
            gross = far.best_bid - near.best_ask
            if gross <= 0:
                return None
            leg_a_price = near.best_ask              # buy near-YES
            leg_b_price = 1.0 - far.best_bid         # buy far-NO (complement approx)
            fees = (
                taker_fee_per_share(leg_a_price, rate_near)
                + taker_fee_per_share(leg_b_price, rate_far)
            )
            token_id, side = far.token_id_no, SignalSide.BUY_NO
            horizon_days = days_until(far.end_date) or 1.0
            legs = "buy near-YES + buy far-NO (survival family); split risk ~= full outlay"

        net = gross - fees - tick  # complement-spread buffer
        if net < MIN_NET_EDGE:
            return None

        similarity, details = description_similarity(near, far)
        grade = SIMILARITY_TO_GRADE[similarity]

        outlay = leg_a_price + leg_b_price
        annualized = (net / outlay) * (365.0 / max(horizon_days, 0.05)) if outlay > 0 else 0.0

        # entry_price must be the executable price of the PAPERED token:
        # up -> far-YES at leg_a_price (far ask); down -> far-NO at leg_b_price
        # (1 - far bid). One signal per pair/direction per day: a persisting
        # inversion must not re-toast hourly, quote drift must not mint keys.
        return Signal(
            strategy="calendar_arb",
            market_id=far.id,
            token_id=token_id,
            side=side,
            grade=grade,
            score=round(net, 4),
            entry_price=leg_b_price if direction == "down" else leg_a_price,
            expires_at=utcnow() + timedelta(minutes=self.interval_minutes * 2),
            dedup_key=f"cal:{near.id}:{far.id}:{direction}:{utcnow():%Y%m%d}",
            snapshot={
                "question": far.question,
                "slug": far.slug,
                "event_slug": far.event_slug,
                "direction": direction,
                "near_market_id": near.id,
                "near_question": near.question,
                "near_bid": near.best_bid,
                "near_ask": near.best_ask,
                "near_deadline": near.end_date.isoformat() if near.end_date else None,
                "far_question": far.question,
                "far_bid": far.best_bid,
                "far_ask": far.best_ask,
                "far_deadline": far.end_date.isoformat() if far.end_date else None,
                "edge": round(net, 4),
                "gross_edge": round(gross, 4),
                "fees": round(fees, 4),
                "outlay_per_pair": round(outlay, 4),
                "annualized_to_capital_return": round(annualized, 4),
                "similarity": similarity,
                "similarity_details": details,
                "fee_rate": rate_far,
                "legs": legs,
            },
        )
