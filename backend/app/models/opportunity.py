"""Opportunities and the artefacts that justify them.

An opportunity row is a *decision record*. It keeps the score breakdown, the fee
assumptions, the risk signals and the lifecycle events that produced it, because
the point of the platform is to be able to answer "why did we buy this" six
months later, and to measure the prediction against the outcome.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    org_index,
)
from app.models.types import GUID, JSONB, Money, Ratio


class Opportunity(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    __tablename__ = "opportunities"
    __table_args__ = (
        org_index("opportunities", "status", "score"),
        org_index("opportunities", "product_id"),
    )

    product_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_listing_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("marketplace_listings.id", ondelete="CASCADE"), nullable=False
    )
    target_listing_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("marketplace_listings.id", ondelete="CASCADE"), nullable=False
    )
    match_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("product_matches.id", ondelete="SET NULL")
    )

    direction: Mapped[str] = mapped_column(String(32), nullable=False)
    sourcing_channel: Mapped[str] = mapped_column(
        String(32), nullable=False, default="online_arbitrage"
    )
    source_marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    target_marketplace: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="new", index=True)
    recommendation: Mapped[str] = mapped_column(String(16), nullable=False, default="review")
    #: 0..100, produced by the configurable scoring model.
    score: Mapped[Decimal | None] = mapped_column(Ratio, index=True)
    score_model_version: Mapped[str | None] = mapped_column(String(32))
    #: Per-component sub-scores and the weights applied, so the total is never a
    #: black box (spec §17).
    score_components: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)

    #: Headline economics, denormalised from the profitability snapshot for list
    #: views. The snapshot remains the authoritative record.
    acquisition_cost: Mapped[Decimal | None] = mapped_column(Money)
    expected_sale_price: Mapped[Decimal | None] = mapped_column(Money)
    net_profit: Mapped[Decimal | None] = mapped_column(Money)
    roi: Mapped[Decimal | None] = mapped_column(Ratio)
    margin: Mapped[Decimal | None] = mapped_column(Ratio)
    spread: Mapped[Decimal | None] = mapped_column(Money)

    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    risk_score: Mapped[Decimal | None] = mapped_column(Ratio)
    match_confidence: Mapped[Decimal | None] = mapped_column(Ratio)
    data_quality_score: Mapped[Decimal | None] = mapped_column(Ratio)
    demand_confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="none")

    #: The rendered explanation: reasons[], risks[], gates[]. Built from system
    #: data, never from a model's prose.
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)
    recommended_quantity: Mapped[int | None] = mapped_column(Integer)
    recommended_capital: Mapped[Decimal | None] = mapped_column(Money)
    notes: Mapped[str | None] = mapped_column(Text)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(120))

    events: Mapped[list[OpportunityEvent]] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
        order_by="OpportunityEvent.created_at",
    )
    risk_assessments: Mapped[list[RiskAssessment]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )
    profitability_snapshots: Mapped[list[ProfitabilitySnapshot]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )
    validations: Mapped[list[OpportunityValidation]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )


class OpportunityEvent(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """Append-only lifecycle log. Every transition produces one (spec §21)."""

    __tablename__ = "opportunity_events"
    __table_args__ = (org_index("opportunity_events", "opportunity_id", "created_at"),)

    opportunity_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(120), nullable=False, default="system")
    message: Mapped[str | None] = mapped_column(String(500))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)

    opportunity: Mapped[Opportunity] = relationship(back_populates="events")


class ProfitabilitySnapshot(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """A frozen profitability calculation.

    ``assumptions`` is stored in full, not referenced by id. Today's fee schedule
    must not silently rewrite yesterday's prediction (spec §14), which is exactly
    what a foreign key to a mutable assumption row would do.
    """

    __tablename__ = "profitability_snapshots"

    opportunity_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    scenario: Mapped[str] = mapped_column(String(40), nullable=False, default="base")
    target_marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    fulfillment: Mapped[str] = mapped_column(String(16), nullable=False, default="fba")

    sale_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    acquisition_cost: Mapped[Decimal] = mapped_column(Money, nullable=False)
    acquisition_shipping: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    referral_fee: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    fulfillment_fee: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    closing_fee: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    storage_fee: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    inbound_shipping: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    return_allowance: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    tax: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    misc_cost: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    total_fees: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    total_cost: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    net_profit: Mapped[Decimal] = mapped_column(Money, nullable=False)
    roi: Mapped[Decimal | None] = mapped_column(Ratio)
    margin: Mapped[Decimal | None] = mapped_column(Ratio)
    breakeven_sale_price: Mapped[Decimal | None] = mapped_column(Money)
    max_acquisition_cost: Mapped[Decimal | None] = mapped_column(Money)
    line_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(), nullable=False, default=list)
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)
    assumptions_version: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")

    opportunity: Mapped[Opportunity | None] = relationship(back_populates="profitability_snapshots")


class RiskAssessment(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    __tablename__ = "risk_assessments"

    opportunity_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    scenario: Mapped[str] = mapped_column(String(40), nullable=False, default="base")
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[Decimal] = mapped_column(Ratio, nullable=False)
    #: Each signal keeps its code, severity, weight, message and evidence, so a
    #: level always explains itself (spec §15).
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(), nullable=False, default=list)
    summary: Mapped[str | None] = mapped_column(String(1000))
    model_version: Mapped[str] = mapped_column(String(32), nullable=False, default="risk-v1")

    opportunity: Mapped[Opportunity | None] = relationship(back_populates="risk_assessments")


class OpportunityValidation(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """Personal testing mode (spec §22).

    The operator's hand-checked observation of what the platform claimed. This is
    the labelled dataset that later decides whether the automation is trustworthy,
    so the platform's own numbers are copied in at recording time.
    """

    __tablename__ = "opportunity_validations"

    opportunity_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tested_by: Mapped[str | None] = mapped_column(String(120))
    tested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    actual_source_price: Mapped[Decimal | None] = mapped_column(Money)
    actual_target_price: Mapped[Decimal | None] = mapped_column(Money)
    actual_availability: Mapped[str | None] = mapped_column(String(32))
    match_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    profit_estimate_correct: Mapped[bool | None] = mapped_column(Boolean)
    would_buy: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)

    #: What the system predicted at test time, and the deltas. Snapshotted so a
    #: later rescore cannot rewrite the measurement.
    predicted_source_price: Mapped[Decimal | None] = mapped_column(Money)
    predicted_target_price: Mapped[Decimal | None] = mapped_column(Money)
    predicted_net_profit: Mapped[Decimal | None] = mapped_column(Money)
    predicted_roi: Mapped[Decimal | None] = mapped_column(Ratio)
    predicted_recommendation: Mapped[str | None] = mapped_column(String(16))
    source_price_delta: Mapped[Decimal | None] = mapped_column(Money)
    target_price_delta: Mapped[Decimal | None] = mapped_column(Money)

    opportunity: Mapped[Opportunity] = relationship(back_populates="validations")
