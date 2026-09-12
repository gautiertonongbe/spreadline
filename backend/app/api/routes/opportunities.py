"""Opportunity listing, detail, decisions and hand validation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Auth, DbSession
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


def _with_titles(session: Session, rows: list[Any]) -> list[OpportunitySummary]:
    """Attach the product title to each row in one extra query.

    Fetched as a batch rather than per row: a 100-row opportunity table should
    cost two queries, not a hundred and one.
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
    items: list[OpportunitySummary] = []
    for row in rows:
        item = OpportunitySummary.model_validate(row)
        product = products.get(row.product_id)
        if product is not None:
            item.title = product.title
            item.brand = product.brand
        items.append(item)
    return items


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
    scenarios = list(
        session.scalars(
            select(ProfitabilitySnapshot)
            .where(
                ProfitabilitySnapshot.opportunity_id == opportunity.id,
                ProfitabilitySnapshot.scenario != "base",
            )
            .order_by(ProfitabilitySnapshot.created_at.desc())
        )
    )
    risk = session.scalar(
        select(RiskAssessment)
        .where(RiskAssessment.opportunity_id == opportunity.id)
        .order_by(RiskAssessment.created_at.desc())
    )

    from app.domains.opportunities.analysis import load_price_points
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
            "model_version": risk.model_version,
        }
        if risk
        else None
    )
    detail.stress_test = {
        "scenarios": [_snapshot_payload(item) for item in scenarios],
    }
    detail.price_history = {
        "source": analyze_prices(load_price_points(session, source.id)).as_dict()
        if source
        else None,
        "target": analyze_prices(load_price_points(session, target.id)).as_dict()
        if target
        else None,
    }
    detail.events = [
        {
            "id": event.id,
            "type": event.event_type,
            "from_status": event.from_status,
            "to_status": event.to_status,
            "actor": event.actor,
            "message": event.message,
            "payload": event.payload,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        for event in opportunity.events
    ]
    detail.validations = [
        {
            "id": row.id,
            "tested_at": row.tested_at.isoformat(),
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
    if snapshot is None:
        return None
    return {
        "scenario": snapshot.scenario,
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
        "tested_at": validation.tested_at.isoformat(),
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
