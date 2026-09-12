"""Realised inventory: purchases, sales, outcomes and capital plans.

This is where predictions meet reality. The outcome row is the comparison between
what the engines said and what the money did (spec §23).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    OrganizationScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    org_index,
)
from app.models.types import GUID, JSONB, Money, Ratio


class Purchase(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    __tablename__ = "purchases"
    __table_args__ = (org_index("purchases", "purchased_at"),)

    opportunity_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_marketplace: Mapped[str | None] = mapped_column(String(32))
    supplier: Mapped[str | None] = mapped_column(String(200))
    sourcing_channel: Mapped[str] = mapped_column(
        String(32), nullable=False, default="online_arbitrage"
    )
    order_reference: Mapped[str | None] = mapped_column(String(120))

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    shipping_cost: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    tax: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    other_costs: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    total_cost: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_delivery: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    sales: Mapped[list[Sale]] = relationship(back_populates="purchase")


class Sale(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    __tablename__ = "sales"
    __table_args__ = (org_index("sales", "sold_at"),)

    purchase_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("purchases.id", ondelete="SET NULL"), index=True
    )
    opportunity_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    order_reference: Mapped[str | None] = mapped_column(String(120))

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    gross_revenue: Mapped[Decimal] = mapped_column(Money, nullable=False)
    marketplace_fees: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    fulfillment_fees: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    storage_fees: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    shipping_cost: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    refunds: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    other_costs: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    net_proceeds: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    purchase: Mapped[Purchase | None] = relationship(back_populates="sales")


class Outcome(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """Predicted versus actual for one closed position.

    The predicted side is copied from the profitability snapshot that was live at
    purchase time, so accuracy measurement survives any later rescore.
    """

    __tablename__ = "outcomes"

    opportunity_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )
    purchase_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("purchases.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )

    quantity_purchased: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity_sold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capital_deployed: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    predicted_unit_profit: Mapped[Decimal | None] = mapped_column(Money)
    predicted_total_profit: Mapped[Decimal | None] = mapped_column(Money)
    predicted_roi: Mapped[Decimal | None] = mapped_column(Ratio)
    predicted_recommendation: Mapped[str | None] = mapped_column(String(16))
    predicted_score: Mapped[Decimal | None] = mapped_column(Ratio)

    actual_revenue: Mapped[Decimal | None] = mapped_column(Money)
    actual_costs: Mapped[Decimal | None] = mapped_column(Money)
    actual_profit: Mapped[Decimal | None] = mapped_column(Money)
    actual_roi: Mapped[Decimal | None] = mapped_column(Ratio)

    profit_variance: Mapped[Decimal | None] = mapped_column(Money)
    profit_variance_pct: Mapped[Decimal | None] = mapped_column(Ratio)
    roi_variance: Mapped[Decimal | None] = mapped_column(Ratio)
    days_to_sell: Mapped[int | None] = mapped_column(Integer)
    is_closed: Mapped[bool] = mapped_column(nullable=False, default=False)
    #: "win" | "loss" | "breakeven" | "open" - derived, stored for aggregation.
    result: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    notes: Mapped[str | None] = mapped_column(Text)


class CapitalPlan(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """A saved capital allocation run (spec §20, §29).

    Stored with its constraints and the allocations produced so a past plan can be
    compared against what was actually deployed.
    """

    __tablename__ = "capital_plans"

    name: Mapped[str | None] = mapped_column(String(200))
    available_capital: Mapped[Decimal] = mapped_column(Money, nullable=False)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)
    allocations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(), nullable=False, default=list)
    excluded: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(), nullable=False, default=list)
    allocated_capital: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    unallocated_capital: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    expected_profit: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    expected_roi: Mapped[Decimal | None] = mapped_column(Ratio)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False, default="greedy-v1")
    is_simulation: Mapped[bool] = mapped_column(nullable=False, default=True)
