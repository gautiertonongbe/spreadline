"""Identity resolution against stored listings.

Bridges the pure matcher to the database: builds candidates from listing rows,
records the decision with its evidence, and unifies the canonical product when
the evidence supports it.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domains.identity.matcher import (
    DEFAULT_POLICY,
    MatchCandidate,
    MatchingPolicy,
    MatchResult,
    match_listings,
)
from app.models.catalog import MarketplaceListing, Product, ProductIdentifier
from app.models.identity import ProductMatch
from app.models.observations import CompetitionObservation, DemandObservation, PriceObservation


def candidate_from_listing(session: Session, listing: MarketplaceListing) -> MatchCandidate:
    identifiers: dict[str, list[str]] = {}
    if listing.product_id:
        rows = session.scalars(
            select(ProductIdentifier).where(ProductIdentifier.product_id == listing.product_id)
        )
        for row in rows:
            identifiers.setdefault(row.identifier_type, []).append(row.value)

    attributes = dict(listing.raw_attributes or {})
    product = session.get(Product, listing.product_id) if listing.product_id else None
    return MatchCandidate(
        listing_id=listing.id,
        marketplace=listing.marketplace,
        title=listing.title,
        brand=listing.brand or (product.brand if product else None),
        model=(product.model if product else None),
        condition=listing.condition,
        pack_count=listing.pack_count,
        attributes=attributes,
        identifiers=identifiers,
        provider=listing.provider,
    )


def resolve_match(
    session: Session,
    organization_id: str,
    source: MarketplaceListing,
    target: MarketplaceListing,
    *,
    policy: MatchingPolicy = DEFAULT_POLICY,
    persist: bool = True,
) -> tuple[MatchResult, ProductMatch | None]:
    """Match two stored listings and record the decision."""
    result = match_listings(
        candidate_from_listing(session, source),
        candidate_from_listing(session, target),
        policy=policy,
    )
    if not persist:
        return result, None

    row = session.scalar(
        select(ProductMatch).where(
            ProductMatch.source_listing_id == source.id,
            ProductMatch.target_listing_id == target.id,
        )
    )
    if row is None:
        row = ProductMatch(
            organization_id=organization_id,
            source_listing_id=source.id,
            target_listing_id=target.id,
        )
        session.add(row)

    # A manual review decision outranks the automated one and is not overwritten
    # by a later rescore.
    if row.reviewed_by:
        session.flush()
        return result, row

    row.product_id = source.product_id
    row.match_confidence = result.confidence
    row.match_method = result.method.value
    row.status = result.status.value
    row.evidence = result.evidence
    row.conflicts = result.conflicts
    row.variation_check_passed = result.variation_passed
    row.provider = source.provider
    session.flush()

    if result.is_usable:
        unify_products(session, source, target)
    return result, row


def unify_products(
    session: Session, source: MarketplaceListing, target: MarketplaceListing
) -> Product | None:
    """Point both listings at one canonical product.

    The surviving product is the one with a validated GTIN, then the older row.
    Observations already written against the absorbed product are repointed so no
    history is orphaned.
    """
    if not source.product_id or not target.product_id:
        return None
    if source.product_id == target.product_id:
        return session.get(Product, source.product_id)

    source_product = session.get(Product, source.product_id)
    target_product = session.get(Product, target.product_id)
    if source_product is None or target_product is None:
        return source_product or target_product

    if source_product.primary_gtin and not target_product.primary_gtin:
        survivor, absorbed = source_product, target_product
    elif target_product.primary_gtin and not source_product.primary_gtin:
        survivor, absorbed = target_product, source_product
    else:
        # created_at is server-generated and may not be populated on a freshly
        # flushed row, so fall back to a stable ordering by id.
        source_created = getattr(source_product, "created_at", None)
        target_created = getattr(target_product, "created_at", None)
        if source_created and target_created:
            source_first = source_created <= target_created
        else:
            source_first = source_product.id <= target_product.id
        survivor, absorbed = (
            (source_product, target_product) if source_first else (target_product, source_product)
        )

    survivor.brand = survivor.brand or absorbed.brand
    survivor.model = survivor.model or absorbed.model
    survivor.category = survivor.category or absorbed.category
    survivor.primary_gtin = survivor.primary_gtin or absorbed.primary_gtin
    survivor.pack_count = survivor.pack_count or absorbed.pack_count

    for model in (PriceObservation, DemandObservation, CompetitionObservation):
        session.execute(
            update(model).where(model.product_id == absorbed.id).values(product_id=survivor.id)
        )
    # Identifiers are unique per (product, type, value): drop the absorbed rows
    # that the survivor already carries before repointing the rest.
    survivor_keys = {
        (row.identifier_type, row.value)
        for row in session.scalars(
            select(ProductIdentifier).where(ProductIdentifier.product_id == survivor.id)
        )
    }
    for row in session.scalars(
        select(ProductIdentifier).where(ProductIdentifier.product_id == absorbed.id)
    ).all():
        if (row.identifier_type, row.value) in survivor_keys:
            session.delete(row)
        else:
            row.product_id = survivor.id
    session.flush()
    session.execute(
        update(MarketplaceListing)
        .where(MarketplaceListing.product_id == absorbed.id)
        .values(product_id=survivor.id)
    )
    session.flush()
    session.delete(absorbed)
    session.flush()
    return survivor
