"""The analysis pipeline.

Implements the workflow in spec §26 end to end:

    search -> identify source -> find counterpart -> resolve identity ->
    offers -> price history -> competition -> economics -> risk ->
    stress test -> score -> BUY / REVIEW / PASS

Ordering is not incidental. Identity is resolved before any economics are
computed, so a rejected match never produces a profit figure that someone could
act on. Data quality is assembled from what each step actually found, not
declared up front.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.errors import NotFoundError, ProviderCapabilityError, ProviderError, ValidationError
from app.core.logging import get_logger
from app.core.money import to_decimal
from app.core.security import AuthContext
from app.domains.catalog import service as catalog
from app.domains.competition.service import (
    CompetitionAssessment,
    CompetitionPoint,
    OfferSnapshot,
    assess_competition,
)
from app.domains.demand.service import DemandAssessment, DemandPoint, assess_demand
from app.domains.history import capture as history_capture
from app.domains.history import service as history
from app.domains.identity.service import resolve_match
from app.domains.identity.similarity import title_similarity
from app.domains.opportunities import manual
from app.domains.opportunities.context import AnalysisContext, ListingContext
from app.domains.opportunities.decision import (
    DEFAULT_DECISION_POLICY,
    Decision,
    DecisionPolicy,
    decide,
)
from app.domains.opportunities.scoring import (
    DEFAULT_SCORING_MODEL,
    OpportunityScore,
    ScoringModel,
    score_opportunity,
)
from app.domains.opportunities.stress import StressTestResult, run_stress_test
from app.domains.pricing.anomaly import PriceAnomaly, detect_anomaly
from app.domains.pricing.spread_evidence import SpreadEvidence, assess_spread
from app.domains.pricing.statistics import PriceHistoryAnalysis, PricePoint, analyze_prices
from app.domains.profitability.assumptions import FeeAssumptions, assumptions_for
from app.domains.profitability.engine import (
    ProfitabilityInput,
    ProfitabilityResult,
    calculate_profitability,
)
from app.domains.quality.service import (
    DataQualityScore,
    QualityDimension,
    build_dimension,
    score_quality,
)
from app.domains.risk.engine import RiskAssessmentResult, assess_risk
from app.models.catalog import MarketplaceListing
from app.models.enums import (
    Availability,
    Confidence,
    DataQualityDimension,
    Direction,
    Marketplace,
    SourcingChannel,
)
from app.models.observations import (
    CompetitionObservation,
    DemandObservation,
    PriceObservation,
)
from app.services.providers.base import ProviderCapability
from app.services.providers.registry import ProviderRegistry, get_registry

logger = get_logger(__name__)

#: How much history to request from a provider on first contact.
HISTORY_DAYS = 365
DEMAND_DAYS = 180
COMPETITION_DAYS = 90

#: Minimum title similarity for a search result to be accepted as the counterpart
#: when no identifier lookup succeeded. Set high: a wrong counterpart poisons
#: every downstream engine with another product's prices, offers and demand.
COUNTERPART_SEARCH_FLOOR = 0.75


@dataclass
class AnalysisOptions:
    direction: Direction = Direction.CUSTOM
    sourcing_channel: SourcingChannel = SourcingChannel.ONLINE_ARBITRAGE
    fee_overrides: dict[str, Any] | None = None
    scoring_model: ScoringModel = DEFAULT_SCORING_MODEL
    decision_policy: DecisionPolicy = DEFAULT_DECISION_POLICY
    #: Skip provider calls and analyse what is already stored. Used by bulk runs
    #: and by any path that must not spend provider quota.
    offline: bool = False
    run_stress_test: bool = True
    persist: bool = True


@dataclass
class AnalysisResult:
    context: AnalysisContext
    risk: RiskAssessmentResult
    score: OpportunityScore
    decision: Decision
    stress: StressTestResult | None = None
    #: Whether today's spread is a standing feature of these markets or a
    #: recent move. ``None`` only on a context built before this ran.
    spread_evidence: SpreadEvidence | None = None
    #: Set when the analysis was persisted.
    opportunity_id: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "summary": self.context.summary(),
            "match": self.context.match.as_dict(),
            "economics": self.context.profitability.as_dict(),
            "source_prices": self.context.source_prices.as_dict(),
            "target_prices": self.context.target_prices.as_dict(),
            "source_anomaly": self.context.source_anomaly.as_dict(),
            "target_anomaly": self.context.target_anomaly.as_dict(),
            "demand": self.context.demand.as_dict(),
            "competition": self.context.competition.as_dict(),
            "data_quality": self.context.quality.as_dict(),
            "risk": self.risk.as_dict(),
            "score": self.score.as_dict(),
            "decision": self.decision.as_dict(),
            "stress_test": self.stress.as_dict() if self.stress else None,
            "spread_evidence": (
                self.spread_evidence.as_dict() if self.spread_evidence else None
            ),
            "notes": self.notes,
        }


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------


async def ingest_listing(
    session: Session,
    auth: AuthContext,
    marketplace: Marketplace,
    external_id: str,
    *,
    registry: ProviderRegistry | None = None,
    offline: bool = False,
) -> tuple[MarketplaceListing, list[str]]:
    """Fetch a listing and everything the providers will tell us about it."""
    registry = registry or get_registry()
    notes: list[str] = []

    if offline:
        listing = catalog.get_listing(session, auth.organization_id, marketplace, external_id)
        if listing is None:
            raise NotFoundError(
                f"No stored listing {external_id} on {marketplace.value}, and the "
                "analysis was requested offline."
            )
        return listing, ["Offline analysis: stored data only, no provider calls."]

    provider = registry.for_marketplace(marketplace, capability=ProviderCapability.PRODUCT)
    raw = await provider.get_product(external_id)
    listing = catalog.upsert_listing(session, auth.organization_id, raw)
    catalog.resolve_product(session, auth.organization_id, raw, listing)

    # One stamp for every observation this call produces. Built once because the
    # identifier lookup is a query, and stated explicitly rather than inferred so
    # that a fixture answer can never be recorded as a market observation.
    stamp = catalog.observation_stamp(
        session, listing, provider=provider.slug, is_simulated=not provider.is_live
    )
    catalog.snapshot_current_price(
        session,
        auth.organization_id,
        listing,
        provider=provider.slug,
        stamp=stamp,
        source=history_capture.SOURCE_ANALYSIS,
    )
    # Anything analysed enters the observation universe. The dataset is only
    # worth owning if it kept observing after the question that prompted it.
    history.track(
        session,
        auth.organization_id,
        marketplace=marketplace,
        external_id=external_id,
        listing=listing,
        reason=history.REASON_ANALYSIS,
    )
    if not provider.is_live:
        notes.append(
            f"{marketplace.value} data came from {provider.display_name}, which is "
            "fixture data rather than a market observation."
        )

    # Each optional capability is attempted independently: one provider that
    # cannot serve history must not cost us the offers it can serve.
    async def _offers() -> None:
        if not provider.supports(ProviderCapability.OFFERS):
            return
        offers = await provider.get_offers(external_id)
        catalog.replace_offers(
            session, auth.organization_id, listing, offers, provider=provider.slug
        )

    async def _history() -> None:
        if not provider.supports(ProviderCapability.HISTORY):
            notes.append(
                f"{provider.display_name} does not supply price history; statistics will "
                "build up from observations Spreadline records over time."
            )
            return
        points = await provider.get_history(external_id, days=HISTORY_DAYS)
        catalog.record_price_observations(
            session,
            auth.organization_id,
            listing,
            points,
            provider=provider.slug,
            source=history_capture.SOURCE_PROVIDER_HISTORY,
            stamp=stamp,
        )

    async def _demand() -> None:
        if not provider.supports(ProviderCapability.DEMAND):
            return
        points = await provider.get_demand(external_id, days=DEMAND_DAYS)
        catalog.record_demand_observations(
            session,
            auth.organization_id,
            listing,
            points,
            provider=provider.slug,
            stamp=stamp,
        )

    async def _competition() -> None:
        if not provider.supports(ProviderCapability.COMPETITION):
            return
        points = await provider.get_competition(external_id, days=COMPETITION_DAYS)
        catalog.record_competition_observations(
            session,
            auth.organization_id,
            listing,
            points,
            provider=provider.slug,
            stamp=stamp,
        )

    for coroutine in (_offers(), _history(), _demand(), _competition()):
        try:
            await coroutine
        except ProviderCapabilityError as exc:
            notes.append(str(exc))
        except ProviderError as exc:
            # A failed enrichment degrades the analysis; it does not abort it.
            # The missing data is reflected in the data quality score.
            logger.warning("provider enrichment failed", extra={"context": {"error": str(exc)}})
            notes.append(f"Enrichment failed: {exc}")

    session.flush()
    return listing, notes


async def find_counterpart(
    session: Session,
    auth: AuthContext,
    listing: MarketplaceListing,
    target_marketplace: Marketplace,
    *,
    registry: ProviderRegistry | None = None,
) -> tuple[MarketplaceListing | None, list[str]]:
    """Locate the same product on another marketplace.

    Identifier lookup first, title search only as a fallback, and the fallback is
    recorded in the notes because a title-sourced counterpart can never reach a
    high-confidence match.
    """
    registry = registry or get_registry()
    notes: list[str] = []
    provider = registry.for_marketplace(target_marketplace, capability=ProviderCapability.PRODUCT)

    from app.models.catalog import ProductIdentifier
    from app.models.enums import IdentifierType

    identifiers = []
    if listing.product_id:
        identifiers = list(
            session.scalars(
                select(ProductIdentifier).where(
                    ProductIdentifier.product_id == listing.product_id,
                    ProductIdentifier.is_valid.is_(True),
                )
            )
        )

    ordered = sorted(
        identifiers,
        key=lambda row: {
            IdentifierType.GTIN.value: 0,
            IdentifierType.UPC.value: 1,
            IdentifierType.EAN.value: 2,
            IdentifierType.MPN.value: 3,
            IdentifierType.MODEL.value: 4,
        }.get(row.identifier_type, 9),
    )

    for identifier in ordered:
        if identifier.identifier_type in {
            IdentifierType.ASIN.value,
            IdentifierType.WALMART_ITEM_ID.value,
        }:
            continue
        try:
            raw = await provider.get_product_by_identifier(
                identifier.identifier_type, identifier.value
            )
        except (ProviderCapabilityError, ProviderError) as exc:
            notes.append(f"Identifier lookup unavailable on {provider.display_name}: {exc}")
            break
        if raw is not None:
            target = catalog.upsert_listing(session, auth.organization_id, raw)
            catalog.resolve_product(session, auth.organization_id, raw, target)
            notes.append(
                f"Counterpart found on {target_marketplace.value} by "
                f"{identifier.identifier_type.upper()} {identifier.value}."
            )
            return target, notes

    if not provider.supports(ProviderCapability.SEARCH):
        notes.append(f"{provider.display_name} cannot search; no counterpart could be found.")
        return None, notes

    result = await provider.search_products(listing.title, limit=5)
    candidates = [(title_similarity(listing.title, item.title), item) for item in result.listings]
    candidates.sort(key=lambda row: row[0], reverse=True)
    best = candidates[0] if candidates else None

    # A weak title hit is not a counterpart. Accepting one would attach an
    # unrelated product's price history and offers to this analysis, and the
    # identity engine would then be arguing against evidence we invented.
    if best is None or best[0] < COUNTERPART_SEARCH_FLOOR:
        notes.append(
            f"No {target_marketplace.value} listing matched '{listing.title}' closely "
            f"enough to be a counterpart (best title similarity "
            f"{best[0]:.0%} against a {COUNTERPART_SEARCH_FLOOR:.0%} floor). "
            if best
            else f"No {target_marketplace.value} listing matched '{listing.title}'. "
        )
        notes.append("There is no opportunity without an exit market.")
        return None, notes

    raw = best[1]
    target = catalog.upsert_listing(session, auth.organization_id, raw)
    catalog.resolve_product(session, auth.organization_id, raw, target)
    notes.append(
        f"Counterpart found on {target_marketplace.value} by title search only. "
        "A title-sourced match cannot reach high confidence on its own."
    )
    return target, notes


# --------------------------------------------------------------------------
# Loading stored observations
# --------------------------------------------------------------------------


def load_price_points(
    session: Session, listing_id: str, *, as_of: datetime | None = None
) -> list[PricePoint]:
    """Stored price observations for one listing.

    ``as_of`` truncates the series to what existed at that instant. It is the
    single most important argument in this module: a backtest that can see a
    price recorded after its simulated present is not a backtest, it is a
    memory of the answer. Defaulting to ``None`` keeps live analysis unchanged.
    """
    query = select(PriceObservation).where(PriceObservation.listing_id == listing_id)
    if as_of is not None:
        query = query.where(PriceObservation.observed_at <= as_of)
    rows = session.scalars(query.order_by(PriceObservation.observed_at))
    return [PricePoint(price=row.landed_price, observed_at=row.observed_at) for row in rows]


def load_demand_points(
    session: Session, listing_id: str, *, as_of: datetime | None = None
) -> list[DemandPoint]:
    """Stored demand observations, optionally truncated to a point in time."""
    query = select(DemandObservation).where(DemandObservation.listing_id == listing_id)
    if as_of is not None:
        query = query.where(DemandObservation.observed_at <= as_of)
    rows = session.scalars(query.order_by(DemandObservation.observed_at))
    return [
        DemandPoint(
            sales_rank=row.sales_rank,
            observed_at=row.observed_at,
            review_count=row.review_count,
            rank_category=row.rank_category,
            estimated_monthly_units=row.estimated_monthly_units,
            estimation_basis=row.estimation_basis,
        )
        for row in rows
    ]


def load_competition_points(
    session: Session, listing_id: str, *, as_of: datetime | None = None
) -> list[CompetitionPoint]:
    """Stored competition observations, optionally truncated to a point in time."""
    query = select(CompetitionObservation).where(CompetitionObservation.listing_id == listing_id)
    if as_of is not None:
        query = query.where(CompetitionObservation.observed_at <= as_of)
    rows = session.scalars(query.order_by(CompetitionObservation.observed_at))
    return [
        CompetitionPoint(
            observed_at=row.observed_at,
            seller_count=row.seller_count,
            offer_count=row.offer_count,
            lowest_price=row.lowest_price,
            median_price=row.median_price,
            buy_box_price=row.buy_box_price,
            buy_box_seller_id=row.buy_box_seller_id,
            marketplace_is_seller=row.marketplace_is_seller,
            seller_ids=tuple(row.seller_ids or ()),
        )
        for row in rows
    ]


def load_offer_snapshots(session: Session, listing: MarketplaceListing) -> list[OfferSnapshot]:
    return [
        OfferSnapshot(
            price=offer.price,
            shipping=offer.shipping,
            seller_id=offer.seller_id,
            is_buy_box=offer.is_buy_box,
            is_marketplace_seller=offer.is_marketplace_seller,
        )
        for offer in listing.offers
    ]


def _listing_context(listing: MarketplaceListing, *, is_live: bool) -> ListingContext:
    quantity = None
    buy_box = next((offer for offer in listing.offers if offer.is_buy_box), None)
    if buy_box is not None:
        quantity = buy_box.quantity_available
    return ListingContext(
        listing_id=listing.id,
        marketplace=Marketplace(listing.marketplace),
        external_id=listing.external_id,
        title=listing.title,
        price=listing.current_price,
        shipping=listing.current_shipping or Decimal("0"),
        availability=_availability(listing.availability),
        quantity_available=quantity,
        seller_count=listing.seller_count,
        url=listing.url,
        provider=listing.provider,
        is_live_data=is_live,
        observed_at=listing.last_seen_at,
    )


def _availability(value: str | None) -> Availability:
    try:
        return Availability(value or "unknown")
    except ValueError:
        return Availability.UNKNOWN


def _decimal_attribute(listing: MarketplaceListing, key: str) -> Decimal | None:
    value = (listing.raw_attributes or {}).get(key)
    if value in (None, ""):
        return None
    try:
        return to_decimal(value)
    except ValueError:
        return None


def _quality_dimensions(
    source: MarketplaceListing,
    target: MarketplaceListing,
    match_confidence_status: str,
    source_prices: PriceHistoryAnalysis,
    target_prices: PriceHistoryAnalysis,
    demand: DemandAssessment,
    competition: CompetitionAssessment,
    match_confidence: Decimal,
) -> list[QualityDimension]:
    dimensions: list[QualityDimension] = []

    price_confidence = Confidence.NONE
    if source.current_price is not None and target.current_price is not None:
        price_confidence = Confidence.HIGH
    elif source.current_price is not None or target.current_price is not None:
        price_confidence = Confidence.LOW
    dimensions.append(
        build_dimension(
            DataQualityDimension.PRICE,
            price_confidence,
            (
                "Current prices observed on both sides."
                if price_confidence is Confidence.HIGH
                else "A current price is missing on at least one side."
            ),
            observed_at=min(filter(None, [source.last_seen_at, target.last_seen_at]), default=None),
            source=source.provider,
        )
    )

    if match_confidence >= Decimal("0.90"):
        match_conf = Confidence.HIGH
    elif match_confidence >= Decimal("0.75"):
        match_conf = Confidence.MEDIUM
    elif match_confidence > 0:
        match_conf = Confidence.LOW
    else:
        match_conf = Confidence.NONE
    dimensions.append(
        build_dimension(
            DataQualityDimension.PRODUCT_MATCH,
            match_conf,
            f"Identity {match_confidence_status} at {match_confidence:.0%} confidence.",
        )
    )

    history_confidence = min(
        (source_prices.confidence, target_prices.confidence),
        key=lambda value: ["none", "low", "medium", "high"].index(value.value),
    )
    dimensions.append(
        build_dimension(
            DataQualityDimension.HISTORICAL_DATA,
            history_confidence,
            (
                f"{source_prices.observation_count} source and "
                f"{target_prices.observation_count} target price observations; "
                f"reference windows {source_prices.reference_window} and "
                f"{target_prices.reference_window} days."
            ),
        )
    )
    dimensions.append(
        build_dimension(
            DataQualityDimension.COMPETITION,
            competition.confidence,
            f"Competition assessed with {competition.confidence.value} confidence.",
        )
    )
    dimensions.append(
        build_dimension(
            DataQualityDimension.DEMAND,
            demand.confidence,
            (
                f"{demand.observation_count} demand observation(s) over {demand.span_days} day(s)."
                if demand.observation_count
                else "No demand observations."
            ),
        )
    )
    availability_known = _availability(source.availability) is not Availability.UNKNOWN
    dimensions.append(
        build_dimension(
            DataQualityDimension.AVAILABILITY,
            Confidence.HIGH if availability_known else Confidence.NONE,
            (
                f"Source availability reported as {source.availability}."
                if availability_known
                else "Source availability is unknown."
            ),
            observed_at=source.last_seen_at,
        )
    )
    return dimensions


def build_context(
    session: Session,
    source: MarketplaceListing,
    target: MarketplaceListing,
    *,
    match,
    options: AnalysisOptions,
    assumptions: FeeAssumptions | None = None,
    source_is_live: bool = False,
    target_is_live: bool = False,
    warnings: Sequence[str] = (),
) -> AnalysisContext:
    """Assemble everything the engines need from stored data."""
    source_points = load_price_points(session, source.id)
    target_points = load_price_points(session, target.id)
    source_prices = analyze_prices(source_points)
    target_prices = analyze_prices(target_points)

    source_offers = load_offer_snapshots(session, source)
    source_quantity = next(
        (offer.quantity_available for offer in source.offers if offer.is_buy_box), None
    )

    source_anomaly: PriceAnomaly = detect_anomaly(
        source_prices,
        source_points,
        availability=_availability(source.availability),
        quantity_available=source_quantity,
    )
    target_anomaly: PriceAnomaly = detect_anomaly(
        target_prices, target_points, availability=_availability(target.availability)
    )

    # Demand and competition are measured on the *exit* market: what matters is
    # whether the unit sells where it will be listed, not where it was bought.
    demand = assess_demand(
        load_demand_points(session, target.id),
        current_rank=target.sales_rank,
        current_review_count=target.review_count,
    )
    competition = assess_competition(
        load_competition_points(session, target.id), load_offer_snapshots(session, target)
    )

    category = target.category or source.category
    fees = assumptions or assumptions_for(target.marketplace, options.fee_overrides)
    weight = _decimal_attribute(source, "weight_lb") or _decimal_attribute(target, "weight_lb")
    cubic_feet = _decimal_attribute(source, "cubic_feet") or _decimal_attribute(
        target, "cubic_feet"
    )

    sale_price = target.current_price or Decimal("0")
    acquisition = source.current_price or Decimal("0")
    acquisition_shipping = source.current_shipping or Decimal("0")

    profitability: ProfitabilityResult = calculate_profitability(
        ProfitabilityInput(
            sale_price=sale_price,
            acquisition_cost=acquisition,
            target_marketplace=Marketplace(target.marketplace),
            acquisition_shipping=acquisition_shipping,
            category=category,
            weight_lb=weight,
            cubic_feet=cubic_feet,
        ),
        fees,
    )

    quality: DataQualityScore = score_quality(
        _quality_dimensions(
            source,
            target,
            match.status.value,
            source_prices,
            target_prices,
            demand,
            competition,
            match.confidence,
        )
    )

    all_warnings = list(warnings) + list(profitability.warnings)
    if source_offers and not target.offers:
        all_warnings.append(
            "No offer detail on the exit listing; competition rests on counts only."
        )

    return AnalysisContext(
        product_id=target.product_id or source.product_id,
        title=source.title,
        brand=source.brand or target.brand,
        category=category,
        source=_listing_context(source, is_live=source_is_live),
        target=_listing_context(target, is_live=target_is_live),
        match=match,
        profitability=profitability,
        quality=quality,
        source_prices=source_prices,
        target_prices=target_prices,
        source_anomaly=source_anomaly,
        target_anomaly=target_anomaly,
        demand=demand,
        competition=competition,
        direction=options.direction,
        sourcing_channel=options.sourcing_channel,
        weight_lb=weight,
        cubic_feet=cubic_feet,
        analyzed_at=utcnow(),
        is_live_data=source_is_live and target_is_live,
        warnings=all_warnings,
    )


def evaluate(context: AnalysisContext, options: AnalysisOptions) -> AnalysisResult:
    """Run risk, scoring, decision and stress testing over a built context."""
    risk = assess_risk(context)
    score = score_opportunity(context, risk, model=options.scoring_model)
    decision = decide(context, risk, score, policy=options.decision_policy)
    # Whether today's gap is the normal state of these two markets. Read from
    # the same histories the statistics came from, so it costs no extra fetch.
    spread_evidence = assess_spread(
        current_spread=context.profitability.gross_spread,
        source_prices=context.source_prices,
        target_prices=context.target_prices,
    )
    stress = None
    if options.run_stress_test:
        stress = run_stress_test(
            context, weight_lb=context.weight_lb, cubic_feet=context.cubic_feet
        )
    return AnalysisResult(
        context=context,
        risk=risk,
        score=score,
        decision=decision,
        stress=stress,
        spread_evidence=spread_evidence,
    )


async def analyze_pair(
    session: Session,
    auth: AuthContext,
    *,
    source_marketplace: Marketplace,
    source_external_id: str,
    target_marketplace: Marketplace,
    target_external_id: str | None = None,
    options: AnalysisOptions | None = None,
    registry: ProviderRegistry | None = None,
) -> AnalysisResult:
    """Full pipeline for one source listing against one exit market."""
    options = options or AnalysisOptions()
    registry = registry or get_registry()
    notes: list[str] = []

    if source_marketplace is target_marketplace and target_external_id is None:
        raise ValidationError("Source and exit marketplace cannot be the same.")

    source, source_notes = await ingest_listing(
        session,
        auth,
        source_marketplace,
        source_external_id,
        registry=registry,
        offline=options.offline,
    )
    notes.extend(source_notes)

    if target_external_id:
        target, target_notes = await ingest_listing(
            session,
            auth,
            target_marketplace,
            target_external_id,
            registry=registry,
            offline=options.offline,
        )
        notes.extend(target_notes)
    else:
        target, find_notes = await find_counterpart(
            session, auth, source, target_marketplace, registry=registry
        )
        notes.extend(find_notes)
        if target is None:
            raise NotFoundError(
                f"No {target_marketplace.value} listing could be matched to "
                f"{source.title!r}. Without an exit market there is no opportunity."
            )
        target, enrich_notes = await ingest_listing(
            session,
            auth,
            target_marketplace,
            target.external_id,
            registry=registry,
            offline=options.offline,
        )
        notes.extend(enrich_notes)

    match, _ = resolve_match(session, auth.organization_id, source, target, persist=options.persist)

    source_live = _provider_is_live(registry, source)
    target_live = _provider_is_live(registry, target)

    direction = options.direction
    if direction is Direction.CUSTOM:
        direction = _direction_for(source_marketplace, target_marketplace)
    options = AnalysisOptions(
        direction=direction,
        sourcing_channel=options.sourcing_channel,
        fee_overrides=options.fee_overrides,
        scoring_model=options.scoring_model,
        decision_policy=options.decision_policy,
        offline=options.offline,
        run_stress_test=options.run_stress_test,
        persist=options.persist,
    )

    context = build_context(
        session,
        source,
        target,
        match=match,
        options=options,
        source_is_live=source_live,
        target_is_live=target_live,
        warnings=notes,
    )
    result = evaluate(context, options)
    result.notes = notes

    if options.persist:
        from app.domains.opportunities.service import persist_analysis

        opportunity = persist_analysis(session, auth, source, target, result)
        result.opportunity_id = opportunity.id
    return result


def _provider_is_live(registry: ProviderRegistry, listing: MarketplaceListing) -> bool:
    """Whether this listing's price is a real observation of a market.

    Hand-entered listings are, and they are the one case that is not a provider.
    A person opened the page and read the price off it, so nothing about it is
    invented; it is the registry that cannot answer, because nothing fetched.
    Falling through to the generic "unknown provider means not live" would have
    labelled a price somebody verified with their own eyes as fixture data,
    which is a worse lie than the one that rule exists to prevent.
    """
    provider = listing.provider or ""
    if provider == manual.MANUAL_PROVIDER:
        return True
    try:
        return registry.get(provider).is_live
    except ProviderError:
        return False


def _direction_for(source: Marketplace, target: Marketplace) -> Direction:
    if source is Marketplace.AMAZON and target is Marketplace.WALMART:
        return Direction.AMAZON_TO_WALMART
    if source is Marketplace.WALMART and target is Marketplace.AMAZON:
        return Direction.WALMART_TO_AMAZON
    return Direction.CUSTOM


async def analyze_both_directions(
    session: Session,
    auth: AuthContext,
    *,
    amazon_external_id: str,
    walmart_external_id: str,
    options: AnalysisOptions | None = None,
    registry: ProviderRegistry | None = None,
) -> list[AnalysisResult]:
    """Evaluate Amazon -> Walmart and Walmart -> Amazon for the same product.

    Both directions are always evaluated because which one is profitable is an
    empirical question, and assuming the answer is how a platform ends up only
    ever finding opportunities in the direction its author expected.
    """
    results = []
    for source_marketplace, source_id, target_marketplace, target_id in (
        (Marketplace.AMAZON, amazon_external_id, Marketplace.WALMART, walmart_external_id),
        (Marketplace.WALMART, walmart_external_id, Marketplace.AMAZON, amazon_external_id),
    ):
        results.append(
            await analyze_pair(
                session,
                auth,
                source_marketplace=source_marketplace,
                source_external_id=source_id,
                target_marketplace=target_marketplace,
                target_external_id=target_id,
                options=options,
                registry=registry,
            )
        )
    return results


async def gather_with_limit(coroutines: Sequence[Any], limit: int = 5) -> list[Any]:
    """Run coroutines with bounded concurrency.

    Used by bulk analysis. The limit exists to respect provider rate limits and
    to keep a 500-row CSV from opening 500 simultaneous connections.
    """
    semaphore = asyncio.Semaphore(limit)

    async def _run(coroutine: Any) -> Any:
        async with semaphore:
            return await coroutine

    return await asyncio.gather(*(_run(item) for item in coroutines), return_exceptions=True)
