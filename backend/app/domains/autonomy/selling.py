"""When to get out of a position.

Buying well is half the decision. The other half is knowing when a position has
stopped being the trade you entered, and that question is answered from
different evidence: not the spread that justified the purchase, but what the
exit market is doing now, how long the capital has been tied up, and what is
still left to make.

Four answers, and the distinction between them is what makes this useful:

``hold``
    The position is doing what it was bought to do. No action.

``sell_now``
    The remaining profit is still there and something is eroding it: the price
    is falling, sellers are arriving, or the inventory is approaching the age
    the policy allows. Take it while it is there.

``reprice``
    It is not selling, but the economics survive a lower price. The break-even
    price says how far there is to go.

``liquidate``
    The position is past the policy's age limit or the exit price has fallen
    through break-even. The decision is no longer about profit, it is about
    getting the capital back.

Every recommendation is exactly that. Execution stays a human action until an
authorised marketplace integration exists (spec §15), so nothing here reprices
or lists anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import display_currency, money, pct_of
from app.core.security import AuthContext
from app.domains.autonomy.policy import PolicyView
from app.domains.autonomy.positions import PositionView, list_positions
from app.domains.pricing.statistics import analyze_prices
from app.models.autonomy import CapitalPosition
from app.models.catalog import MarketplaceListing
from app.models.enums import ExecutionMode, PositionStatus, TrendDirection

#: Age, as a share of the policy limit, at which a position is old enough that
#: turning capital over starts to matter more than the last few percent.
AGEING_SHARE = Decimal("0.70")
#: Exit price falling by at least this much over the reference window is enough
#: to stop waiting for a better one.
FALLING_THRESHOLD = Decimal("-0.08")


class SellAction(StrEnum):
    HOLD = "hold"
    SELL_NOW = "sell_now"
    REPRICE = "reprice"
    LIQUIDATE = "liquidate"


@dataclass
class SellRecommendation:
    position_id: str
    action: SellAction
    summary: str
    reasons: list[str] = field(default_factory=list)
    #: Present for ``reprice``: the lowest price that still clears costs.
    floor_price: Decimal | None = None
    current_price: Decimal | None = None
    days_held: int = 0
    remaining_units: int = 0
    capital_at_risk: Decimal = Decimal("0")

    @property
    def is_urgent(self) -> bool:
        return self.action in {SellAction.SELL_NOW, SellAction.LIQUIDATE}

    def as_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "action": self.action.value,
            "summary": self.summary,
            "reasons": self.reasons,
            "floor_price": None if self.floor_price is None else str(self.floor_price),
            "current_price": None if self.current_price is None else str(self.current_price),
            "days_held": self.days_held,
            "remaining_units": self.remaining_units,
            "capital_at_risk": str(self.capital_at_risk),
            "is_urgent": self.is_urgent,
        }


def _exit_market(session: Session, position: CapitalPosition) -> MarketplaceListing | None:
    """The listing the position would be sold on."""
    if not position.target_marketplace or not position.product_id:
        return None
    return session.scalar(
        select(MarketplaceListing).where(
            MarketplaceListing.organization_id == position.organization_id,
            MarketplaceListing.product_id == position.product_id,
            MarketplaceListing.marketplace == position.target_marketplace,
        )
    )


def assess_position(
    session: Session, item: PositionView, policy: PolicyView
) -> SellRecommendation:
    """Decide what to do with one open position, from current evidence."""
    from app.domains.opportunities.analysis import load_price_points

    position = item.row
    remaining = max(0, position.quantity - position.quantity_sold)
    capital_at_risk = money(position.unit_cost * remaining)
    reasons: list[str] = []

    listing = _exit_market(session, position)
    current_price = listing.current_price if listing else None
    prices = analyze_prices(load_price_points(session, listing.id)) if listing else None
    reference = prices.reference if prices else None

    # The floor is the unit cost: below it the position is losing money on every
    # remaining unit, whatever the original plan said. Selling fees are not
    # re-derived here because they belong to the profitability engine; this is a
    # floor, not a re-underwrite.
    floor = money(position.unit_cost)

    age_limit = policy.maximum_inventory_age_days
    ageing = item.days_held >= int(age_limit * AGEING_SHARE)
    past_limit = item.days_held >= age_limit

    if current_price is not None:
        reasons.append(
            f"The exit market is at {display_currency(current_price)}; the position cost "
            f"{display_currency(position.unit_cost)} per unit."
        )
    else:
        reasons.append("No current exit price is recorded for this product.")

    if reference is not None and reference.trend_pct is not None:
        reasons.append(
            f"The exit price has moved {reference.trend_pct:+.0%} over "
            f"{reference.window_days} days."
        )

    reasons.append(
        f"Held {item.days_held} day(s); the policy allows {age_limit}."
    )

    # --- past the age limit, or under water ------------------------------
    if past_limit:
        return SellRecommendation(
            position_id=position.id,
            action=SellAction.LIQUIDATE,
            summary=(
                f"Past the {age_limit} day limit at {item.days_held} days. The question "
                "is no longer the last few percent of profit, it is getting the capital "
                "back."
            ),
            reasons=reasons,
            floor_price=floor,
            current_price=current_price,
            days_held=item.days_held,
            remaining_units=remaining,
            capital_at_risk=capital_at_risk,
        )

    if current_price is not None and current_price < floor:
        shortfall = money(floor - current_price)
        reasons.append(
            f"Every remaining unit sells for {display_currency(shortfall)} less than it "
            "cost."
        )
        return SellRecommendation(
            position_id=position.id,
            action=SellAction.LIQUIDATE,
            summary=(
                "The exit price has fallen through what the units cost. Holding for a "
                "recovery is a new bet, not the one that was underwritten."
            ),
            reasons=reasons,
            floor_price=floor,
            current_price=current_price,
            days_held=item.days_held,
            remaining_units=remaining,
            capital_at_risk=capital_at_risk,
        )

    # --- something is eroding a profit that is still there ---------------
    falling = (
        reference is not None
        and reference.trend is TrendDirection.FALLING
        and reference.trend_pct is not None
        and reference.trend_pct <= FALLING_THRESHOLD
    )
    if falling or ageing:
        cause = (
            "the exit price is trending down"
            if falling
            else f"the capital has been tied up {item.days_held} days"
        )
        return SellRecommendation(
            position_id=position.id,
            action=SellAction.SELL_NOW,
            summary=(
                f"Still profitable, but {cause}. Taking it now is worth more than "
                "waiting for a better price that is moving the wrong way."
            ),
            reasons=reasons,
            floor_price=floor,
            current_price=current_price,
            days_held=item.days_held,
            remaining_units=remaining,
            capital_at_risk=capital_at_risk,
        )

    # --- not moving, but the economics survive a lower price -------------
    if item.days_held >= 14 and position.quantity_sold == 0 and current_price is not None:
        headroom = pct_of(money(current_price - floor), current_price)
        if headroom is not None and headroom > Decimal("0.05"):
            reasons.append(
                f"There is {headroom:.0%} between the current price and the "
                f"{display_currency(floor)} floor."
            )
            return SellRecommendation(
                position_id=position.id,
                action=SellAction.REPRICE,
                summary=(
                    f"Nothing has sold in {item.days_held} days. The price can come down "
                    f"to {display_currency(floor)} before the position stops making "
                    "money."
                ),
                reasons=reasons,
                floor_price=floor,
                current_price=current_price,
                days_held=item.days_held,
                remaining_units=remaining,
                capital_at_risk=capital_at_risk,
            )

    return SellRecommendation(
        position_id=position.id,
        action=SellAction.HOLD,
        summary="Doing what it was bought to do. Nothing to act on.",
        reasons=reasons,
        floor_price=floor,
        current_price=current_price,
        days_held=item.days_held,
        remaining_units=remaining,
        capital_at_risk=capital_at_risk,
    )


def review(
    session: Session,
    auth: AuthContext,
    policy: PolicyView,
    *,
    execution_mode: ExecutionMode | None = None,
) -> dict[str, Any]:
    """Every open position, with what to do about it.

    Ordered by urgency then by capital: the positions that need a decision come
    first, and among those the ones with the most money in them.
    """
    items = list_positions(
        session, auth, execution_mode=execution_mode, status=PositionStatus.OPEN
    )
    recommendations = [assess_position(session, item, policy) for item in items]
    recommendations.sort(
        key=lambda entry: (not entry.is_urgent, -entry.capital_at_risk),
    )

    by_action: dict[str, int] = {}
    for entry in recommendations:
        by_action[entry.action.value] = by_action.get(entry.action.value, 0) + 1

    urgent = [entry for entry in recommendations if entry.is_urgent]
    return {
        "items": [entry.as_dict() for entry in recommendations],
        "total": len(recommendations),
        "by_action": by_action,
        "needs_attention": len(urgent),
        "capital_needing_attention": str(
            money(sum((entry.capital_at_risk for entry in urgent), Decimal("0")))
        ),
        "note": (
            "Recommendations only. Spreadline does not list, reprice or sell anything: "
            "an authorised marketplace integration would be needed for that, and the "
            "shape of this output is what such an executor would read."
        ),
    }
