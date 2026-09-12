"""The product graph: canonical products, their identifiers and attributes, the
marketplace listings that represent them, and the offers against those listings.

    Canonical Product
           ├── Amazon listing ── offer, offer, offer
           └── Walmart listing ── offer, offer

A product is never "two unrelated URLs" (spec §8): the canonical row is what
pricing, demand, competition and profitability all attach to.
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


class Product(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """Canonical, marketplace-independent sellable product."""

    __tablename__ = "products"
    __table_args__ = (
        org_index("products", "brand"),
        org_index("products", "primary_gtin"),
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(200), index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(120), index=True)
    #: GTIN-14, normalised and check-digit validated. The strongest identity we
    #: hold; null when no listing supplied a valid one.
    primary_gtin: Mapped[str | None] = mapped_column(String(14), index=True)
    pack_count: Mapped[int | None] = mapped_column(Integer)
    #: Free-form normalised attributes (size, color, capacity, unit…) used by the
    #: variation engine.
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)
    image_url: Mapped[str | None] = mapped_column(String(1024))
    notes: Mapped[str | None] = mapped_column(Text)

    identifiers: Mapped[list[ProductIdentifier]] = relationship(
        back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )
    attribute_rows: Mapped[list[ProductAttribute]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    listings: Mapped[list[MarketplaceListing]] = relationship(
        back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )


class ProductIdentifier(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """One identifier claim about a product, with its provenance.

    Kept as rows rather than columns because a single product legitimately has
    several of each type (multiple UPCs across regions, an ASIN per marketplace)
    and because each claim needs its own source and validation state.
    """

    __tablename__ = "product_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "identifier_type", "value", name="uq_product_identifiers_product_id"
        ),
        org_index("product_identifiers", "identifier_type", "value"),
    )

    product_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    identifier_type: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Normalised form (GTIN-14 for UPC/EAN/GTIN, uppercased MPN, …).
    normalized_value: Mapped[str | None] = mapped_column(String(64), index=True)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    validation_note: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="provider")
    provider: Mapped[str | None] = mapped_column(String(64))

    product: Mapped[Product] = relationship(back_populates="identifiers")


class ProductAttribute(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """A normalised attribute with its raw source value kept alongside.

    The raw value is retained because normalisation is lossy and the variation
    engine's mistakes are only debuggable against what the provider actually said.
    """

    __tablename__ = "product_attributes"
    __table_args__ = (
        UniqueConstraint("product_id", "name", name="uq_product_attributes_product_id"),
    )

    product_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[str | None] = mapped_column(String(255))
    normalized_value: Mapped[str | None] = mapped_column(String(255))
    unit: Mapped[str | None] = mapped_column(String(32))
    numeric_value: Mapped[Decimal | None] = mapped_column(Ratio)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="provider")
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")

    product: Mapped[Product] = relationship(back_populates="attribute_rows")


class MarketplaceListing(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """A marketplace's own representation of a product."""

    __tablename__ = "marketplace_listings"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "marketplace",
            "external_id",
            name="uq_marketplace_listings_organization_id",
        ),
        org_index("marketplace_listings", "marketplace", "product_id"),
    )

    product_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sku: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(120))
    url: Mapped[str | None] = mapped_column(String(1024))
    image_url: Mapped[str | None] = mapped_column(String(1024))
    condition: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    pack_count: Mapped[int | None] = mapped_column(Integer)
    #: Latest observed snapshot. History lives in the *_history tables; these
    #: columns exist so a listing row alone can answer "what is it now".
    current_price: Mapped[Decimal | None] = mapped_column(Money)
    current_shipping: Mapped[Decimal | None] = mapped_column(Money)
    availability: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    seller_count: Mapped[int | None] = mapped_column(Integer)
    offer_count: Mapped[int | None] = mapped_column(Integer)
    sales_rank: Mapped[int | None] = mapped_column(Integer)
    review_count: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[Decimal | None] = mapped_column(Ratio)
    raw_attributes: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)
    provider: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    product: Mapped[Product | None] = relationship(back_populates="listings")
    offers: Mapped[list[Offer]] = relationship(
        back_populates="listing", cascade="all, delete-orphan", lazy="selectin"
    )


class Offer(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """A seller's offer against a listing, as observed at a point in time."""

    __tablename__ = "offers"
    __table_args__ = (org_index("offers", "listing_id", "observed_at"),)

    listing_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_offer_id: Mapped[str | None] = mapped_column(String(120))
    seller_id: Mapped[str | None] = mapped_column(String(120), index=True)
    seller_name: Mapped[str | None] = mapped_column(String(200))
    price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    shipping: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))
    condition: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    fulfillment: Mapped[str | None] = mapped_column(String(16))
    is_buy_box: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_marketplace_seller: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    availability: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    quantity_available: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    provider: Mapped[str | None] = mapped_column(String(64))

    listing: Mapped[MarketplaceListing] = relationship(back_populates="offers")

    @property
    def landed_price(self) -> Decimal:
        return (self.price or Decimal("0")) + (self.shipping or Decimal("0"))
