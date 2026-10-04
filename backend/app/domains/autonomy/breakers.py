"""Capital circuit breakers.

Distinct from the provider circuit breaker, which protects a provider from being
hammered. These protect capital from the system.

A tripped breaker pauses autonomous deployment and requires a human to reset it.
It does not unwind positions, and it does not stop analysis: the system keeps
watching and keeps reporting, it just stops spending. Automatic recovery is
deliberately not implemented - a breaker that resets itself after a cooling-off
period is a breaker that hides the thing it tripped on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import ensure_utc, utcnow
from app.core.money import display_currency, money, pct_of
from app.core.security import AuthContext
from app.models.autonomy import AutonomyDecision, CapitalPosition, CircuitBreaker
from app.models.enums import AutonomyEventType, BreakerState, PositionStatus

DAILY_LOSS = "daily_loss"
PORTFOLIO_DRAWDOWN = "portfolio_drawdown"
FAILED_DECISIONS = "failed_decisions"
DAILY_DEPLOYMENT = "daily_deployment"


@dataclass(frozen=True)
class BreakerDefinition:
    code: str
    label: str
    description: str
    default_threshold: Decimal
    unit: str


#: Configurable defaults, not permanent business rules.
DEFINITIONS: tuple[BreakerDefinition, ...] = (
    BreakerDefinition(
        code=DAILY_LOSS,
        label="Daily realised loss",
        description="Total realised loss across positions closed today.",
        default_threshold=money("50"),
        unit="money",
    ),
    BreakerDefinition(
        code=PORTFOLIO_DRAWDOWN,
        label="Portfolio drawdown",
        description="Realised losses as a share of all capital ever committed.",
        default_threshold=Decimal("0.05"),
        unit="ratio",
    ),
    BreakerDefinition(
        code=FAILED_DECISIONS,
        label="Blocked decisions in a row",
        description=(
            "Consecutive decisions the engine refused to authorise. A run of these "
            "usually means an input is wrong, not that the market is."
        ),
        default_threshold=Decimal("3"),
        unit="count",
    ),
    BreakerDefinition(
        code=DAILY_DEPLOYMENT,
        label="Capital deployed today",
        description="Capital committed to new positions in one day.",
        default_threshold=money("250"),
        unit="money",
    ),
)


@dataclass
class BreakerReading:
    """One breaker, its threshold, and what was actually measured."""

    row: CircuitBreaker
    observed: Decimal
    would_trip: bool
    detail: str
    #: money, ratio or count. Published because a threshold of "0.05" and one of
    #: "50" are read completely differently, and the number alone does not say
    #: which it is.
    unit: str = "money"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.row.id,
            "code": self.row.code,
            "unit": self.unit,
            "label": self.row.label,
            "description": self.row.description,
            "is_enabled": self.row.is_enabled,
            "threshold": None if self.row.threshold is None else str(self.row.threshold),
            "observed": str(self.observed),
            "state": self.row.state,
            "would_trip": self.would_trip,
            "tripped_reason": self.row.tripped_reason,
            "detail": self.detail,
        }


def ensure(session: Session, auth: AuthContext) -> list[CircuitBreaker]:
    """Create any breaker that does not exist yet, enabled, at its default."""
    existing = {
        row.code: row
        for row in session.scalars(
            select(CircuitBreaker).where(CircuitBreaker.organization_id == auth.organization_id)
        )
    }
    for definition in DEFINITIONS:
        if definition.code in existing:
            continue
        row = CircuitBreaker(
            organization_id=auth.organization_id,
            code=definition.code,
            label=definition.label,
            description=definition.description,
            threshold=definition.default_threshold,
            is_enabled=True,
            state=BreakerState.OK.value,
        )
        session.add(row)
        existing[definition.code] = row
    session.flush()
    return [existing[definition.code] for definition in DEFINITIONS]


def _today_window() -> tuple[Any, Any]:
    now = utcnow()
    start = now - timedelta(days=1)
    return start, now


def read(session: Session, auth: AuthContext) -> list[BreakerReading]:
    """Measure every breaker against the current state of the portfolio."""
    rows = ensure(session, auth)
    start, _ = _today_window()

    positions = list(
        session.scalars(
            select(CapitalPosition).where(CapitalPosition.organization_id == auth.organization_id)
        )
    )
    closed_today = [
        row
        for row in positions
        if row.status == PositionStatus.CLOSED.value
        and row.closed_at is not None
        and ensure_utc(row.closed_at) >= start
    ]
    opened_today = [row for row in positions if ensure_utc(row.opened_at) >= start]

    daily_loss = money(
        sum(
            (abs(row.realized_profit) for row in closed_today if row.realized_profit < 0),
            Decimal("0"),
        )
    )
    committed = money(sum((row.capital_invested for row in positions), Decimal("0")))
    total_losses = money(
        sum(
            (
                abs(row.realized_profit)
                for row in positions
                if row.status == PositionStatus.CLOSED.value and row.realized_profit < 0
            ),
            Decimal("0"),
        )
    )
    drawdown = pct_of(total_losses, committed) or Decimal("0")
    deployed_today = money(sum((row.capital_invested for row in opened_today), Decimal("0")))

    recent = list(
        session.scalars(
            select(AutonomyDecision)
            .where(AutonomyDecision.organization_id == auth.organization_id)
            .order_by(AutonomyDecision.created_at.desc())
            .limit(20)
        )
    )
    consecutive_blocked = 0
    for decision in recent:
        if decision.outcome == "blocked":
            consecutive_blocked += 1
        else:
            break

    measured: dict[str, tuple[Decimal, str]] = {
        DAILY_LOSS: (
            daily_loss,
            f"{display_currency(daily_loss)} realised loss across "
            f"{len(closed_today)} position(s) closed in the last 24 hours.",
        ),
        PORTFOLIO_DRAWDOWN: (
            drawdown,
            f"{drawdown:.1%} of {display_currency(committed)} ever committed has been "
            "lost on closed positions.",
        ),
        FAILED_DECISIONS: (
            Decimal(consecutive_blocked),
            f"{consecutive_blocked} decision(s) blocked in a row.",
        ),
        DAILY_DEPLOYMENT: (
            deployed_today,
            f"{display_currency(deployed_today)} committed to "
            f"{len(opened_today)} new position(s) in the last 24 hours.",
        ),
    }

    units = {definition.code: definition.unit for definition in DEFINITIONS}
    readings: list[BreakerReading] = []
    for row in rows:
        observed, detail = measured.get(row.code, (Decimal("0"), "Not measured."))
        threshold = row.threshold
        readings.append(
            BreakerReading(
                row=row,
                observed=observed,
                would_trip=bool(row.is_enabled and threshold is not None and observed >= threshold),
                detail=detail,
                unit=units.get(row.code, "money"),
            )
        )
    return readings


def evaluate(session: Session, auth: AuthContext) -> list[BreakerReading]:
    """Measure every breaker and trip the ones that are over.

    Tripping is recorded on the row and in the audit log. A breaker already
    tripped stays tripped until a person resets it: re-measuring below the
    threshold does not clear it, because the point of a breaker is that the
    thing it caught gets looked at.
    """
    from app.domains.autonomy.policy import record_event

    readings = read(session, auth)
    for reading in readings:
        if not reading.would_trip or reading.row.state == BreakerState.TRIPPED.value:
            continue
        reading.row.state = BreakerState.TRIPPED.value
        reading.row.observed_value = reading.observed
        reading.row.tripped_at = utcnow()
        reading.row.tripped_reason = reading.detail
        record_event(
            session,
            auth,
            AutonomyEventType.BREAKER_TRIPPED,
            message=f"{reading.row.label} tripped. {reading.detail}",
            payload={
                "code": reading.row.code,
                "threshold": str(reading.row.threshold),
                "observed": str(reading.observed),
            },
        )
    session.flush()
    return readings


def tripped(session: Session, auth: AuthContext) -> list[CircuitBreaker]:
    return list(
        session.scalars(
            select(CircuitBreaker).where(
                CircuitBreaker.organization_id == auth.organization_id,
                CircuitBreaker.state == BreakerState.TRIPPED.value,
                CircuitBreaker.is_enabled.is_(True),
            )
        )
    )


def reset(
    session: Session, auth: AuthContext, breaker_id: str, *, actor: str | None = None
) -> CircuitBreaker | None:
    """Clear one breaker. Always a human action."""
    from app.domains.autonomy.policy import record_event

    row = session.scalar(
        select(CircuitBreaker).where(
            CircuitBreaker.organization_id == auth.organization_id,
            CircuitBreaker.id == breaker_id,
        )
    )
    if row is None:
        return None
    row.state = BreakerState.OK.value
    row.reset_at = utcnow()
    row.reset_by = actor or auth.email
    row.tripped_reason = None
    session.flush()
    record_event(
        session,
        auth,
        AutonomyEventType.BREAKER_RESET,
        message=f"{row.label} reset.",
        actor=actor or auth.email,
        payload={"code": row.code},
    )
    return row
