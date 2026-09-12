"""Historical observation tables.

Spreadline builds its own dataset over time (spec §10). State is never
overwritten: each poll appends a row, so a statistic can always be recomputed and
a backtest can always ask what was visible at a past instant.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrganizationScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import GUID, JSONB, Money, Ratio


class PriceObservation(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    __tablename__ = "price_history"
    __table_args__ = (
        # The read pattern is always "this listing, this window, ordered by time".
        {"comment": "Append-only price observations. Never updated in place."},
    )

    product_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    listing_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    shipping: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    landed_price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    availability: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    condition: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    is_buy_box: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    seller_id: Mapped[str | None] = mapped_column(String(120))
    seller_name: Mapped[str | None] = mapped_column(String(200))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    #: ``provider`` is which adapter produced it; ``source`` is how (live poll,
    #: provider history backfill, manual entry). Statistics weight them
    #: differently and audits need to tell them apart.
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="mock")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="poll")


class DemandObservation(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """Demand signals. Every field is nullable on purpose.

    A missing sales rank is recorded as missing. It is never inferred from
    reviews, and estimated units are only ever populated when a calibration for
    that marketplace and category exists (spec §12).
    """

    __tablename__ = "demand_history"

    product_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    listing_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sales_rank: Mapped[int | None] = mapped_column(Integer)
    rank_category: Mapped[str | None] = mapped_column(String(120))
    estimated_monthly_units: Mapped[int | None] = mapped_column(Integer)
    estimation_basis: Mapped[str | None] = mapped_column(String(64))
    review_count: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[Decimal | None] = mapped_column(Ratio)
    buy_box_seller_id: Mapped[str | None] = mapped_column(String(120))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="mock")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="poll")


class CompetitionObservation(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    __tablename__ = "competition_history"

    product_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    listing_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    seller_count: Mapped[int | None] = mapped_column(Integer)
    offer_count: Mapped[int | None] = mapped_column(Integer)
    lowest_price: Mapped[Decimal | None] = mapped_column(Money)
    median_price: Mapped[Decimal | None] = mapped_column(Money)
    highest_price: Mapped[Decimal | None] = mapped_column(Money)
    buy_box_price: Mapped[Decimal | None] = mapped_column(Money)
    buy_box_seller_id: Mapped[str | None] = mapped_column(String(120))
    marketplace_is_seller: Mapped[bool | None] = mapped_column(Boolean)
    #: Seller ids present at this observation, so entries and exits between two
    #: observations are a set difference rather than a guess from a count delta.
    seller_ids: Mapped[list[str]] = mapped_column(JSONB(), nullable=False, default=list)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="mock")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="poll")


class ProviderRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One outbound provider call. Feeds reliability metrics and cost control."""

    __tablename__ = "provider_requests"

    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Identifier or query, never the credential-bearing URL.
    target: Mapped[str | None] = mapped_column(String(255))
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(500))
    was_cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)


class ProviderHealth(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Rolling health per provider. One row per provider, updated in place."""

    __tablename__ = "provider_health"

    provider: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="not_configured")
    circuit_state: Mapped[str] = mapped_column(String(16), nullable=False, default="closed")
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_latency_ms: Mapped[int | None] = mapped_column(Integer)
    p95_latency_ms: Mapped[int | None] = mapped_column(Integer)
    rate_limit_per_second: Mapped[Decimal | None] = mapped_column(Ratio)
    quota_limit: Mapped[int | None] = mapped_column(Integer)
    quota_used: Mapped[int | None] = mapped_column(Integer)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    circuit_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
