"""Stored product-identity decisions.

Every assertion that two listings are the same sellable product is recorded with
the evidence that produced it and the conflicts that were tolerated. A match is a
decision with a provenance, not a boolean.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, OrganizationScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import GUID, JSONB, Ratio


class ProductMatch(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    __tablename__ = "product_matches"
    __table_args__ = (
        UniqueConstraint(
            "source_listing_id", "target_listing_id", name="uq_product_matches_source_listing_id"
        ),
    )

    product_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    source_listing_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_listing_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #: 0..1. Title similarity alone can never reach the high-confidence band
    #: (spec §7); the ceiling is enforced in the matcher, not here.
    match_confidence: Mapped[Decimal] = mapped_column(Ratio, nullable=False)
    match_method: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="unverified")
    #: Human-readable statements of what was compared and what it showed.
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(), nullable=False, default=list)
    #: Variation disagreements found (pack count, size, color, capacity…).
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(), nullable=False, default=list)
    variation_check_passed: Mapped[bool | None] = mapped_column()
    provider: Mapped[str | None] = mapped_column(String(64))
    #: Set when the operator confirms or overrides the automated decision.
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    review_note: Mapped[str | None] = mapped_column(Text)
