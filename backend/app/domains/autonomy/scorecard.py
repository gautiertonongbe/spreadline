"""How well the system has actually performed.

Autonomy is earned against this. Every figure is computed from records the
platform already keeps - decisions, purchases, sales, outcomes and positions -
and every one of them reports the sample it was computed from, because a 100%
precision over three decisions is not a fact about the system.

Nothing here is smoothed, weighted or projected. A metric with no sample is
``None`` and reads as "not enough evidence", never as zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import ensure_utc, utcnow
from app.core.money import money, pct_of, ratio
from app.core.security import AuthContext
from app.models.autonomy import AutonomyDecision, CapitalPosition
from app.models.enums import ExecutionMode, PositionStatus
from app.models.portfolio import Outcome

#: Below this many closed positions, ratios are reported with the sample size
#: attached and the gate refuses to treat them as evidence.
MEANINGFUL_SAMPLE = 20


@dataclass
class Metric:
    """One measurement, with the sample behind it.

    The sample is not decoration. Every ratio here is meaningless without it,
    and the gate reads ``sample`` before it reads ``value``.
    """

    key: str
    label: str
    value: Decimal | None
    sample: int
    unit: str = "ratio"
    detail: str = ""

    @property
    def is_meaningful(self) -> bool:
        return self.value is not None and self.sample >= MEANINGFUL_SAMPLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": None if self.value is None else str(self.value),
            "sample": self.sample,
            "unit": self.unit,
            "detail": self.detail,
            "is_meaningful": self.is_meaningful,
        }


@dataclass
class Scorecard:
    metrics: list[Metric] = field(default_factory=list)
    decisions_total: int = 0
    decisions_autonomous: int = 0
    human_overrides: int = 0

    def get(self, key: str) -> Metric | None:
        return next((metric for metric in self.metrics if metric.key == key), None)

    def value(self, key: str) -> Decimal | None:
        metric = self.get(key)
        return metric.value if metric else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "decisions_total": self.decisions_total,
            "decisions_autonomous": self.decisions_autonomous,
            "human_overrides": self.human_overrides,
            "meaningful_sample_threshold": MEANINGFUL_SAMPLE,
            "metrics": [metric.as_dict() for metric in self.metrics],
        }


def _positions(session: Session, auth: AuthContext) -> list[CapitalPosition]:
    return list(
        session.scalars(
            select(CapitalPosition).where(CapitalPosition.organization_id == auth.organization_id)
        )
    )


def build(
    session: Session,
    auth: AuthContext,
    *,
    execution_mode: ExecutionMode | None = None,
) -> Scorecard:
    """Compute the scorecard.

    ``execution_mode`` narrows it to shadow or live, which is how the two are
    compared: the entire point of paper trading is that the same measurements
    are taken of both.
    """
    decisions = list(
        session.scalars(
            select(AutonomyDecision).where(AutonomyDecision.organization_id == auth.organization_id)
        )
    )
    positions = _positions(session, auth)
    if execution_mode is not None:
        decisions = [row for row in decisions if row.execution_mode == execution_mode.value]
        positions = [row for row in positions if row.execution_mode == execution_mode.value]

    outcomes = list(
        session.scalars(select(Outcome).where(Outcome.organization_id == auth.organization_id))
    )

    authorized = [row for row in decisions if row.outcome == "authorized"]
    closed = [row for row in positions if row.status == PositionStatus.CLOSED.value]
    metrics: list[Metric] = []

    # --- did the buys make money -------------------------------------------
    winners = [row for row in closed if row.realized_profit > 0]
    metrics.append(
        Metric(
            key="buy_precision",
            label="Buy precision",
            value=pct_of(Decimal(len(winners)), Decimal(len(closed))) if closed else None,
            sample=len(closed),
            detail="Closed positions that made money, over all closed positions.",
        )
    )

    invested = sum((row.capital_invested for row in closed), Decimal("0"))
    realized = sum((row.realized_profit for row in closed), Decimal("0"))
    metrics.append(
        Metric(
            key="realized_roi",
            label="Realised return",
            value=pct_of(money(realized), money(invested)),
            sample=len(closed),
            detail="Profit actually realised against capital actually committed.",
        )
    )

    losers = [row for row in closed if row.realized_profit < 0]
    metrics.append(
        Metric(
            key="loss_rate",
            label="Loss rate",
            value=pct_of(Decimal(len(losers)), Decimal(len(closed))) if closed else None,
            sample=len(closed),
            detail="Closed positions that lost money.",
        )
    )

    # --- was the prediction any good ---------------------------------------
    measured = [row for row in outcomes if row.predicted_total_profit is not None]
    if measured:
        errors = []
        for row in measured:
            predicted = row.predicted_total_profit or Decimal("0")
            actual = row.actual_profit or Decimal("0")
            if predicted != 0:
                errors.append(abs((actual - predicted) / predicted))
        accuracy = ratio(Decimal("1") - (sum(errors) / Decimal(len(errors)))) if errors else None
        metrics.append(
            Metric(
                key="prediction_accuracy",
                label="Prediction accuracy",
                value=None if accuracy is None else max(Decimal("0"), accuracy),
                sample=len(errors),
                detail="One minus the mean absolute error of predicted profit.",
            )
        )
    else:
        metrics.append(
            Metric(
                key="prediction_accuracy",
                label="Prediction accuracy",
                value=None,
                sample=0,
                detail="No closed position has been compared against its prediction yet.",
            )
        )

    # --- how the capital behaved -------------------------------------------
    open_positions = [row for row in positions if row.status == PositionStatus.OPEN.value]
    deployed = sum((row.capital_invested for row in open_positions), Decimal("0"))
    metrics.append(
        Metric(
            key="capital_deployed",
            label="Capital deployed",
            value=money(deployed),
            sample=len(open_positions),
            unit="money",
            detail="Committed to positions still open.",
        )
    )

    ages = [
        Decimal((ensure_utc(row.closed_at) - ensure_utc(row.opened_at)).days)
        for row in closed
        if row.closed_at is not None
    ]
    metrics.append(
        Metric(
            key="days_to_sell",
            label="Average days to sell",
            value=ratio(sum(ages) / Decimal(len(ages))) if ages else None,
            sample=len(ages),
            unit="days",
            detail="From opening a position to closing it.",
        )
    )

    metrics.append(
        Metric(
            key="max_drawdown",
            label="Maximum drawdown",
            value=_max_drawdown(closed),
            sample=len(closed),
            detail="The worst single realised loss against the capital committed to it.",
        )
    )

    sold_units = sum(row.quantity_sold for row in positions)
    bought_units = sum(row.quantity for row in positions)
    metrics.append(
        Metric(
            key="sell_through",
            label="Sell-through",
            value=pct_of(Decimal(sold_units), Decimal(bought_units)) if bought_units else None,
            sample=bought_units,
            detail="Units sold against units bought.",
        )
    )

    overrides = [row for row in decisions if row.reason_code == "HUMAN_OVERRIDE"]
    return Scorecard(
        metrics=metrics,
        decisions_total=len(decisions),
        decisions_autonomous=len(authorized),
        human_overrides=len(overrides),
    )


def _max_drawdown(closed: list[CapitalPosition]) -> Decimal | None:
    """The worst single realised loss, as a share of what was committed to it.

    Position-level rather than portfolio-level because that is what the data
    supports honestly: a portfolio equity curve needs mark-to-market valuations
    over time, and inventing one from purchase prices would be a fabricated
    series presented as a risk measure.
    """
    losses = [
        pct_of(abs(row.realized_profit), row.capital_invested)
        for row in closed
        if row.realized_profit < 0 and row.capital_invested > 0
    ]
    real = [value for value in losses if value is not None]
    return max(real) if real else Decimal("0") if closed else None


def snapshot_payload(scorecard: Scorecard) -> dict[str, Any]:
    return {"computed_at": utcnow().isoformat(), **scorecard.as_dict()}
