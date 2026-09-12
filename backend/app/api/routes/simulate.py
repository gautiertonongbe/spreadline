"""Capital simulation (spec §20, §29)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import Auth, DbSession
from app.core.money import money
from app.domains.portfolio.capital import (
    AllocationCandidate,
    CapitalConstraints,
    allocate_capital,
)
from app.models.catalog import MarketplaceListing, Product
from app.models.enums import RiskLevel
from app.models.opportunity import Opportunity
from app.models.portfolio import CapitalPlan
from app.schemas.analysis import CapitalSimulationRequest

router = APIRouter(prefix="/simulate", tags=["capital"])


@router.post("/capital")
def simulate_capital(
    payload: CapitalSimulationRequest, session: DbSession, auth: Auth
) -> dict[str, Any]:
    """Allocate capital across stored opportunities under explicit constraints."""
    query = select(Opportunity).where(Opportunity.organization_id == auth.organization_id)
    if payload.opportunity_ids:
        query = query.where(Opportunity.id.in_(payload.opportunity_ids))
    rows = list(session.scalars(query))

    candidates: list[AllocationCandidate] = []
    for row in rows:
        product = session.get(Product, row.product_id) if row.product_id else None
        source = session.get(MarketplaceListing, row.source_listing_id)
        units = row.recommended_quantity
        candidates.append(
            AllocationCandidate(
                opportunity_id=row.id,
                title=(source.title if source else (product.title if product else row.id)),
                unit_cost=money(row.acquisition_cost or Decimal("0")),
                unit_profit=money(row.net_profit or Decimal("0")),
                roi=row.roi,
                risk_level=RiskLevel(row.risk_level),
                score=row.score or Decimal("0"),
                units_available=units,
                brand=(product.brand if product else None),
                category=(product.category if product else None),
                recommendation=row.recommendation,
            )
        )

    constraints = CapitalConstraints(
        available_capital=money(payload.available_capital),
        max_position_size=money(payload.max_position_size)
        if payload.max_position_size is not None
        else None,
        max_position_pct=payload.max_position_pct,
        min_roi=payload.min_roi,
        max_risk_level=RiskLevel(payload.max_risk_level),
        min_score=payload.min_score,
        max_positions=payload.max_positions,
        max_brand_pct=payload.max_brand_pct,
        max_category_pct=payload.max_category_pct,
        min_position_size=money(payload.min_position_size),
        allowed_recommendations=tuple(payload.allowed_recommendations),
    )
    plan = allocate_capital(candidates, constraints)
    payload_dict = plan.as_dict()

    if payload.save:
        row = CapitalPlan(
            organization_id=auth.organization_id,
            name=payload.name,
            available_capital=constraints.available_capital,
            constraints=payload_dict["constraints"],
            allocations=payload_dict["allocations"],
            excluded=payload_dict["excluded"],
            allocated_capital=plan.allocated_capital,
            unallocated_capital=plan.unallocated_capital,
            expected_profit=plan.expected_profit,
            expected_roi=plan.expected_roi,
            model_version=plan.model_version,
            is_simulation=True,
        )
        session.add(row)
        session.commit()
        payload_dict["plan_id"] = row.id

    payload_dict["candidates_considered"] = len(candidates)
    return payload_dict


@router.get("/capital/plans")
def list_plans(session: DbSession, auth: Auth, limit: int = 25) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(CapitalPlan)
        .where(CapitalPlan.organization_id == auth.organization_id)
        .order_by(CapitalPlan.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": row.id,
            "name": row.name,
            "available_capital": str(row.available_capital),
            "allocated_capital": str(row.allocated_capital),
            "unallocated_capital": str(row.unallocated_capital),
            "expected_profit": str(row.expected_profit),
            "expected_roi": str(row.expected_roi) if row.expected_roi is not None else None,
            "position_count": len(row.allocations or []),
            "model_version": row.model_version,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
