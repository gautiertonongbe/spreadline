"""Autonomy: policy, decisions, positions and the record of how it performed.

Spreadline is built to become an inventory investment system that manages
capital within explicit rules. The rules here exist to make that progression
safe and reversible:

* A policy is **versioned and append-only**. A decision made under v1.0 keeps
  v1.0 forever, because a record that silently re-reads itself under today's
  rules cannot answer "why did we buy this".
* Every decision keeps an **evidence snapshot** of what was known at the time.
* Autonomy is **earned**. The gate reports eligibility against explicit
  criteria; a person acts on it. Nothing here raises its own limit.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    org_index,
)
from app.models.types import GUID, JSONB, Money, Ratio


class AutonomyPolicy(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """One version of the investment policy. Append-only.

    Changing a policy writes a new row and deactivates the old one. Nothing is
    edited in place, so a decision can always be re-read against the rules it was
    actually made under (spec autonomy §21).
    """

    __tablename__ = "autonomy_policies"
    __table_args__ = (
        UniqueConstraint("organization_id", "version", name="uq_autonomy_policies_version"),
        org_index("autonomy_policies", "is_active"),
    )

    version: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    #: 0..7. What the system is allowed to do without a human.
    autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    #: The ceiling on capital the system may commit on its own. Zero means none,
    #: which is the default and the only safe starting value.
    capital_limit: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    max_position_size: Mapped[Decimal | None] = mapped_column(Money)
    max_position_pct: Mapped[Decimal] = mapped_column(
        Ratio, nullable=False, default=Decimal("0.15")
    )
    max_loss_per_position: Mapped[Decimal | None] = mapped_column(Money)
    max_daily_deployment: Mapped[Decimal | None] = mapped_column(Money)

    minimum_roi: Mapped[Decimal] = mapped_column(Ratio, nullable=False, default=Decimal("0.20"))
    minimum_profit: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("3"))
    maximum_risk: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    minimum_match_confidence: Mapped[Decimal] = mapped_column(
        Ratio, nullable=False, default=Decimal("0.95")
    )
    minimum_data_quality: Mapped[Decimal] = mapped_column(
        Ratio, nullable=False, default=Decimal("60")
    )
    maximum_inventory_age_days: Mapped[int] = mapped_column(Integer, nullable=False, default=45)

    maximum_category_exposure: Mapped[Decimal] = mapped_column(
        Ratio, nullable=False, default=Decimal("0.30")
    )
    maximum_brand_exposure: Mapped[Decimal] = mapped_column(
        Ratio, nullable=False, default=Decimal("0.20")
    )
    maximum_marketplace_exposure: Mapped[Decimal] = mapped_column(
        Ratio, nullable=False, default=Decimal("1.00")
    )

    #: Human approval required before any position is opened. True by default:
    #: the safe state is the one that needs a person, and it has to be turned
    #: off deliberately.
    require_human_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    emergency_stop_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: When true, no autonomous deployment happens at all until it is cleared.
    emergency_stop_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    emergency_stop_reason: Mapped[str | None] = mapped_column(String(500))
    emergency_stop_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: observe | shadow | live.
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="observe")

    created_by: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    #: Anything configurable that has not earned a column yet. Kept so a policy
    #: knob can be added without a migration during this phase.
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)


class AgentRun(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """One stage of one decision, with what it concluded and why.

    The stages are the existing deterministic engines under the names an
    investment organisation would use. Recording them individually is what makes
    a decision auditable stage by stage, and what lets two stages disagree in a
    way the system can escalate rather than average away.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (org_index("agent_runs", "opportunity_id", "created_at"),)

    opportunity_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    decision_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("autonomy_decisions.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    #: What the stage produced, in full. The evidence behind the verdict.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)
    #: The version of the engine that produced it, so a change in behaviour is
    #: attributable rather than mysterious.
    engine_version: Mapped[str | None] = mapped_column(String(40))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class AutonomyDecision(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """An execution authorization, or a refusal to issue one.

    Preserves exactly what was known when the decision was made, including the
    policy version it was made under. Never updated once written.
    """

    __tablename__ = "autonomy_decisions"
    __table_args__ = (
        org_index("autonomy_decisions", "opportunity_id"),
        org_index("autonomy_decisions", "outcome", "created_at"),
    )

    opportunity_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )
    policy_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("autonomy_policies.id", ondelete="SET NULL"), index=True
    )
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False)

    #: authorized | blocked | escalated
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: Machine-readable cause, e.g. CAPITAL_LIMIT_EXCEEDED. Stable enough to
    #: assert on in a test and to count in a report.
    reason_code: Mapped[str | None] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False, default="")

    quantity: Mapped[int | None] = mapped_column(Integer)
    max_unit_price: Mapped[Decimal | None] = mapped_column(Money)
    capital_committed: Mapped[Decimal | None] = mapped_column(Money)
    expected_profit: Mapped[Decimal | None] = mapped_column(Money)
    expected_roi: Mapped[Decimal | None] = mapped_column(Ratio)
    risk_level: Mapped[str | None] = mapped_column(String(16))

    #: Everything the decision rested on: prices, history, fees, profitability,
    #: match confidence, demand, competition, risk, allocation, policy, versions.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)
    #: Per-stage verdicts, so disagreement is visible without a join.
    stage_verdicts: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)

    decided_by: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    runs: Mapped[list[AgentRun]] = relationship(cascade="all, delete-orphan")


