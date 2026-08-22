"""Deadline-family API endpoints (window tables computed on read)."""

import logging

from fastapi import APIRouter, HTTPException

from core.models import Market
from data.storage import db
from scanners.calendar_families import description_similarity

logger = logging.getLogger(__name__)

router = APIRouter()


def _row_to_market(row) -> Market:
    """Minimal Market projection from a DB row (for similarity + prices)."""
    return Market(
        id=row.id,
        condition_id=row.condition_id,
        question=row.question,
        description=row.description,
        resolution_source=row.resolution_source,
        best_bid=row.best_bid,
        best_ask=row.best_ask,
        price_yes=row.price_yes,
        slug=row.slug,
        event_slug=row.event_slug,
        token_id_yes=row.token_id_yes,
        token_id_no=row.token_id_no,
        end_date=row.end_date,
        liquidity=row.liquidity,
    )


def _mid(market: Market):
    if market.best_bid is not None and market.best_ask is not None:
        return (market.best_bid + market.best_ask) / 2
    return market.price_yes


@router.get("")
async def list_families():
    """All deadline families with live tranche prices, windows, hazards."""
    families = await db.get_families()
    out = []
    for family, members in families:
        market_rows = await db.get_markets_by_ids([m.market_id for m in members])
        by_id = {r.id: r for r in market_rows}
        tranches = []
        for member in members:
            row = by_id.get(member.market_id)
            if row is None:
                continue
            market = _row_to_market(row)
            tranches.append(
                {
                    "market_id": market.id,
                    "question": market.question,
                    "deadline": member.deadline,
                    "bid": market.best_bid,
                    "ask": market.best_ask,
                    "mid": _mid(market),
                    "liquidity": market.liquidity,
                    "event_slug": market.event_slug or market.slug,
                    "_market": market,
                }
            )
        tranches.sort(key=lambda t: (t["deadline"] is None, t["deadline"]))

        survival = family.kind == "stem_down"
        pairs = []
        for near, far in zip(tranches, tranches[1:]):
            p_near, p_far = near["mid"], far["mid"]
            window = conditional = None
            if p_near is not None and p_far is not None:
                if survival:
                    # P(event ENDS in (near, far]) given survival to near
                    window = p_near - p_far
                    if p_near > 0:
                        conditional = window / p_near
                else:
                    window = p_far - p_near
                    if p_near < 1:
                        conditional = window / (1 - p_near)
            similarity, details = description_similarity(near["_market"], far["_market"])
            if survival:
                violation = (
                    far["bid"] is not None
                    and near["ask"] is not None
                    and far["bid"] > near["ask"]
                )
            else:
                violation = (
                    near["bid"] is not None
                    and far["ask"] is not None
                    and near["bid"] > far["ask"]
                )
            pairs.append(
                {
                    "near_market_id": near["market_id"],
                    "far_market_id": far["market_id"],
                    "near_deadline": near["deadline"],
                    "far_deadline": far["deadline"],
                    "survival": survival,
                    "implied_window": round(window, 4) if window is not None else None,
                    "conditional_hazard": round(conditional, 4) if conditional is not None else None,
                    "monotonicity_violation": violation,
                    "similarity": similarity,
                    "similarity_details": details,
                }
            )

        for t in tranches:
            t.pop("_market", None)
        out.append(
            {
                "id": family.id,
                "family_key": family.family_key,
                "title": family.title,
                "kind": family.kind,
                "tranches": tranches,
                "pairs": pairs,
            }
        )
    return out


@router.get("/{family_id}")
async def get_family(family_id: int):
    """One family by id."""
    for fam in await list_families():
        if fam["id"] == family_id:
            return fam
    raise HTTPException(status_code=404, detail="Family not found")
