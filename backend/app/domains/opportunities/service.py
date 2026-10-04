"""Opportunity persistence and lifecycle.

Storing an analysis writes four things: the opportunity, the profitability
snapshot with its frozen assumptions, the risk assessment with its signals, and
an event. Nothing is overwritten in a way that would destroy the record of what
was believed at decision time.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.core.security import AuthContext
from app.domains.opportunities.analysis import AnalysisResult
from app.domains.opportunities.stress import StressTestResult
from app.models.catalog import MarketplaceListing
from app.models.enums import (
    OPPORTUNITY_TRANSITIONS,
    OpportunityEventType,
    OpportunityStatus,
    Recommendation,
    RiskLevel,
)
from app.models.identity import ProductMatch
from app.models.opportunity import (
    Opportunity,
    OpportunityEvent,
    ProfitabilitySnapshot,
    RiskAssessment,
)

#: Recommendation -> the status a freshly analysed opportunity starts in.
INITIAL_STATUS: dict[Recommendation, OpportunityStatus] = {
    Recommendation.BUY: OpportunityStatus.NEW,
    Recommendation.REVIEW: OpportunityStatus.REVIEW,
    Recommendation.PASS: OpportunityStatus.REJECTED,
}


def record_event(
    session: Session,
    auth: AuthContext,
    opportunity: Opportunity,
    event_type: OpportunityEventType,
    *,
    message: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    payload: dict[str, Any] | None = None,
) -> OpportunityEvent:
    event = OpportunityEvent(
        organization_id=auth.organization_id,
        opportunity_id=opportunity.id,
        event_type=event_type.value,
        from_status=from_status,
        to_status=to_status,
        actor=auth.actor,
        message=message,
        payload=payload or {},
    )
    session.add(event)
    session.flush()
    return event


def persist_analysis(
    session: Session,
    auth: AuthContext,
    source: MarketplaceListing,
    target: MarketplaceListing,
    result: AnalysisResult,
) -> Opportunity:
    """Create or update the opportunity for this source/target pair."""
    context = result.context
    existing = session.scalar(
        select(Opportunity).where(
            Opportunity.organization_id == auth.organization_id,
            Opportunity.source_listing_id == source.id,
            Opportunity.target_listing_id == target.id,
        )
    )
    is_new = existing is None
    opportunity = existing or Opportunity(
        organization_id=auth.organization_id,
        product_id=target.product_id or source.product_id,
        source_listing_id=source.id,
        target_listing_id=target.id,
        status=INITIAL_STATUS[result.decision.recommendation].value,
    )
    if is_new:
        session.add(opportunity)

    match_row = session.scalar(
        select(ProductMatch).where(
            ProductMatch.source_listing_id == source.id,
            ProductMatch.target_listing_id == target.id,
        )
    )

    profitability = context.profitability
    opportunity.product_id = target.product_id or source.product_id
    opportunity.match_id = match_row.id if match_row else None
    opportunity.direction = context.direction.value
    opportunity.sourcing_channel = context.sourcing_channel.value
    opportunity.source_marketplace = source.marketplace
    opportunity.target_marketplace = target.marketplace
    opportunity.recommendation = result.decision.recommendation.value
    opportunity.score = result.score.total
    opportunity.score_model_version = result.score.model_version
    opportunity.score_components = result.score.as_dict()
    opportunity.acquisition_cost = profitability.acquisition_cost
    opportunity.expected_sale_price = profitability.sale_price
    opportunity.net_profit = profitability.net_profit
    opportunity.roi = profitability.roi
    opportunity.margin = profitability.margin
    opportunity.spread = profitability.spread
    opportunity.risk_level = result.risk.level.value
    opportunity.risk_score = result.risk.score
    opportunity.match_confidence = context.match.confidence
    opportunity.data_quality_score = context.quality.score
    opportunity.demand_confidence = context.demand.confidence.value
    opportunity.explanation = {
        **result.decision.as_dict(),
        "warnings": context.warnings,
        "is_live_data": context.is_live_data,
    }
    opportunity.recommended_quantity = context.source.quantity_available
    opportunity.analyzed_at = context.analyzed_at
    session.flush()

    # A rescore must not resurrect an opportunity the operator already actioned,
    # nor silently overwrite a purchase. Only pre-decision statuses move.
    if not is_new and opportunity.status in {
        OpportunityStatus.NEW.value,
        OpportunityStatus.REVIEW.value,
        OpportunityStatus.REJECTED.value,
    }:
        opportunity.status = INITIAL_STATUS[result.decision.recommendation].value

    _store_profitability(session, auth, opportunity, result)
    _store_risk(session, auth, opportunity, result)

    record_event(
        session,
        auth,
        opportunity,
        OpportunityEventType.CREATED if is_new else OpportunityEventType.RESCORED,
        message=result.decision.headline,
        to_status=opportunity.status,
        payload={
            "recommendation": result.decision.recommendation.value,
            "score": str(result.score.total),
            "risk_level": result.risk.level.value,
            "net_profit": str(profitability.net_profit),
            "assumptions_version": profitability.assumptions_version,
        },
    )
    session.flush()
    return opportunity


def _store_profitability(
    session: Session, auth: AuthContext, opportunity: Opportunity, result: AnalysisResult
) -> None:
    profitability = result.context.profitability
    snapshot = ProfitabilitySnapshot(
        organization_id=auth.organization_id,
        opportunity_id=opportunity.id,
        product_id=opportunity.product_id,
        scenario="base",
        target_marketplace=opportunity.target_marketplace,
        fulfillment=profitability.fulfillment.value,
        sale_price=profitability.sale_price,
        acquisition_cost=profitability.acquisition_cost,
        acquisition_shipping=profitability.acquisition_shipping,
        referral_fee=profitability.referral_fee,
        fulfillment_fee=profitability.fulfillment_fee,
        closing_fee=profitability.closing_fee,
        storage_fee=profitability.storage_fee,
        inbound_shipping=profitability.inbound_shipping,
        return_allowance=profitability.return_allowance,
        tax=profitability.tax,
        misc_cost=profitability.misc_cost,
        total_fees=profitability.total_fees,
        total_cost=profitability.total_cost,
        net_profit=profitability.net_profit,
        roi=profitability.roi,
        margin=profitability.margin,
        breakeven_sale_price=profitability.breakeven_sale_price,
        max_acquisition_cost=profitability.max_acquisition_cost,
        line_items=[item.as_dict() for item in profitability.line_items],
        # The full assumption set is embedded, not referenced: a later fee change
        # must not rewrite what this prediction was based on.
        assumptions=profitability.assumptions.model_dump(mode="json"),
        assumptions_version=profitability.assumptions_version,
    )
    session.add(snapshot)

    if result.stress:
        _store_scenarios(
            session, auth, opportunity, result.stress, profitability.assumptions_version
        )
    session.flush()


def _store_scenarios(
    session: Session,
    auth: AuthContext,
    opportunity: Opportunity,
    stress: StressTestResult,
    assumptions_version: str,
) -> None:
    for scenario in stress.scenarios:
        if scenario.scenario.key == "base":
            continue
        session.add(
            ProfitabilitySnapshot(
                organization_id=auth.organization_id,
                opportunity_id=opportunity.id,
                product_id=opportunity.product_id,
                scenario=scenario.scenario.key,
                target_marketplace=opportunity.target_marketplace,
                sale_price=scenario.sale_price,
                acquisition_cost=scenario.acquisition_cost,
                total_fees=scenario.total_fees,
                total_cost=scenario.acquisition_cost + scenario.total_fees,
                net_profit=scenario.net_profit,
                roi=scenario.roi,
                margin=scenario.margin,
                line_items=[],
                assumptions={"scenario": scenario.scenario.description, "notes": scenario.notes},
                assumptions_version=assumptions_version,
            )
        )


def _store_risk(
    session: Session, auth: AuthContext, opportunity: Opportunity, result: AnalysisResult
) -> None:
    session.add(
        RiskAssessment(
            organization_id=auth.organization_id,
            opportunity_id=opportunity.id,
            product_id=opportunity.product_id,
            scenario="base",
            level=result.risk.level.value,
            score=result.risk.score,
            signals=[signal.as_dict() for signal in result.risk.signals],
            categories=[item.as_dict() for item in result.risk.categories],
            summary=result.risk.summary,
            model_version=result.risk.model_version,
        )
    )


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------


def transition(
    session: Session,
    auth: AuthContext,
    opportunity: Opportunity,
    to_status: OpportunityStatus,
    *,
    note: str | None = None,
) -> Opportunity:
    """Move an opportunity through its lifecycle, refusing illegal jumps."""
    current = OpportunityStatus(opportunity.status)
    if to_status == current:
        return opportunity
    allowed = OPPORTUNITY_TRANSITIONS[current]
    if to_status not in allowed:
        raise ConflictError(
            f"Cannot move an opportunity from {current.value} to {to_status.value}. "
            f"Allowed: {', '.join(sorted(item.value for item in allowed)) or 'none'}.",
            opportunity_id=opportunity.id,
        )
    opportunity.status = to_status.value
    if to_status in {OpportunityStatus.APPROVED, OpportunityStatus.REJECTED}:
        opportunity.decided_at = utcnow()
        opportunity.decided_by = auth.actor
    record_event(
        session,
        auth,
        opportunity,
        OpportunityEventType.STATUS_CHANGED,
        message=note,
        from_status=current.value,
        to_status=to_status.value,
    )
    session.flush()
    return opportunity


def get_opportunity(session: Session, auth: AuthContext, opportunity_id: str) -> Opportunity:
    opportunity = session.scalar(
        select(Opportunity).where(
            Opportunity.id == opportunity_id,
            Opportunity.organization_id == auth.organization_id,
        )
    )
    if opportunity is None:
        raise NotFoundError(f"Opportunity {opportunity_id} was not found.")
    return opportunity


def list_opportunities(
    session: Session,
    auth: AuthContext,
    *,
    status: Sequence[str] | None = None,
    recommendation: Sequence[str] | None = None,
    risk_level: Sequence[str] | None = None,
    min_score: Decimal | None = None,
    min_roi: Decimal | None = None,
    min_profit: Decimal | None = None,
    marketplace: str | None = None,
    search: str | None = None,
    sort: str = "score",
    descending: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Opportunity], int]:
    query: Select = select(Opportunity).where(Opportunity.organization_id == auth.organization_id)
    if status:
        query = query.where(Opportunity.status.in_(list(status)))
    if recommendation:
        query = query.where(Opportunity.recommendation.in_(list(recommendation)))
    if risk_level:
        query = query.where(Opportunity.risk_level.in_(list(risk_level)))
    if min_score is not None:
        query = query.where(Opportunity.score >= min_score)
    if min_roi is not None:
        query = query.where(Opportunity.roi >= min_roi)
    if min_profit is not None:
        query = query.where(Opportunity.net_profit >= min_profit)
    if marketplace:
        query = query.where(
            (Opportunity.source_marketplace == marketplace)
            | (Opportunity.target_marketplace == marketplace)
        )
    if search:
        pattern = f"%{search.lower()}%"
        query = query.join(
            MarketplaceListing, Opportunity.source_listing_id == MarketplaceListing.id
        ).where(MarketplaceListing.title.ilike(pattern))

    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0

    sort_columns = {
        "score": Opportunity.score,
        "roi": Opportunity.roi,
        "profit": Opportunity.net_profit,
        "risk": Opportunity.risk_score,
        "analyzed_at": Opportunity.analyzed_at,
        "created_at": Opportunity.created_at,
    }
    column = sort_columns.get(sort, Opportunity.score)
    query = query.order_by(column.desc() if descending else column.asc())
    rows = list(session.scalars(query.limit(limit).offset(offset)))
    return rows, total


def dashboard_counts(session: Session, auth: AuthContext) -> dict[str, Any]:
    rows = list(
        session.scalars(
            select(Opportunity).where(Opportunity.organization_id == auth.organization_id)
        )
    )
    buy = [row for row in rows if row.recommendation == Recommendation.BUY.value]
    review = [row for row in rows if row.recommendation == Recommendation.REVIEW.value]
    return {
        "total": len(rows),
        "buy": len(buy),
        "review": len(review),
        "pass": len([row for row in rows if row.recommendation == Recommendation.PASS.value]),
        "high_risk": len(
            [
                row
                for row in rows
                if row.risk_level in {RiskLevel.HIGH.value, RiskLevel.CRITICAL.value}
            ]
        ),
        "expected_profit_buy": str(
            sum((row.net_profit or Decimal("0")) for row in buy) or Decimal("0")
        ),
    }