class CapitalPosition(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """Inventory held as a capital position.

    One concept covers shadow and live deliberately. A shadow position is
    tracked against the same real market data as a live one and measured the
    same way; the only difference is whether money moved. Comparing the two is
    the entire point of paper trading, and it is only possible if they are the
    same shape.
    """

    __tablename__ = "capital_positions"
    __table_args__ = (
        org_index("capital_positions", "status", "opened_at"),
        org_index("capital_positions", "product_id"),
    )

    opportunity_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )
    decision_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("autonomy_decisions.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    #: Set only for a live position. Null on a shadow position, which is how the
    #: two are told apart without trusting a flag alone.
    purchase_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("purchases.id", ondelete="SET NULL"), index=True
    )

    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="shadow")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)

    title: Mapped[str | None] = mapped_column(String(512))
    brand: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(120))
    source_marketplace: Mapped[str | None] = mapped_column(String(32))
    target_marketplace: Mapped[str | None] = mapped_column(String(32))

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_cost: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    capital_invested: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    expected_unit_profit: Mapped[Decimal | None] = mapped_column(Money)
    expected_profit: Mapped[Decimal | None] = mapped_column(Money)
    expected_roi: Mapped[Decimal | None] = mapped_column(Ratio)
    risk_level: Mapped[str | None] = mapped_column(String(16))

    quantity_sold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    realized_proceeds: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    realized_profit: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class AgentPerformanceSnapshot(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """The scorecard, frozen at a point in time.

    Computed on demand from the decision and outcome records; stored when a
    gate is evaluated so that the evidence an autonomy level was granted on
    survives later data.
    """

    __tablename__ = "agent_performance_snapshots"
    __table_args__ = (org_index("agent_performance_snapshots", "created_at"),)

    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)
    decisions_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decisions_autonomous: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(String(500))


class AutonomyGateResult(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """Whether the system has earned a level, and what is still missing."""

    __tablename__ = "autonomy_gate_results"
    __table_args__ = (org_index("autonomy_gate_results", "target_level", "created_at"),)

    target_level: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Every criterion with its required value, actual value and whether it met.
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(), nullable=False, default=list)
    summary: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    snapshot_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("agent_performance_snapshots.id", ondelete="SET NULL")
    )


class CircuitBreaker(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """A capital circuit breaker and its current state.

    Separate from the provider circuit breaker, which protects a provider from
    being hammered. These protect capital from the system.
    """

    __tablename__ = "capital_circuit_breakers"
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_capital_breakers_code"),)

    code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    threshold: Mapped[Decimal | None] = mapped_column(Money)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ok", index=True)
    observed_value: Mapped[Decimal | None] = mapped_column(Money)
    tripped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tripped_reason: Mapped[str | None] = mapped_column(String(500))
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reset_by: Mapped[str | None] = mapped_column(String(120))


class AutonomyEvent(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """Append-only audit log for everything autonomy does or is told to do."""

    __tablename__ = "autonomy_events"
    __table_args__ = (org_index("autonomy_events", "event_type", "created_at"),)

    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    message: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    opportunity_id: Mapped[str | None] = mapped_column(GUID(), index=True)
    decision_id: Mapped[str | None] = mapped_column(GUID(), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)


class ExecutionInstruction(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """What an authorised decision asks a person to actually do.

    Spreadline decides what, where, how many and at what maximum price, and
    stops there: the retail order is placed by a human. Until now that boundary
    was a full stop, and the consequence was that the system never learned what
    happened next. A position was opened at the figures that were *authorised*
    and never corrected by the figures that were *paid*, so every realised
    outcome was measured against a plan rather than against reality.

    This row is the thing handed across the boundary and the thing handed back.
    It carries the ceiling the authorisation rested on, so an order placed above
    it is recorded as a departure from the decision rather than absorbed
    silently, and it expires, because "buy at up to $35.09" is only true while
    the price that justified it holds.

    An authorised supplier or marketplace integration, when there is one, reads
    exactly this and writes exactly the same fields back. That is what makes it
    an executor rather than a rewrite of the layer above.
    """

    __tablename__ = "execution_instructions"
    __table_args__ = (
        org_index("execution_instructions", "status", "created_at"),
        org_index("execution_instructions", "decision_id"),
    )

    decision_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("autonomy_decisions.id", ondelete="SET NULL"), index=True
    )
    position_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("capital_positions.id", ondelete="SET NULL"), index=True
    )
    opportunity_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    purchase_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("purchases.id", ondelete="SET NULL"), index=True
    )

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    #: Who or what is expected to carry this out. "human" today; the name of an
    #: authorised integration when one exists.
    executor: Mapped[str] = mapped_column(String(40), nullable=False, default="human")

    title: Mapped[str | None] = mapped_column(String(512))
    source_marketplace: Mapped[str | None] = mapped_column(String(32))
    source_external_id: Mapped[str | None] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(String(1000))

    quantity_authorized: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: The condition the authorisation rested on, not a suggestion.
    max_unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    capital_authorized: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    quantity_executed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_price_paid: Mapped[Decimal | None] = mapped_column(Money)
    capital_spent: Mapped[Decimal | None] = mapped_column(Money)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_by: Mapped[str | None] = mapped_column(String(120))
    order_reference: Mapped[str | None] = mapped_column(String(120))

    #: Stable codes for how the execution departed from the authorisation.
    #: Empty when it did not.
    variances: Mapped[list[Any]] = mapped_column(JSONB(), nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
