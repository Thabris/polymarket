"""Theta scanner: near-certain contracts bought for convergence yield.

Research grounding (encoded, all thresholds configurable):
- 93-98.5c band; the >98.5c zone no longer covers documented oracle-tail risk.
- Category-conditional calibration: short-horizon weather/entertainment are
  OVERconfident domains — theta there is negative-EV, so they are excluded.
- Fees peak at 50c and are near-zero at the extremes; feeSchedule.rate on the
  market itself is authoritative (geopolitics is fee-free).
- The real edge is resolution-criteria reading: grade C wording is listed
  but never toasted or papered.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from config.settings import settings
from core.fees import annualized_yield, category_for, fee_rate_for, net_edge_per_share
from core.timeutil import days_until, utcnow
from data.clob_client import clob_client
from scanners import theta_clarity
from scanners.base import PeriodicScanner
from signals.models import Signal, SignalGrade, SignalSide

logger = logging.getLogger(__name__)


def _best_ask_from_book(book: dict):
    """(best ask price, size) from a raw CLOB book; None if no asks.

    Levels are not best-first — best ask = lowest price.
    """
    best = None
    for level in book.get("asks") or []:
        try:
            price, size = float(level["price"]), float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if best is None or price < best[0]:
            best = (price, size)
    return best


class ThetaScanner(PeriodicScanner):
    """Periodic REST scan for high-probability convergence candidates."""

    name = "theta"

    def __init__(self, universe, signal_service, database) -> None:
        super().__init__(universe, signal_service, database)
        self.interval_minutes = settings.theta_interval_minutes

    async def run_once(self) -> tuple[int, list[Signal]]:
        markets = self.universe.markets()
        candidates = []
        scanned = 0

        for market in markets:
            if not (market.active and market.accepting_orders and not market.closed):
                continue
            if (market.liquidity or 0) < settings.scan_min_liquidity:
                continue
            days = days_until(market.end_date)
            if days is None or days < settings.theta_min_days or days > settings.theta_max_days:
                continue
            scanned += 1

            category = market.category or category_for(market)
            if category in settings.theta_excluded_categories:
                continue

            for side, ask, token_id in self._sides(market):
                if ask is None or token_id is None:
                    continue
                if not (settings.theta_ask_min <= ask <= settings.theta_ask_max):
                    continue
                rate = fee_rate_for(market, category)
                net = net_edge_per_share(ask, rate)
                annualized = annualized_yield(net, ask, days)
                if annualized < settings.theta_hurdle_annualized:
                    continue
                candidates.append(
                    {
                        "market": market,
                        "side": side,
                        "token_id": token_id,
                        "ask": ask,
                        "rate": rate,
                        "net": net,
                        "annualized": annualized,
                        "days": days,
                        "category": category,
                    }
                )

        candidates.sort(key=lambda c: c["annualized"], reverse=True)
        top = candidates[: settings.theta_book_refine_top_n]

        signals = []
        for c in top:
            refined = await self._refine_with_book(c)
            if refined is None:
                continue
            signals.append(refined)
            await asyncio.sleep(0.2)  # CLOB rate-limit spacing

        return scanned, signals

    @staticmethod
    def _sides(market):
        """Executable asks for both sides from cached Gamma top-of-book."""
        yes_ask = market.best_ask
        no_ask = (1.0 - market.best_bid) if market.best_bid is not None else None
        return [
            (SignalSide.BUY_YES, yes_ask, market.token_id_yes),
            (SignalSide.BUY_NO, no_ask, market.token_id_no),
        ]

    async def _refine_with_book(self, c: dict):
        """Confirm executable price/size with a live CLOB book, then grade."""
        market = c["market"]
        book = await clob_client.get_book(c["token_id"])
        if book is None:
            ask, ask_size = c["ask"], None  # fetch FAILED: Gamma fallback
        else:
            best = _best_ask_from_book(book)
            if best is None:
                return None  # book fetched but has NO asks: nothing executable
            ask, ask_size = best
            if not (settings.theta_ask_min <= ask <= settings.theta_ask_max):
                return None
            c["net"] = net_edge_per_share(ask, c["rate"])
            c["annualized"] = annualized_yield(c["net"], ask, c["days"])
            if c["annualized"] < settings.theta_hurdle_annualized:
                return None

        clarity_grade, evidence = theta_clarity.grade(market.description, market.resolution_source)

        # Any live UMA dispute forces C — the oracle is already contested
        uma = market.uma_resolution_status
        if uma and uma.lower() not in ("resolved", ""):
            clarity_grade = "C"
            evidence["uma_status"] = uma

        signal_grade = SignalGrade(clarity_grade)

        return Signal(
            strategy="theta",
            market_id=market.id,
            token_id=c["token_id"],
            side=c["side"],
            grade=signal_grade,
            score=round(c["annualized"], 4),
            entry_price=ask,
            expires_at=utcnow() + timedelta(minutes=self.interval_minutes * 2),
            # one signal per market/side per day: keying on the live ask made
            # cent-flicker mint fresh signals while permanently blocking others
            dedup_key=f"theta:{market.id}:{c['side'].value}:{utcnow():%Y%m%d}",
            snapshot={
                "question": market.question,
                "slug": market.slug,
                "event_slug": market.event_slug,
                "category": c["category"],
                "fee_rate": c["rate"],
                "net_yield": round(c["net"] / ask, 5) if ask else None,
                "annualized_yield": round(c["annualized"], 4),
                "days_to_resolution": round(c["days"], 2),
                "liquidity": market.liquidity,
                "ask_size": ask_size,
                "neg_risk": market.neg_risk,
                "uma_resolution_status": market.uma_resolution_status,
                "clarity_evidence": evidence,
                "benchmark_apy": settings.usdc_benchmark_apy,
            },
        )
