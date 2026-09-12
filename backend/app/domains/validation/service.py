"""Personal testing mode (spec §22).

The operator checks a recommendation by hand and records what was actually true.
That record is the labelled dataset the platform will eventually be judged on, so
two things matter more than convenience:

* The platform's own prediction is snapshotted at recording time. A later rescore
  must not be able to move the goalposts after the fact.
* Nothing is inferred. If the operator did not say whether the match was correct,
  it stays null rather than being assumed from the price agreeing.

The target is 100-200 hand-checked products before automated decisions are
trusted at scale (spec §45).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.money import money, ratio
from app.core.security import AuthContext
from app.models.catalog import MarketplaceListing
from app.models.enums import OpportunityEventType
from app.models.opportunity import Opportunity, OpportunityValidation

#: Tolerance within which a predicted price counts as accurate.
PRICE_TOLERANCE = Decimal("0.05")
#: Tolerance within which a profit prediction counts as accurate.
PROFIT_TOLERANCE = Decimal("0.15")
#: Hand-checked products needed before automated decisions carry real weight.
VALIDATION_TARGET = 150


@dataclass
class ValidationInput:
    actual_source_price: Decimal | None = None
    actual_target_price: Decimal | None = None
    actual_availability: str | None = None
    match_confirmed: bool | None = None
    profit_estimate_correct: bool | None = None
    would_buy: bool | None = None
    notes: str | None = None


def record_validation(
    session: Session,
    auth: AuthContext,
    opportunity: Opportunity,
    data: ValidationInput,
) -> OpportunityValidation:
    """Store a hand-checked observation against an opportunity."""
    source = session.get(MarketplaceListing, opportunity.source_listing_id)
    target = session.get(MarketplaceListing, opportunity.target_listing_id)

    predicted_source = source.current_price if source else None
    predicted_target = target.current_price if target else None

    validation = OpportunityValidation(
        organization_id=auth.organization_id,
        opportunity_id=opportunity.id,
        tested_by=auth.actor,
        tested_at=utcnow(),
        actual_source_price=money(data.actual_source_price)
        if data.actual_source_price is not None
        else None,
        actual_target_price=money(data.actual_target_price)
        if data.actual_target_price is not None
        else None,
        actual_availability=data.actual_availability,
        match_confirmed=data.match_confirmed,
        profit_estimate_correct=data.profit_estimate_correct,
        would_buy=data.would_buy,
        notes=data.notes,
        # Frozen at test time so the measurement survives any later rescore.
        predicted_source_price=predicted_source,
        predicted_target_price=predicted_target,
        predicted_net_profit=opportunity.net_profit,
        predicted_roi=opportunity.roi,
        predicted_recommendation=opportunity.recommendation,
    )
    if validation.actual_source_price is not None and predicted_source is not None:
        validation.source_price_delta = money(validation.actual_source_price - predicted_source)
    if validation.actual_target_price is not None and predicted_target is not None:
        validation.target_price_delta = money(validation.actual_target_price - predicted_target)

    session.add(validation)
    session.flush()

    from app.domains.opportunities.service import record_event

    record_event(
        session,
        auth,
        opportunity,
        OpportunityEventType.VALIDATION_RECORDED,
        message="Hand-checked by the operator.",
        payload={
            "match_confirmed": data.match_confirmed,
            "would_buy": data.would_buy,
            "source_price_delta": str(validation.source_price_delta)
            if validation.source_price_delta is not None
            else None,
            "target_price_delta": str(validation.target_price_delta)
            if validation.target_price_delta is not None
            else None,
        },
    )
    return validation


def validation_metrics(session: Session, auth: AuthContext) -> dict[str, Any]:
    """Accuracy of the platform against hand-checked reality (spec §45).

    Every rate is reported with the denominator it was computed from. A 100%
    match accuracy over four samples is not a measurement, and showing the count
    next to the percentage is what stops it being read as one.
    """
    rows = list(
        session.scalars(
            select(OpportunityValidation).where(
                OpportunityValidation.organization_id == auth.organization_id
            )
        )
    )
    total = len(rows)

    def rate(numerator: int, denominator: int) -> str | None:
        if denominator == 0:
            return None
        return str(ratio(Decimal(numerator) / Decimal(denominator)))

    match_checked = [row for row in rows if row.match_confirmed is not None]
    match_correct = [row for row in match_checked if row.match_confirmed]

    profit_checked = [row for row in rows if row.profit_estimate_correct is not None]
    profit_correct = [row for row in profit_checked if row.profit_estimate_correct]

    price_checked = [
        row
        for row in rows
        if row.actual_source_price is not None and row.predicted_source_price not in (None, 0)
    ]
    price_accurate = [
        row
        for row in price_checked
        if abs(row.actual_source_price - row.predicted_source_price)  # type: ignore[operator]
        / row.predicted_source_price  # type: ignore[operator]
        <= PRICE_TOLERANCE
    ]

    # BUY precision: of the products the platform said BUY on, how many would the
    # operator actually have bought.
    buy_rows = [row for row in rows if row.predicted_recommendation == "buy"]
    buy_checked = [row for row in buy_rows if row.would_buy is not None]
    buy_confirmed = [row for row in buy_checked if row.would_buy]
    false_positives = [row for row in buy_checked if row.would_buy is False]

    # Missed opportunities: the platform said PASS or REVIEW and the operator
    # would have bought anyway.
    non_buy_checked = [
        row
        for row in rows
        if row.predicted_recommendation in {"pass", "review"} and row.would_buy is not None
    ]
    missed = [row for row in non_buy_checked if row.would_buy]

    return {
        "validated_count": total,
        "target_count": VALIDATION_TARGET,
        "progress_pct": str(
            ratio(Decimal(min(total, VALIDATION_TARGET)) / Decimal(VALIDATION_TARGET))
        ),
        "is_statistically_meaningful": total >= VALIDATION_TARGET,
        "match_accuracy": {
            "rate": rate(len(match_correct), len(match_checked)),
            "correct": len(match_correct),
            "checked": len(match_checked),
        },
        "price_accuracy": {
            "rate": rate(len(price_accurate), len(price_checked)),
            "within_tolerance": len(price_accurate),
            "checked": len(price_checked),
            "tolerance": str(PRICE_TOLERANCE),
        },
        "profit_accuracy": {
            "rate": rate(len(profit_correct), len(profit_checked)),
            "correct": len(profit_correct),
            "checked": len(profit_checked),
        },
        "buy_precision": {
            "rate": rate(len(buy_confirmed), len(buy_checked)),
            "confirmed": len(buy_confirmed),
            "checked": len(buy_checked),
        },
        "false_positive_rate": {
            "rate": rate(len(false_positives), len(buy_checked)),
            "false_positives": len(false_positives),
            "checked": len(buy_checked),
        },
        "missed_opportunities": {
            "rate": rate(len(missed), len(non_buy_checked)),
            "missed": len(missed),
            "checked": len(non_buy_checked),
        },
        "caveat": (
            None
            if total >= VALIDATION_TARGET
            else (
                f"{total} of {VALIDATION_TARGET} hand-checked products. These rates are "
                "indicative only and should not yet be used to justify automated decisions."
            )
        ),
    }
