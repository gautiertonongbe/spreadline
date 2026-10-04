"""Product search, lookup, history and the analysis entry point."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import Auth, DbSession, Providers
from app.core.clock import iso_utc
from app.core.errors import NotFoundError
from app.domains.opportunities import manual, paste
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
    ManualAnalyzeRequest,
    ManualSideRequest,
    PasteRequest,
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


def _manual_side(payload: ManualSideRequest) -> manual.ManualSide:
    """The request shape mapped onto the domain's own."""
    return manual.ManualSide(
        marketplace=payload.marketplace,
        external_id=payload.external_id,
        title=payload.title,
        price=payload.price,
        url=payload.url,
        brand=payload.brand,
        model=payload.model,
        category=payload.category,
        shipping=payload.shipping,
        condition=payload.condition,
        availability=payload.availability,
        identifiers=payload.identifiers,
        sales_rank=payload.sales_rank,
        rank_category=payload.rank_category,
        seller_count=payload.seller_count,
        offer_count=payload.offer_count,
        review_count=payload.review_count,
        rating=payload.rating,
        quantity_available=payload.quantity_available,
        note=payload.note,
    )


@router.post("/analyze/paste")
def read_paste(payload: PasteRequest, auth: Auth) -> dict[str, Any]:
    """Read a pasted product page into a draft for a person to confirm.

    Nothing is fetched, nothing is written and nothing is analysed. Copying text
    off a page you are looking at is not automated access, and what comes back
    here is a proposal: every field carries the line it was read from and how
    sure the parser is, and the person confirms it before any of it counts.

    The pasted text is not stored. A signed-in product page carries the reader's
    name, their delivery address, their cart and their customer id threaded
    through every link on it, and none of that is the platform's business.
    """
    del auth  # required for the session check, not used to scope anything
    return paste.parse(payload.text, marketplace=payload.marketplace).as_dict()


@router.post("/analyze/manual")
async def analyze_manual(
    payload: ManualAnalyzeRequest, session: DbSession, auth: Auth
) -> dict[str, Any]:
    """Analyse a pair somebody looked up by hand.

    The path that needs no credential and no queue. A person opens the two
    product pages, reads the prices and types them in; nothing here fetches
    anything, so there is no robot, no terms of service to breach and no account
    to put at risk. A human reading a public page is not scraping, and the
    resulting observation is recorded as real rather than simulated, because
    nothing about it is invented.

    The listings are written through the same choke point a provider answer goes
    through and the analysis then runs offline, so identity, profitability,
    scoring, risk and the decision are the same code on the same data. A
    hand-entered pair is judged by exactly the standard an API-fed one is, which
    includes being refused when the evidence is thin.
    """
    source = _manual_side(payload.source)
    target = _manual_side(payload.target)
    recorded = manual.record_pair(session, auth, source=source, target=target)

    result = await analyze_pair(
        session,
        auth,
        source_marketplace=source.marketplace,
        source_external_id=source.external_id,
        target_marketplace=target.marketplace,
        target_external_id=target.external_id,
        options=AnalysisOptions(
            sourcing_channel=payload.sourcing_channel,
            fee_overrides=payload.fee_overrides,
            run_stress_test=payload.run_stress_test,
            persist=payload.persist,
            # Nothing to call. The pair was just written by hand and the
            # pipeline reads it from the catalogue.
            offline=True,
        ),
    )
    if payload.persist:
        session.commit()

    body = result.as_dict()
    body["entry"] = {
        "method": "manual",
        "shared_identifiers": recorded["shared_identifiers"],
        #: What this pair cannot answer, said before the verdict rather than
        #: discovered inside a refusal.
        "gaps": recorded["warnings"],
    }
    return body


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
                    {"price": str(point.price), "observed_at": iso_utc(point.observed_at)}
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
