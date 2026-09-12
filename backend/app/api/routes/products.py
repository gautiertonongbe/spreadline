"""Product search, lookup, history and the analysis entry point."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import Auth, DbSession, Providers
from app.core.errors import NotFoundError
from app.domains.opportunities.analysis import (
    AnalysisOptions,
    analyze_both_directions,
    analyze_pair,
    load_price_points,
)
from app.domains.pricing.statistics import analyze_prices
from app.models.catalog import MarketplaceListing, Product
from app.models.enums import Marketplace
from app.schemas.analysis import (
    AnalyzeBothRequest,
    AnalyzeRequest,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from app.schemas.opportunity import ListingSummary, ProductSummary
from app.services.providers.base import ProviderCapability

router = APIRouter(prefix="/products", tags=["products"])


@router.post("/search", response_model=SearchResponse)
async def search_products(payload: SearchRequest, providers: Providers) -> SearchResponse:
    """Search a marketplace.

    Accepts a keyword, an ASIN, a Walmart item id, a UPC or a marketplace URL:
    the provider decides how to interpret it.
    """
    provider = providers.for_marketplace(payload.marketplace, capability=ProviderCapability.SEARCH)
    result = await provider.search_products(payload.query, limit=payload.limit)
    return SearchResponse(
        query=payload.query,
        marketplace=payload.marketplace.value,
        total_results=result.total_results,
        truncated=result.truncated,
        items=[
            SearchResultItem(
                marketplace=item.marketplace.value,
                external_id=item.external_id,
                title=item.title,
                brand=item.brand,
                category=item.category,
                price=item.price,
                availability=item.availability.value,
                seller_count=item.seller_count,
                sales_rank=item.sales_rank,
                url=item.url,
                provider=item.provider,
                is_live_data=provider.is_live,
            )
            for item in result.listings
        ],
    )


@router.post("/analyze")
async def analyze(
    payload: AnalyzeRequest, session: DbSession, auth: Auth, providers: Providers
) -> dict[str, Any]:
    """Run the full analysis workflow for one source listing and one exit market."""
    result = await analyze_pair(
        session,
        auth,
        source_marketplace=payload.source_marketplace,
        source_external_id=payload.source_external_id,
        target_marketplace=payload.target_marketplace,
        target_external_id=payload.target_external_id,
        options=AnalysisOptions(
            sourcing_channel=payload.sourcing_channel,
            fee_overrides=payload.fee_overrides,
            run_stress_test=payload.run_stress_test,
            persist=payload.persist,
        ),
        registry=providers,
    )
    if payload.persist:
        session.commit()
    return result.as_dict()


@router.post("/analyze/both-directions")
async def analyze_both(
    payload: AnalyzeBothRequest, session: DbSession, auth: Auth, providers: Providers
) -> dict[str, Any]:
    """Evaluate Amazon to Walmart and Walmart to Amazon for the same product."""
    results = await analyze_both_directions(
        session,
        auth,
        amazon_external_id=payload.amazon_external_id,
        walmart_external_id=payload.walmart_external_id,
        options=AnalysisOptions(run_stress_test=payload.run_stress_test),
        registry=providers,
    )
    session.commit()
    best = max(results, key=lambda item: item.score.total)
    return {
        "directions": [item.as_dict() for item in results],
        "best_direction": best.context.direction.value,
        "best_score": str(best.score.total),
    }


@router.get("", response_model=list[ProductSummary])
def list_products(
    session: DbSession,
    auth: Auth,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
) -> list[Product]:
    query = select(Product).where(Product.organization_id == auth.organization_id)
    if search:
        query = query.where(Product.title.ilike(f"%{search}%"))
    return list(
        session.scalars(query.order_by(Product.created_at.desc()).limit(limit).offset(offset))
    )


@router.get("/{product_id}")
def get_product(product_id: str, session: DbSession, auth: Auth) -> dict[str, Any]:
    product = session.scalar(
        select(Product).where(
            Product.id == product_id, Product.organization_id == auth.organization_id
        )
    )
    if product is None:
        raise NotFoundError(f"Product {product_id} was not found.")
    return {
        "product": ProductSummary.model_validate(product).model_dump(),
        "identifiers": [
            {
                "type": row.identifier_type,
                "value": row.value,
                "normalized": row.normalized_value,
                "is_valid": row.is_valid,
                "note": row.validation_note,
                "source": row.source,
            }
            for row in product.identifiers
        ],
        "listings": [ListingSummary.model_validate(row).model_dump() for row in product.listings],
    }


@router.get("/{product_id}/history")
def get_product_history(product_id: str, session: DbSession, auth: Auth) -> dict[str, Any]:
    """Price history and statistics per listing.

    Windows that lack the observations to support a statistic report that fact
    rather than a number.
    """
    product = session.scalar(
        select(Product).where(
            Product.id == product_id, Product.organization_id == auth.organization_id
        )
    )
    if product is None:
        raise NotFoundError(f"Product {product_id} was not found.")

    listings: list[dict[str, Any]] = []
    for listing in product.listings:
        points = load_price_points(session, listing.id)
        analysis = analyze_prices(points)
        listings.append(
            {
                "listing_id": listing.id,
                "marketplace": listing.marketplace,
                "external_id": listing.external_id,
                "statistics": analysis.as_dict(),
                "observations": [
                    {"price": str(point.price), "observed_at": point.observed_at.isoformat()}
                    for point in points[-365:]
                ],
            }
        )
    return {"product_id": product_id, "listings": listings}


@router.get("/listings/{marketplace}/{external_id}", response_model=ListingSummary)
def get_listing(
    marketplace: Marketplace, external_id: str, session: DbSession, auth: Auth
) -> MarketplaceListing:
    listing = session.scalar(
        select(MarketplaceListing).where(
            MarketplaceListing.organization_id == auth.organization_id,
            MarketplaceListing.marketplace == marketplace.value,
            MarketplaceListing.external_id == external_id,
        )
    )
    if listing is None:
        raise NotFoundError(f"Listing {external_id} on {marketplace.value} was not found.")
    return listing
