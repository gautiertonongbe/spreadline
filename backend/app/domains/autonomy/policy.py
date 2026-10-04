"""The investment policy, and the fact that it is never edited in place.

The human defines the policy. The system executes within it. That division only
holds if the policy a decision was made under can still be read back exactly as
it was, so every change writes a new version and deactivates the old one.
Nothing here is updated in place, and nothing raises its own limits.

The defaults are deliberately the safest possible configuration: level 2 (human
approval), zero autonomous capital, observe mode. Autonomy is something a person
turns on, one step at a time, after the system has demonstrated it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import iso_utc, utcnow
from app.core.errors import ValidationError
from app.core.money import money, ratio
from app.core.security import AuthContext
from app.models.autonomy import AutonomyEvent, AutonomyPolicy
from app.models.enums import AutonomyEventType, AutonomyLevel, ExecutionMode, RiskLevel

#: The fields a caller may change. Anything else about a policy is derived or
#: administrative, and listing them explicitly keeps an unknown key from being
#: silently accepted and silently ignored.
EDITABLE_FIELDS = (
    "autonomy_level",
    "capital_limit",
    "max_position_size",
    "max_position_pct",
    "max_loss_per_position",
    "max_daily_deployment",
    "minimum_roi",
    "minimum_profit",
    "maximum_risk",
    "minimum_match_confidence",
    "minimum_data_quality",
    "maximum_inventory_age_days",
    "maximum_category_exposure",
    "maximum_brand_exposure",
    "maximum_marketplace_exposure",
    "require_human_approval",
    "emergency_stop_enabled",
    "execution_mode",
    "note",
)

_DECIMAL_FIELDS = {
    "capital_limit",
    "max_position_size",
    "max_loss_per_position",
    "max_daily_deployment",
    "minimum_profit",
}
_RATIO_FIELDS = {
    "max_position_pct",
    "minimum_roi",
    "minimum_match_confidence",
    "minimum_data_quality",
    "maximum_category_exposure",
    "maximum_brand_exposure",
    "maximum_marketplace_exposure",
}


@dataclass(frozen=True)
class PolicyView:
    """A policy as the rest of the system reads it, with types resolved."""

    id: str
    version: str
    autonomy_level: AutonomyLevel
    execution_mode: ExecutionMode
    capital_limit: Decimal
    max_position_size: Decimal | None
    max_position_pct: Decimal
    max_loss_per_position: Decimal | None
    max_daily_deployment: Decimal | None
    minimum_roi: Decimal
    minimum_profit: Decimal
    maximum_risk: RiskLevel
    minimum_match_confidence: Decimal
    minimum_data_quality: Decimal
    maximum_inventory_age_days: int
    maximum_category_exposure: Decimal
    maximum_brand_exposure: Decimal
    maximum_marketplace_exposure: Decimal
    require_human_approval: bool
    emergency_stop_enabled: bool
    emergency_stop_active: bool
    emergency_stop_reason: str | None

    @property
    def may_deploy_capital(self) -> bool:
        """Every condition that has to hold before any capital moves.

        Four independent switches, all of which must agree. They are separate on
        purpose: one of them being wrong should never be enough.
        """
        return (
            self.autonomy_level.deploys_capital
            and not self.require_human_approval
            and not self.emergency_stop_active
            and self.execution_mode is not ExecutionMode.OBSERVE
        )

    @property
    def is_live(self) -> bool:
        return self.execution_mode is ExecutionMode.LIVE

    def position_cap(self, available_capital: Decimal) -> Decimal:
        """The most that may go into one position. The tighter limit binds."""
        by_pct = money(available_capital * self.max_position_pct)
        if self.max_position_size is None:
            return by_pct
        return min(by_pct, money(self.max_position_size))

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "autonomy_level": int(self.autonomy_level),
            "autonomy_level_label": self.autonomy_level.label,
            "execution_mode": self.execution_mode.value,
            "capital_limit": str(self.capital_limit),
            "max_position_size": (
                None if self.max_position_size is None else str(self.max_position_size)
            ),
            "max_position_pct": str(self.max_position_pct),
            "max_loss_per_position": (
                None if self.max_loss_per_position is None else str(self.max_loss_per_position)
            ),
            "max_daily_deployment": (
                None if self.max_daily_deployment is None else str(self.max_daily_deployment)
            ),
            "minimum_roi": str(self.minimum_roi),
            "minimum_profit": str(self.minimum_profit),
            "maximum_risk": self.maximum_risk.value,
            "minimum_match_confidence": str(self.minimum_match_confidence),
            "minimum_data_quality": str(self.minimum_data_quality),
            "maximum_inventory_age_days": self.maximum_inventory_age_days,
            "maximum_category_exposure": str(self.maximum_category_exposure),
            "maximum_brand_exposure": str(self.maximum_brand_exposure),
            "maximum_marketplace_exposure": str(self.maximum_marketplace_exposure),
            "require_human_approval": self.require_human_approval,
            "emergency_stop_enabled": self.emergency_stop_enabled,
            "emergency_stop_active": self.emergency_stop_active,
            "emergency_stop_reason": self.emergency_stop_reason,
            "may_deploy_capital": self.may_deploy_capital,
        }


def view(row: AutonomyPolicy) -> PolicyView:
    return PolicyView(
        id=row.id,
        version=row.version,
        autonomy_level=AutonomyLevel(row.autonomy_level),
        execution_mode=ExecutionMode(row.execution_mode),
        capital_limit=money(row.capital_limit),
        max_position_size=None if row.max_position_size is None else money(row.max_position_size),
        max_position_pct=ratio(row.max_position_pct),
        max_loss_per_position=(
            None if row.max_loss_per_position is None else money(row.max_loss_per_position)
        ),
        max_daily_deployment=(
            None if row.max_daily_deployment is None else money(row.max_daily_deployment)
        ),
        minimum_roi=ratio(row.minimum_roi),
        minimum_profit=money(row.minimum_profit),
        maximum_risk=RiskLevel(row.maximum_risk),
        minimum_match_confidence=ratio(row.minimum_match_confidence),
        minimum_data_quality=ratio(row.minimum_data_quality),
        maximum_inventory_age_days=row.maximum_inventory_age_days,
        maximum_category_exposure=ratio(row.maximum_category_exposure),
        maximum_brand_exposure=ratio(row.maximum_brand_exposure),
        maximum_marketplace_exposure=ratio(row.maximum_marketplace_exposure),
        require_human_approval=row.require_human_approval,
        emergency_stop_enabled=row.emergency_stop_enabled,
        emergency_stop_active=row.emergency_stop_active,
        emergency_stop_reason=row.emergency_stop_reason,
    )


def active_policy(session: Session, auth: AuthContext) -> AutonomyPolicy:
    """The policy in force, creating the safe default on first use.

    The default is level 2 with zero autonomous capital and observe mode: the
    system analyses and recommends, and a person does everything else. An
    organisation that has never configured autonomy has not authorised any.
    """
    row = session.scalar(
        select(AutonomyPolicy).where(
            AutonomyPolicy.organization_id == auth.organization_id,
            AutonomyPolicy.is_active.is_(True),
        )
    )
    if row is not None:
        return row

    row = AutonomyPolicy(
        organization_id=auth.organization_id,
        version="v1.0",
        is_active=True,
        autonomy_level=int(AutonomyLevel.HUMAN_APPROVAL),
        capital_limit=Decimal("0"),
        execution_mode=ExecutionMode.OBSERVE.value,
        require_human_approval=True,
        created_by=auth.email,
        note="Default policy. No autonomous capital until a person authorises it.",
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        auth,
        AutonomyEventType.POLICY_CHANGED,
        message="Default policy created: human approval, no autonomous capital.",
        payload={"version": row.version},
    )
    return row


def _next_version(current: str) -> str:
    """v1.0 -> v1.1 -> v1.2. Minor only; a human names a major version."""
    try:
        major, minor = current.lstrip("v").split(".", 1)
        return f"v{int(major)}.{int(minor) + 1}"
    except (ValueError, AttributeError):
        return "v1.1"


def update_policy(
    session: Session,
    auth: AuthContext,
    changes: dict[str, Any],
    *,
    version: str | None = None,
) -> AutonomyPolicy:
    """Write a new policy version. The previous one is kept, not overwritten.

    Raising the autonomy level or the capital limit through this function is
    allowed because a person is asking for it. What is not allowed is the system
    doing it to itself: nothing inside the autonomy domain calls this.
    """
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValidationError(f"Unknown policy fields: {', '.join(sorted(unknown))}.")

    current = active_policy(session, auth)
    values: dict[str, Any] = {field: getattr(current, field) for field in EDITABLE_FIELDS}

    for field, value in changes.items():
        if value is None and field in {
            "max_position_size",
            "max_loss_per_position",
            "max_daily_deployment",
            "note",
        }:
            values[field] = None
            continue
        if field in _DECIMAL_FIELDS:
            values[field] = money(value)
        elif field in _RATIO_FIELDS:
            values[field] = ratio(value)
        elif field == "autonomy_level":
            values[field] = int(AutonomyLevel(int(value)))
        elif field == "execution_mode":
            values[field] = ExecutionMode(str(value)).value
        elif field == "maximum_risk":
            values[field] = RiskLevel(str(value)).value
        else:
            values[field] = value

    _validate(values)

    current.is_active = False
    fresh = AutonomyPolicy(
        organization_id=auth.organization_id,
        version=version or _next_version(current.version),
        is_active=True,
        created_by=auth.email,
        # The stop is carried forward deliberately. A policy edit must not be a
        # way to clear an emergency stop by accident.
        emergency_stop_active=current.emergency_stop_active,
        emergency_stop_reason=current.emergency_stop_reason,
        emergency_stop_at=current.emergency_stop_at,
        **values,
    )
    session.add(fresh)
    session.flush()

    changed = {
        field: str(values[field])
        for field in changes
        if str(values[field]) != str(getattr(current, field))
    }
    record_event(
        session,
        auth,
        AutonomyEventType.POLICY_CHANGED,
        message=f"Policy {current.version} superseded by {fresh.version}.",
        payload={"from": current.version, "to": fresh.version, "changed": changed},
    )
    if values["autonomy_level"] != current.autonomy_level:
        record_event(
            session,
            auth,
            AutonomyEventType.LEVEL_CHANGED,
            message=(
                f"Autonomy level {current.autonomy_level} -> {values['autonomy_level']} "
                f"({AutonomyLevel(int(values['autonomy_level'])).label})."
            ),
            payload={"from": current.autonomy_level, "to": values["autonomy_level"]},
        )
    return fresh


def _validate(values: dict[str, Any]) -> None:
    """Checks that would make a policy internally contradictory."""
    if values["capital_limit"] < 0:
        raise ValidationError("Capital limit cannot be negative.")
    if values["max_position_size"] is not None and values["max_position_size"] <= 0:
        raise ValidationError("Maximum position size must be positive when set.")
    if not (Decimal("0") < values["max_position_pct"] <= Decimal("1")):
        raise ValidationError("Maximum position share must be between 0 and 1.")
    if values["minimum_roi"] < 0:
        raise ValidationError("Minimum ROI cannot be negative.")
    if not (Decimal("0") <= values["minimum_match_confidence"] <= Decimal("1")):
        raise ValidationError("Minimum match confidence must be between 0 and 1.")
    if values["maximum_inventory_age_days"] <= 0:
        raise ValidationError("Maximum inventory age must be positive.")

    level = AutonomyLevel(int(values["autonomy_level"]))
    if level.deploys_capital and values["capital_limit"] <= 0:
        raise ValidationError(
            f"Level {int(level)} ({level.label}) deploys capital, so a capital limit "
            "above zero is required. Autonomy without a limit is not a policy."
        )
    if values["execution_mode"] == ExecutionMode.LIVE.value and values["capital_limit"] <= 0:
        raise ValidationError("Live execution requires a capital limit above zero.")


def set_emergency_stop(
    session: Session,
    auth: AuthContext,
    *,
    active: bool,
    reason: str,
    actor: str | None = None,
) -> AutonomyPolicy:
    """Stop or resume autonomous activity immediately.

    Deliberately not a policy version. A stop has to take effect at once and has
    to be reversible without rewriting the investment rules, and versioning it
    would make the two indistinguishable in the history.

    Stopping does not touch existing positions: they are still held, still
    monitored and still reported. What stops is new capital.
    """
    policy = active_policy(session, auth)
    policy.emergency_stop_active = active
    policy.emergency_stop_reason = reason if active else None
    policy.emergency_stop_at = utcnow() if active else None
    session.flush()
    record_event(
        session,
        auth,
        AutonomyEventType.EMERGENCY_STOP if active else AutonomyEventType.EMERGENCY_STOP_CLEARED,
        message=reason,
        actor=actor or auth.email,
        payload={"active": active, "at": iso_utc(policy.emergency_stop_at)},
    )
    return policy


def record_event(
    session: Session,
    auth: AuthContext,
    event_type: AutonomyEventType,
    *,
    message: str,
    actor: str | None = None,
    opportunity_id: str | None = None,
    decision_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AutonomyEvent:
    """Append one line to the autonomy audit log."""
    event = AutonomyEvent(
        organization_id=auth.organization_id,
        event_type=event_type.value,
        actor=actor or "system",
        message=message[:1000],
        opportunity_id=opportunity_id,
        decision_id=decision_id,
        payload=payload or {},
    )
    session.add(event)
    session.flush()
    return event


def history(session: Session, auth: AuthContext, *, limit: int = 50) -> list[AutonomyPolicy]:
    """Every version, newest first. The record of how the rules changed."""
    return list(
        session.scalars(
            select(AutonomyPolicy)
            .where(AutonomyPolicy.organization_id == auth.organization_id)
            .order_by(AutonomyPolicy.created_at.desc())
            .limit(limit)
        )
    )
