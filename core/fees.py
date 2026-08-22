"""Fee math and category classification.

The authoritative fee source is the market's own `feeSchedule.rate` /
`feesEnabled` (verified against live Gamma, Aug 2026). The category map is a
fallback for markets that carry no fee fields, and category itself feeds the
calibration-based include/exclude filters (theta excludes weather and
entertainment; news-fade excludes crypto).

Taker fee formula (Fee Structure V2): fee_per_share = rate * p * (1 - p).
Makers pay zero.
"""

from __future__ import annotations

from typing import Optional

from config.settings import settings
from core.models import Market

# Tag/feeType keywords -> canonical category
_TAG_CATEGORY = {
    "crypto": "crypto",
    "bitcoin": "crypto",
    "ethereum": "crypto",
    "sports": "sports",
    "esports": "sports",
    "nfl": "sports",
    "nba": "sports",
    "mlb": "sports",
    "nhl": "sports",
    "soccer": "sports",
    "football": "sports",
    "tennis": "sports",
    "golf": "sports",
    "mma": "sports",
    "boxing": "sports",
    "dota 2": "sports",
    "counter-strike": "sports",
    "league of legends": "sports",
    "economy": "economics",
    "economics": "economics",
    "fed": "economics",
    "inflation": "economics",
    "politics": "politics",
    "elections": "politics",
    "geopolitics": "geopolitics",
    "world": "geopolitics",
    "middle east": "geopolitics",
    "ukraine": "geopolitics",
    "weather": "weather",
    "climate": "weather",
    "pop culture": "culture",
    "culture": "culture",
    "entertainment": "culture",
    "celebrities": "culture",
    "movies": "culture",
    "music": "culture",
    "awards": "culture",
    "business": "finance",
    "finance": "finance",
    "stocks": "finance",
    "companies": "finance",
    "tech": "tech",
    "ai": "tech",
    "science": "tech",
    "mentions": "mentions",
}

_FEE_TYPE_CATEGORY = {
    "sports": "sports",
    "crypto": "crypto",
    "economics": "economics",
    "politics": "politics",
    "finance": "finance",
    "culture": "culture",
    "weather": "weather",
    "tech": "tech",
    "mentions": "mentions",
}


def category_for(market: Market, event_tags: Optional[list[str]] = None) -> Optional[str]:
    """Best-effort canonical category for a market.

    Priority: explicit category field -> event tags -> feeType hint.
    """
    if market.category:
        return market.category.lower()
    for tag in (market.tags or []) + (event_tags or []):
        cat = _TAG_CATEGORY.get(tag.lower())
        if cat:
            return cat
    if market.fee_type:
        ft = market.fee_type.lower()
        for key, cat in _FEE_TYPE_CATEGORY.items():
            if key in ft:
                return cat
    return None


def fee_rate_for(market: Market, category: Optional[str] = None) -> float:
    """Effective taker fee rate for a market.

    feesEnabled=False means fee-free regardless of category (verified live:
    geopolitics markets carry feesEnabled=False).
    """
    if not market.fees_enabled:
        return 0.0
    if market.fee_rate is not None:
        return market.fee_rate
    cat = category or category_for(market)
    if cat and cat in settings.category_fee_rates:
        return settings.category_fee_rates[cat]
    return settings.default_fee_rate


def taker_fee_per_share(price: float, rate: float) -> float:
    """Taker fee per share at a given price: rate * p * (1-p)."""
    price = min(max(price, 0.0), 1.0)
    return rate * price * (1.0 - price)


def net_edge_per_share(ask: float, rate: float) -> float:
    """Theta economics: profit per share buying at `ask`, held to a win."""
    return (1.0 - ask) - taker_fee_per_share(ask, rate)


def annualized_yield(net_edge: float, ask: float, days_to_resolution: float) -> float:
    """Annualize a per-share net edge on capital `ask` over `days`."""
    if ask <= 0 or days_to_resolution <= 0:
        return 0.0
    per_cycle = net_edge / ask
    return per_cycle * (365.0 / days_to_resolution)
