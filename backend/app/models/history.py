"""The universe Spreadline keeps its own history for.

The intelligence engine is only as independent as its own dataset. Every window
statistic, every volatility figure and every spread verdict is computed from
observations this platform recorded itself, which means the value of the whole
layer is a function of one thing: how consistently it observed.

A tracked listing is a standing instruction to keep observing one listing on one
marketplace. It is deliberately a listing rather than a product, because a
listing is the unit a provider can actually be asked about; products are how the
observations are read back.

The universe is bounded on purpose (spec §43). Spreadline is not trying to crawl
a marketplace. It observes what the operator is actually working on, at a cadence
the operator sets, and stops when a provider says stop.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrganizationScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import GUID, JSONB


class TrackedListing(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """One listing Spreadline keeps observing."""

    __tablename__ = "tracked_listings"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "marketplace",
            "external_id",
            name="uq_tracked_listings_marketplace_external_id",
        ),
    )

    listing_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("marketplace_listings.id", ondelete="SET NULL"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(120), nullable=False)

    #: Why this listing is tracked. Recorded so a universe that grew by accident
    #: can be pruned deliberately.
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    #: Higher is polled first when a run cannot cover the whole universe.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    #: Null means "use the configured default for this data type". An explicit
    #: value here is the operator overriding the cadence for this one listing.
    refresh_interval_seconds: Mapped[int | None] = mapped_column(Integer)

    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    #: Consecutive failures. A listing that keeps failing is backed off rather
    #: than retried on every run, which is how one dead id burns a whole quota.
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    notes: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSONB(), nullable=False, default=dict)
