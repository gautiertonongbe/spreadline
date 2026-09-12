"""Purchases, sales and outcomes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import Auth, DbSession
from app.domains.portfolio import service as portfolio
from app.schemas.opportunity import (
    OutcomeResponse,
    PurchaseRequest,
    PurchaseResponse,
    SaleRequest,
    SaleResponse,
)

router = APIRouter(tags=["portfolio"])


@router.post("/purchases", response_model=PurchaseResponse, status_code=201)
def create_purchase(payload: PurchaseRequest, session: DbSession, auth: Auth) -> Any:
    purchase = portfolio.record_purchase(
        session,
        auth,
        portfolio.PurchaseInput(
            product_id=payload.product_id,
            quantity=payload.quantity,
            unit_price=payload.unit_price,
            purchased_at=payload.purchased_at,
            opportunity_id=payload.opportunity_id,
            source_marketplace=payload.source_marketplace,
            supplier=payload.supplier,
            sourcing_channel=payload.sourcing_channel,
            order_reference=payload.order_reference,
            shipping_cost=payload.shipping_cost,
            tax=payload.tax,
            other_costs=payload.other_costs,
            currency=payload.currency,
            expected_delivery=payload.expected_delivery,
            notes=payload.notes,
        ),
    )
    session.commit()
    return purchase


@router.get("/purchases", response_model=list[PurchaseResponse])
def list_purchases(
    session: DbSession,
    auth: Auth,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return portfolio.list_purchases(session, auth, limit=limit, offset=offset)


@router.post("/sales", response_model=SaleResponse, status_code=201)
def create_sale(payload: SaleRequest, session: DbSession, auth: Auth) -> Any:
    sale = portfolio.record_sale(
        session,
        auth,
        portfolio.SaleInput(
            product_id=payload.product_id,
            marketplace=payload.marketplace,
            quantity=payload.quantity,
            unit_price=payload.unit_price,
            sold_at=payload.sold_at,
            purchase_id=payload.purchase_id,
            opportunity_id=payload.opportunity_id,
            order_reference=payload.order_reference,
            marketplace_fees=payload.marketplace_fees,
            fulfillment_fees=payload.fulfillment_fees,
            storage_fees=payload.storage_fees,
            shipping_cost=payload.shipping_cost,
            refunds=payload.refunds,
            other_costs=payload.other_costs,
            currency=payload.currency,
            notes=payload.notes,
        ),
    )
    session.commit()
    return sale


@router.get("/sales", response_model=list[SaleResponse])
def list_sales(
    session: DbSession,
    auth: Auth,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return portfolio.list_sales(session, auth, limit=limit, offset=offset)


@router.get("/outcomes", response_model=list[OutcomeResponse])
def list_outcomes(
    session: DbSession,
    auth: Auth,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return portfolio.list_outcomes(session, auth, limit=limit, offset=offset)


@router.get("/portfolio/summary")
def summary(session: DbSession, auth: Auth) -> dict[str, Any]:
    return portfolio.portfolio_summary(session, auth)
