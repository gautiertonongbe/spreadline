"""Opportunity listing, detail, decisions and hand validation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Auth, DbSession
from app.core.clock import iso_utc
from app.core.errors import ProviderError
from app.core.money import money, pct_of
from app.domains.opportunities import service as opportunities
from app.domains.validation.service import (
    ValidationInput,
    record_validation,
    validation_metrics,
)
from app.models.catalog import MarketplaceListing, Product
from app.models.enums import OpportunityStatus
from app.models.opportunity import ProfitabilitySnapshot, RiskAssessment
from app.schemas.common import Page
from app.schemas.opportunity import (
    DecisionRequest,
    ListingSummary,
    OpportunityDetail,
    OpportunitySummary,
    ProductSummary,
    ValidationRequest,
)
from app.services.providers.registry import get_registry

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("", response_model=Page[OpportunitySummary])
def list_opportunities(
    session: DbSession,
    auth: Auth,
    status: list[str] | None = Query(default=None),
    recommendation: list[str] | None = Query(default=None),
    risk_level: list[str] | None = Query(default=None),
    min_score: Decimal | None = None,
    min_roi: Decimal | None = None,
    min_profit: Decimal | None = None,
    marketplace: str | None = None,
    search: str | None = None,
    sort: str = Query(default="score", pattern="^(score|roi|profit|risk|analyzed_at|created_at)$"),
    descending: bool = True,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[OpportunitySummary]:
    rows, total = opportunities.list_opportunities(
        session,
        auth,
        status=status,
        recommendation=recommendation,
        risk_level=risk_level,
        min_score=min_score,
        min_roi=min_roi,
        min_profit=min_profit,
        marketplace=marketplace,
        search=search,
        sort=sort,
        descending=descending,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=_with_titles(session, rows),
        total=total,
        limit=limit,
        offset=offset,
    )


def _primary_blocker(explanation: dict[str, Any] | None) -> str | None:
    """The one thing a reader should know about before anything else.

    Hard gates come before soft ones because a hard gate ends the discussion,
    where a soft gate only explains why a profitable candidate is being held.

    The requirement and the finding are both included. A gate label on its own
    reads as a statement of fact rather than as the thing that failed: "No
    serious warnings" next to a held candidate says the opposite of what
    happened. Pairing it with the measurement fixes that without inventing a
    second phrasing of every gate.
    """
    gates = (explanation or {}).get("gates") or []
    failed = [
        gate for gate in gates if isinstance(gate, dict) and not gate.get("passed", True)
    ]
    if not failed:
        return None
    failed.sort(key=lambda gate: not gate.get("hard", False))
    label = str(failed[0].get("label") or "").strip()
    detail = str(failed[0].get("detail") or "").strip()
    if label and detail:
        return f"{label.rstrip('.')}. {detail}"
    return label or detail or None


def _with_titles(session: Session, rows: list[Any]) -> list[OpportunitySummary]:
    """Attach the product title and the decision line to each row.

    Both extra reads are batched rather than issued per row: a 100-row table
    should cost three queries, not two hundred and one. The list view exists to
    answer "which of these should I act on", and it cannot answer that with
    marketplace names and numbers alone, so the stored headline, the first
    failed gate and the maximum acquisition cost travel with the row.
    """
    product_ids = {row.product_id for row in rows if row.product_id}
    products = (
        {
            product.id: product
            for product in session.scalars(
                select(Product).where(Product.id.in_(product_ids))
            )
        }
        if product_ids
        else {}
    )

    # The most recent base snapshot per opportunity. Ordered oldest first so the
    # later row overwrites, leaving the newest without a per-row query.
    snapshots: dict[str, ProfitabilitySnapshot] = {}
    opportunity_ids = [row.id for row in rows]
    if opportunity_ids:
        for snapshot in session.scalars(
            select(ProfitabilitySnapshot)
            .where(
                ProfitabilitySnapshot.opportunity_id.in_(opportunity_ids),
                ProfitabilitySnapshot.scenario == "base",
            )
            .order_by(ProfitabilitySnapshot.created_at.asc())
        ):
            if snapshot.opportunity_id is not None:
                snapshots[snapshot.opportunity_id] = snapshot

    items: list[OpportunitySummary] = []
    for row in rows:
        item = OpportunitySummary.model_validate(row)
        product = products.get(row.product_id)
        if product is not None:
            item.title = product.title
            item.brand = product.brand
        explanation = row.explanation or {}
        headline = explanation.get("headline")
        item.headline = str(headline) if headline else None
        item.primary_blocker = _primary_blocker(explanation)
        _attach_economics(item, snapshots.get(row.id))
        items.append(item)
    return items


def _attach_economics(item: OpportunitySummary, snapshot: ProfitabilitySnapshot | None) -> None:
    """Carry the whole economic ladder onto a row.

    Cost, gap, gap percent, fees, profit and the ceiling are one chain, and a
    row that shows only its two ends asks the reader to take the middle on
    trust. Read off the stored snapshot rather than recomputed, so the figures
    on a row are the ones the decision was actually made from.
    """
    if snapshot is None:
        return
    item.total_cost = snapshot.total_cost
    item.total_fees = snapshot.total_fees
    item.other_unit_costs = money(
        snapshot.inbound_shipping + snapshot.tax + snapshot.misc_cost
    )
    item.max_acquisition_cost = snapshot.max_acquisition_cost
    landed = money(snapshot.acquisition_cost + snapshot.acquisition_shipping)
    item.gross_spread = money(snapshot.sale_price - landed)
    # None rather than zero when nothing was paid: an undefined percentage and a
    # zero percentage lead to different decisions.
    item.gross_spread_pct = pct_of(item.gross_spread, landed)


@router.get("/summary")
def opportunity_summary(session: DbSession, auth: Auth) -> dict[str, Any]:
    return opportunities.dashboard_counts(session, auth)


@router.get("/{opportunity_id}", response_model=OpportunityDetail)
def get_opportunity(opportunity_id: str, session: DbSession, auth: Auth) -> OpportunityDetail:
    """The complete decision view (spec §27)."""
    opportunity = opportunities.get_opportunity(session, auth, opportunity_id)
    source = session.get(MarketplaceListing, opportunity.source_listing_id)
    target = session.get(MarketplaceListing, opportunity.target_listing_id)
    product = session.get(Product, opportunity.product_id)

    base_snapshot = session.scalar(
        select(ProfitabilitySnapshot)
        .where(
            ProfitabilitySnapshot.opportunity_id == opportunity.id,
            ProfitabilitySnapshot.scenario == "base",
        )
        .order_by(ProfitabilitySnapshot.created_at.desc())
    )
    # The newest snapshot per scenario, not every snapshot ever written.
    #
    # Snapshots are append-only, and re-analysing a pair writes a fresh set.
    # Rendering all of them showed the same six scenarios once per analysis, so a
    # product analysed six times reported "36 scenarios" and listed each one six
    # times over. Keyed on the scenario name rather than on a timestamp window
    # because rows written in one flush can share a created_at to the microsecond.
    scenarios: list[ProfitabilitySnapshot] = []
    seen_scenarios: set[str] = set()
    for snapshot in session.scalars(
        select(ProfitabilitySnapshot)
        .where(
            ProfitabilitySnapshot.opportunity_id == opportunity.id,
            ProfitabilitySnapshot.scenario != "base",
        )
        .order_by(ProfitabilitySnapshot.created_at.desc(), ProfitabilitySnapshot.id.desc())
    ):
        if snapshot.scenario in seen_scenarios:
            continue
        seen_scenarios.add(snapshot.scenario)
        scenarios.append(snapshot)
    risk = session.scalar(
        select(RiskAssessment)
        .where(RiskAssessment.opportunity_id == opportunity.id)
        .order_by(RiskAssessment.created_at.desc())
    )

    from app.domains.competition.service import assess_competition
    from app.domains.demand.service import assess_demand
    from app.domains.opportunities.analysis import (
        load_competition_points,
        load_demand_points,
        load_offer_snapshots,
        load_price_points,
    )
    from app.domains.pricing.spread_evidence import assess_spread
    from app.domains.pricing.statistics import analyze_prices

    # Built from the summary fields rather than validated straight off the ORM
    # object: ``events`` and ``validations`` are relationships whose rendered
    # shape differs from the stored rows, and from_attributes would try to coerce
    # the ORM objects into them.
    detail = OpportunityDetail(
        **OpportunitySummary.model_validate(opportunity).model_dump(),
        score_model_version=opportunity.score_model_version,
        score_components=opportunity.score_components or {},
        explanation=opportunity.explanation or {},
        recommended_quantity=opportunity.recommended_quantity,
        decided_at=opportunity.decided_at,
        decided_by=opportunity.decided_by,
        notes=opportunity.notes,
    )
    detail.product = ProductSummary.model_validate(product) if product else None
    detail.source_listing = ListingSummary.model_validate(source) if source else None
    detail.target_listing = ListingSummary.model_validate(target) if target else None
    detail.match = _match_payload(session, opportunity)
    detail.economics = _snapshot_payload(base_snapshot)
    detail.risk = (
        {
            "level": risk.level,
            "score": str(risk.score),
            "summary": risk.summary,
            "signals": risk.signals,
            "categories": risk.categories,
            "unassessed_categories": [
                item["category"]
                for item in (risk.categories or [])
                if not item.get("has_evidence", True)
            ],
            "driving_category": (
                max(
                    risk.signals,
                    key=lambda signal: Decimal(str(signal.get("points", "0"))),
                ).get("category")
                if risk.signals
                else None
            ),
            "model_version": risk.model_version,
        }
        if risk
        else None
    )
    detail.stress_test = {
        "scenarios": [_snapshot_payload(item) for item in scenarios],
    }
    source_prices = analyze_prices(load_price_points(session, source.id)) if source else None
    target_prices = analyze_prices(load_price_points(session, target.id)) if target else None
    detail.price_history = {
        "source": source_prices.as_dict() if source_prices else None,
        "target": target_prices.as_dict() if target_prices else None,
    }
    # Recomputed from the observations stored since, rather than frozen at
    # analysis time: whether a gap is a standing feature of two markets is a
    # claim about history, and history keeps arriving.
    if source_prices is not None and target_prices is not None and base_snapshot is not None:
        landed = money(base_snapshot.acquisition_cost + base_snapshot.acquisition_shipping)
        detail.spread_evidence = assess_spread(
            current_spread=money(base_snapshot.sale_price - landed),
            source_prices=source_prices,
            target_prices=target_prices,
        ).as_dict()
    # Demand and competition are recomputed from the stored observations for the
    # same reason the price history is: they are the evidence a BUY rests on,
    # and a decision record that cannot show them cannot be audited.
    if target is not None:
        detail.demand = assess_demand(
            load_demand_points(session, target.id),
            current_rank=target.sales_rank,
            current_review_count=target.review_count,
        ).as_dict()
        detail.competition = assess_competition(
            load_competition_points(session, target.id),
            load_offer_snapshots(session, target),
        ).as_dict()

    # The same provenance block a live analysis publishes, read off the stored
    # listings. When real providers are connected this is where the reader sees
    # which adapter answered and whether it was a market observation.
    registry = get_registry()

    def _is_live(slug: str | None) -> bool | None:
        """Whether that provider calls a market, or None if it is not registered.

        Resolved through the registry rather than guessed from the slug: what a
        provider name means is the registry's to say, and a row written by a
        provider that is no longer configured is reported as unknown rather
        than as fixture data.
        """
        if not slug:
            return None
        try:
            return registry.get(slug).is_live
        except ProviderError:
            return None

    detail.provenance = [
        {
            "role": role,
            "marketplace": listing.marketplace,
            "external_id": listing.external_id,
            "provider": listing.provider,
            "is_live_data": _is_live(listing.provider),
            "observed_at": iso_utc(listing.last_seen_at),
        }
        for role, listing in (("source", source), ("target", target))
        if listing is not None
    ]

    detail.events = [
        {
            "id": event.id,
            "type": event.event_type,
            "from_status": event.from_status,
            "to_status": event.to_status,
            "actor": event.actor,
            "message": event.message,
            "payload": event.payload,
            "created_at": iso_utc(event.created_at),
        }
        for event in opportunity.events
    ]
    detail.validations = [
        {
            "id": row.id,
            "tested_at": iso_utc(row.tested_at),
            "tested_by": row.tested_by,
            "actual_source_price": str(row.actual_source_price)
            if row.actual_source_price is not None
            else None,
            "actual_target_price": str(row.actual_target_price)
            if row.actual_target_price is not None
            else None,
            "match_confirmed": row.match_confirmed,
            "profit_estimate_correct": row.profit_estimate_correct,
            "would_buy": row.would_buy,
            "source_price_delta": str(row.source_price_delta)
            if row.source_price_delta is not None
            else None,
            "target_price_delta": str(row.target_price_delta)
            if row.target_price_delta is not None
            else None,
            "notes": row.notes,
        }
        for row in opportunity.validations
    ]
    return detail


def _match_payload(session: DbSession, opportunity: Any) -> dict[str, Any] | None:
    from app.models.identity import ProductMatch

    if not opportunity.match_id:
        return None
    row = session.get(ProductMatch, opportunity.match_id)
    if row is None:
        return None
    return {
        "confidence": str(row.match_confidence),
        "method": row.match_method,
        "status": row.status,
        "evidence": row.evidence,
        "conflicts": row.conflicts,
        "variation_check_passed": row.variation_check_passed,
        "reviewed_by": row.reviewed_by,
    }


def _snapshot_payload(snapshot: ProfitabilitySnapshot | None) -> dict[str, Any] | None:
    """Render a stored snapshot in the same shape a live analysis returns.

    The derived terms are recomputed from the stored components rather than
    left out. They are arithmetic over figures already in the row, so deriving
    them here cannot disagree with the record, and omitting them made the
    detail page show "not available" for a gap it had every number to compute.
    """
    if snapshot is None:
        return None
    landed = money(snapshot.acquisition_cost + snapshot.acquisition_shipping)
    other = money(snapshot.inbound_shipping + snapshot.tax + snapshot.misc_cost)
    gross_spread = money(snapshot.sale_price - landed)
    return {
        "scenario": snapshot.scenario,
        "landed_acquisition_cost": str(landed),
        "other_unit_costs": str(other),
        "gross_spread": str(gross_spread),
        "spread": str(gross_spread),
        "gross_spread_pct": (
            str(pct_of(gross_spread, landed)) if pct_of(gross_spread, landed) is not None else None
        ),
        "sale_price": str(snapshot.sale_price),
        "acquisition_cost": str(snapshot.acquisition_cost),
        "acquisition_shipping": str(snapshot.acquisition_shipping),
        "referral_fee": str(snapshot.referral_fee),
        "fulfillment_fee": str(snapshot.fulfillment_fee),
        "closing_fee": str(snapshot.closing_fee),
        "storage_fee": str(snapshot.storage_fee),
        "inbound_shipping": str(snapshot.inbound_shipping),
        "return_allowance": str(snapshot.return_allowance),
        "tax": str(snapshot.tax),
        "misc_cost": str(snapshot.misc_cost),
        "total_fees": str(snapshot.total_fees),
        "total_cost": str(snapshot.total_cost),
        "net_profit": str(snapshot.net_profit),
        "roi": str(snapshot.roi) if snapshot.roi is not None else None,
        "margin": str(snapshot.margin) if snapshot.margin is not None else None,
        "breakeven_sale_price": str(snapshot.breakeven_sale_price)
        if snapshot.breakeven_sale_price is not None
        else None,
        "max_acquisition_cost": str(snapshot.max_acquisition_cost)
        if snapshot.max_acquisition_cost is not None
        else None,
        "line_items": snapshot.line_items,
        "assumptions_version": snapshot.assumptions_version,
        "assumptions": snapshot.assumptions,
        "fulfillment": snapshot.fulfillment,
    }


@router.post("/{opportunity_id}/decision", response_model=OpportunitySummary)
def record_decision(
    opportunity_id: str, payload: DecisionRequest, session: DbSession, auth: Auth
) -> Any:
    """Move an opportunity through its lifecycle."""
    opportunity = opportunities.get_opportunity(session, auth, opportunity_id)
    opportunities.transition(
        session, auth, opportunity, OpportunityStatus(payload.status), note=payload.note
    )
    session.commit()
    return opportunity


@router.post("/{opportunity_id}/validate")
def validate_opportunity(
    opportunity_id: str, payload: ValidationRequest, session: DbSession, auth: Auth
) -> dict[str, Any]:
    """Record a hand-checked observation (personal testing mode, spec §22)."""
    opportunity = opportunities.get_opportunity(session, auth, opportunity_id)
    validation = record_validation(
        session,
        auth,
        opportunity,
        ValidationInput(
            actual_source_price=payload.actual_source_price,
            actual_target_price=payload.actual_target_price,
            actual_availability=payload.actual_availability,
            match_confirmed=payload.match_confirmed,
            profit_estimate_correct=payload.profit_estimate_correct,
            would_buy=payload.would_buy,
            notes=payload.notes,
        ),
    )
    session.commit()
    return {
        "id": validation.id,
        "opportunity_id": opportunity.id,
        "tested_at": iso_utc(validation.tested_at),
        "source_price_delta": str(validation.source_price_delta)
        if validation.source_price_delta is not None
        else None,
        "target_price_delta": str(validation.target_price_delta)
        if validation.target_price_delta is not None
        else None,
        "predicted_recommendation": validation.predicted_recommendation,
    }


@router.get("/validation/metrics")
def get_validation_metrics(session: DbSession, auth: Auth) -> dict[str, Any]:
    return validation_metrics(session, auth)
