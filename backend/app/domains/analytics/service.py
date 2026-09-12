"""Analytics.

Aggregates across opportunities, validations and realised positions. The single
most important number the platform can produce about itself is prediction
accuracy: whether the profit it forecast is the profit that arrived (spec §23).

Every rate here is reported with its denominator, and a rate computed from fewer
than a documented minimum is labelled as not yet meaningful rather than shown as
a clean percentage.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import ZERO, money, pct_of, ratio
from app.core.security import AuthContext
from app.domains.portfolio.service import portfolio_summary
from app.domains.validation.service import validation_metrics
from app.models.enums import Recommendation, RiskLevel
from app.models.opportunity import Opportunity
from app.models.portfolio import Outcome

#: Below this many closed positions, accuracy figures are noise.
MIN_POSITIONS_FOR_ACCURACY = 20


def prediction_accuracy(session: Session, auth: AuthContext) -> dict[str, Any]:
    """How close the predicted economics were to the realised ones."""
    outcomes = [
        row
        for row in session.scalars(
            select(Outcome).where(Outcome.organization_id == auth.organization_id)
        )
        if row.actual_profit is not None and row.predicted_total_profit is not None
    ]

    if not outcomes:
        return {
            "sample_size": 0,
            "is_meaningful": False,
            "caveat": (
                "No closed positions with both a prediction and an actual result. "
                "Prediction accuracy cannot be computed yet."
            ),
        }

    predicted_total = money(sum((row.predicted_total_profit for row in outcomes), ZERO))
    actual_total = money(sum((row.actual_profit for row in outcomes), ZERO))

    # Mean absolute percentage error against the predicted magnitude, skipping
    # rows where the prediction was zero (the percentage is undefined, not huge).
    errors: list[Decimal] = []
    for row in outcomes:
        predicted = row.predicted_total_profit
        if predicted and predicted != 0:
            errors.append(abs(row.actual_profit - predicted) / abs(predicted))
    mape = ratio(sum(errors) / Decimal(len(errors))) if errors else None

    within_10 = len([error for error in errors if error <= Decimal("0.10")])
    within_25 = len([error for error in errors if error <= Decimal("0.25")])
    optimistic = len([row for row in outcomes if row.actual_profit < row.predicted_total_profit])

    return {
        "sample_size": len(outcomes),
        "is_meaningful": len(outcomes) >= MIN_POSITIONS_FOR_ACCURACY,
        "predicted_profit_total": str(predicted_total),
        "actual_profit_total": str(actual_total),
        "total_variance": str(money(actual_total - predicted_total)),
        "total_variance_pct": (
            str(pct_of(money(actual_total - predicted_total), abs(predicted_total)))
            if predicted_total
            else None
        ),
        "mean_absolute_pct_error": None if mape is None else str(mape),
        "within_10_pct": {"count": within_10, "of": len(errors)},
        "within_25_pct": {"count": within_25, "of": len(errors)},
        "optimistic_predictions": {"count": optimistic, "of": len(outcomes)},
        "caveat": (
            None
            if len(outcomes) >= MIN_POSITIONS_FOR_ACCURACY
            else (
                f"{len(outcomes)} closed position(s); at least {MIN_POSITIONS_FOR_ACCURACY} "
                "are needed before these figures mean anything."
            )
        ),
    }


def opportunity_breakdown(session: Session, auth: AuthContext) -> dict[str, Any]:
    rows = list(
        session.scalars(
            select(Opportunity).where(Opportunity.organization_id == auth.organization_id)
        )
    )
    by_recommendation = {
        item.value: len([row for row in rows if row.recommendation == item.value])
        for item in Recommendation
    }
    by_risk = {
        item.value: len([row for row in rows if row.risk_level == item.value]) for item in RiskLevel
    }
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1

    buy_rows = [row for row in rows if row.recommendation == Recommendation.BUY.value]
    scored = [row for row in rows if row.score is not None]

    return {
        "total": len(rows),
        "by_recommendation": by_recommendation,
        "by_risk_level": by_risk,
        "by_status": by_status,
        "average_score": (
            str(ratio(sum((row.score for row in scored), ZERO) / Decimal(len(scored))))
            if scored
            else None
        ),
        "buy_expected_profit": str(money(sum((row.net_profit or ZERO) for row in buy_rows))),
        "buy_capital_required": str(money(sum((row.acquisition_cost or ZERO) for row in buy_rows))),
    }


def dashboard(session: Session, auth: AuthContext) -> dict[str, Any]:
    """Everything the dashboard needs, in one query set (spec §25)."""
    return {
        "opportunities": opportunity_breakdown(session, auth),
        "portfolio": portfolio_summary(session, auth),
        "prediction_accuracy": prediction_accuracy(session, auth),
        "validation": validation_metrics(session, auth),
    }
