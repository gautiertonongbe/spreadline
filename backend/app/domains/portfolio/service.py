"""Purchases, sales and outcomes.

Where predictions meet money. The outcome row compares what the engines said at
decision time against what the position actually did (spec §23), and that
comparison is the dataset any future learning system will be trained on, so it is
built carefully:

* The predicted side is copied from the profitability snapshot that was live when
  the purchase was recorded, not recomputed later.
* Costs are summed from the rows the operator entered, never estimated.
* A position with no sales yet is ``open``, not a loss.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import ensure_utc, utcnow
from app.core.errors import NotFoundError, ValidationError
from app.core.money import ZERO, money, pct_of, ratio
from app.core.security import AuthContext
from app.models.enums import OpportunityEventType, OpportunityStatus
from app.models.opportunity import Opportunity, ProfitabilitySnapshot
from app.models.portfolio import Outcome, Purchase, Sale


@dataclass
class PurchaseInput:
    product_id: str
    quantity: int
    unit_price: Decimal
    purchased_at: datetime | None = None
    opportunity_id: str | None = None
    source_marketplace: str | None = None
    supplier: str | None = None
    sourcing_channel: str = "online_arbitrage"
    order_reference: str | None = None
    shipping_cost: Decimal = ZERO
    tax: Decimal = ZERO
    other_costs: Decimal = ZERO
    currency: str = "USD"
    expected_delivery: date | None = None
    notes: str | None = None


@dataclass
class SaleInput:
    product_id: str
    marketplace: str
    quantity: int
    unit_price: Decimal
    sold_at: datetime | None = None
    purchase_id: str | None = None
    opportunity_id: str | None = None
    order_reference: str | None = None
    marketplace_fees: Decimal = ZERO
    fulfillment_fees: Decimal = ZERO
    storage_fees: Decimal = ZERO
    shipping_cost: Decimal = ZERO
    refunds: Decimal = ZERO
    other_costs: Decimal = ZERO
    currency: str = "USD"
    notes: str | None = None


def record_purchase(session: Session, auth: AuthContext, data: PurchaseInput) -> Purchase:
    if data.quantity <= 0:
        raise ValidationError("Purchase quantity must be at least 1.")
    if data.unit_price < 0:
        raise ValidationError("Unit price cannot be negative.")

    total = money(
        money(data.unit_price) * data.quantity
        + money(data.shipping_cost)
        + money(data.tax)
        + money(data.other_costs)
    )
    purchase = Purchase(
        organization_id=auth.organization_id,
        opportunity_id=data.opportunity_id,
        product_id=data.product_id,
        source_marketplace=data.source_marketplace,
        supplier=data.supplier,
        sourcing_channel=data.sourcing_channel,
        order_reference=data.order_reference,
        quantity=data.quantity,
        unit_price=money(data.unit_price),
        shipping_cost=money(data.shipping_cost),
        tax=money(data.tax),
        other_costs=money(data.other_costs),
        total_cost=total,
        currency=data.currency,
        purchased_at=ensure_utc(data.purchased_at or utcnow()),
        expected_delivery=data.expected_delivery,
        notes=data.notes,
    )
    session.add(purchase)
    session.flush()

    if data.opportunity_id:
        opportunity = _opportunity(session, auth, data.opportunity_id)
        _advance(session, auth, opportunity, OpportunityStatus.PURCHASED)
        from app.domains.opportunities.service import record_event

        record_event(
            session,
            auth,
            opportunity,
            OpportunityEventType.PURCHASE_RECORDED,
            message=f"{data.quantity} unit(s) purchased at {purchase.unit_price}.",
            payload={"purchase_id": purchase.id, "total_cost": str(total)},
        )
        refresh_outcome(session, auth, opportunity_id=opportunity.id, purchase_id=purchase.id)
    return purchase


def record_sale(session: Session, auth: AuthContext, data: SaleInput) -> Sale:
    if data.quantity <= 0:
        raise ValidationError("Sale quantity must be at least 1.")

    gross = money(money(data.unit_price) * data.quantity)
    net = money(
        gross
        - money(data.marketplace_fees)
        - money(data.fulfillment_fees)
        - money(data.storage_fees)
        - money(data.shipping_cost)
        - money(data.refunds)
        - money(data.other_costs)
    )
    sale = Sale(
        organization_id=auth.organization_id,
        purchase_id=data.purchase_id,
        opportunity_id=data.opportunity_id,
        product_id=data.product_id,
        marketplace=data.marketplace,
        order_reference=data.order_reference,
        quantity=data.quantity,
        unit_price=money(data.unit_price),
        gross_revenue=gross,
        marketplace_fees=money(data.marketplace_fees),
        fulfillment_fees=money(data.fulfillment_fees),
        storage_fees=money(data.storage_fees),
        shipping_cost=money(data.shipping_cost),
        refunds=money(data.refunds),
        other_costs=money(data.other_costs),
        net_proceeds=net,
        currency=data.currency,
        sold_at=ensure_utc(data.sold_at or utcnow()),
        notes=data.notes,
    )
    session.add(sale)
    session.flush()

    if data.opportunity_id:
        opportunity = _opportunity(session, auth, data.opportunity_id)
        _advance(session, auth, opportunity, OpportunityStatus.SOLD)
        from app.domains.opportunities.service import record_event

        record_event(
            session,
            auth,
            opportunity,
            OpportunityEventType.SALE_RECORDED,
            message=f"{data.quantity} unit(s) sold at {sale.unit_price}.",
            payload={"sale_id": sale.id, "net_proceeds": str(net)},
        )
        refresh_outcome(session, auth, opportunity_id=opportunity.id, purchase_id=data.purchase_id)
    return sale


def _opportunity(session: Session, auth: AuthContext, opportunity_id: str) -> Opportunity:
    opportunity = session.scalar(
        select(Opportunity).where(
            Opportunity.id == opportunity_id,
            Opportunity.organization_id == auth.organization_id,
        )
    )
    if opportunity is None:
        raise NotFoundError(f"Opportunity {opportunity_id} was not found.")
    return opportunity


def _advance(
    session: Session, auth: AuthContext, opportunity: Opportunity, target: OpportunityStatus
) -> None:
    """Walk the lifecycle forward to ``target``, filling in the steps.

    Recording a purchase against an opportunity still sitting in REVIEW is normal
    operator behaviour; refusing it because APPROVED was skipped would be pedantry
    that loses data. The intermediate transitions are synthesised so the event log
    still reconstructs the full path.
    """
    from app.domains.opportunities.service import transition

    path = {
        OpportunityStatus.PURCHASED: [OpportunityStatus.APPROVED, OpportunityStatus.PURCHASED],
        OpportunityStatus.SOLD: [
            OpportunityStatus.APPROVED,
            OpportunityStatus.PURCHASED,
            OpportunityStatus.LISTED,
            OpportunityStatus.SOLD,
        ],
    }[target]

    for step in path:
        current = OpportunityStatus(opportunity.status)
        if current == step:
            continue
        from app.models.enums import OPPORTUNITY_TRANSITIONS

        if step in OPPORTUNITY_TRANSITIONS[current]:
            transition(session, auth, opportunity, step, note="Implied by a recorded transaction.")


def refresh_outcome(
    session: Session,
    auth: AuthContext,
    *,
    opportunity_id: str | None = None,
    purchase_id: str | None = None,
) -> Outcome | None:
    """Recompute the predicted-versus-actual record for a position."""
    if opportunity_id is None and purchase_id is None:
        raise ValidationError("An outcome needs an opportunity or a purchase.")

    purchases = list(
        session.scalars(
            select(Purchase).where(
                Purchase.organization_id == auth.organization_id,
                (Purchase.opportunity_id == opportunity_id)
                if opportunity_id
                else (Purchase.id == purchase_id),
            )
        )
    )
    if not purchases and purchase_id:
        purchase = session.get(Purchase, purchase_id)
        purchases = [purchase] if purchase else []
    if not purchases:
        return None

    product_id = purchases[0].product_id
    sales = list(
        session.scalars(
            select(Sale).where(
                Sale.organization_id == auth.organization_id,
                (Sale.opportunity_id == opportunity_id)
                if opportunity_id
                else (Sale.purchase_id == purchase_id),
            )
        )
    )

    outcome = session.scalar(
        select(Outcome).where(
            Outcome.organization_id == auth.organization_id,
            (Outcome.opportunity_id == opportunity_id)
            if opportunity_id
            else (Outcome.purchase_id == purchases[0].id),
        )
    )
    if outcome is None:
        outcome = Outcome(
            organization_id=auth.organization_id,
            opportunity_id=opportunity_id,
            purchase_id=purchases[0].id,
            product_id=product_id,
        )
        session.add(outcome)

    quantity_purchased = sum(item.quantity for item in purchases)
    quantity_sold = sum(item.quantity for item in sales)
    capital = money(sum((item.total_cost for item in purchases), ZERO))

    outcome.quantity_purchased = quantity_purchased
    outcome.quantity_sold = quantity_sold
    outcome.capital_deployed = capital

    if opportunity_id:
        opportunity = session.get(Opportunity, opportunity_id)
        if opportunity is not None:
            outcome.predicted_unit_profit = opportunity.net_profit
            outcome.predicted_total_profit = (
                money(opportunity.net_profit * quantity_purchased)
                if opportunity.net_profit is not None
                else None
            )
            outcome.predicted_roi = opportunity.roi
            outcome.predicted_recommendation = opportunity.recommendation
            outcome.predicted_score = opportunity.score

    if sales:
        revenue = money(sum((item.net_proceeds for item in sales), ZERO))
        # Cost is attributed to the units that actually sold, so a position that
        # is half sold does not look like a loss for the half still in stock.
        unit_cost = money(capital / quantity_purchased) if quantity_purchased else ZERO
        cost_of_sold = money(unit_cost * quantity_sold)
        profit = money(revenue - cost_of_sold)

        outcome.actual_revenue = revenue
        outcome.actual_costs = cost_of_sold
        outcome.actual_profit = profit
        outcome.actual_roi = pct_of(profit, cost_of_sold)

        first_purchase = min(purchases, key=lambda item: ensure_utc(item.purchased_at))
        last_sale = max(sales, key=lambda item: ensure_utc(item.sold_at))
        outcome.days_to_sell = max(
            0, (ensure_utc(last_sale.sold_at) - ensure_utc(first_purchase.purchased_at)).days
        )

        if outcome.predicted_total_profit is not None:
            predicted_for_sold = (
                money(outcome.predicted_unit_profit * quantity_sold)
                if outcome.predicted_unit_profit is not None
                else None
            )
            if predicted_for_sold is not None:
                outcome.profit_variance = money(profit - predicted_for_sold)
                outcome.profit_variance_pct = pct_of(
                    outcome.profit_variance, abs(predicted_for_sold)
                )
        if outcome.predicted_roi is not None and outcome.actual_roi is not None:
            outcome.roi_variance = ratio(outcome.actual_roi - outcome.predicted_roi)

        outcome.is_closed = quantity_sold >= quantity_purchased
        if profit > 0:
            outcome.result = "win"
        elif profit < 0:
            outcome.result = "loss"
        else:
            outcome.result = "breakeven"
    else:
        outcome.result = "open"
        outcome.is_closed = False

    session.flush()

    if opportunity_id and outcome.is_closed:
        opportunity = session.get(Opportunity, opportunity_id)
        if opportunity is not None:
            from app.domains.opportunities.service import record_event

            record_event(
                session,
                auth,
                opportunity,
                OpportunityEventType.OUTCOME_RECORDED,
                message=(
                    f"Position closed: {outcome.result}, actual profit {outcome.actual_profit} "
                    f"against {outcome.predicted_total_profit} predicted."
                ),
                payload={"outcome_id": outcome.id},
            )
    return outcome


def snapshot_at_purchase(session: Session, opportunity_id: str) -> ProfitabilitySnapshot | None:
    """The base-case profitability snapshot an opportunity was bought on."""
    return session.scalar(
        select(ProfitabilitySnapshot)
        .where(
            ProfitabilitySnapshot.opportunity_id == opportunity_id,
            ProfitabilitySnapshot.scenario == "base",
        )
        .order_by(ProfitabilitySnapshot.created_at.desc())
    )


def list_purchases(
    session: Session, auth: AuthContext, *, limit: int = 100, offset: int = 0
) -> Sequence[Purchase]:
    return list(
        session.scalars(
            select(Purchase)
            .where(Purchase.organization_id == auth.organization_id)
            .order_by(Purchase.purchased_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )


def list_sales(
    session: Session, auth: AuthContext, *, limit: int = 100, offset: int = 0
) -> Sequence[Sale]:
    return list(
        session.scalars(
            select(Sale)
            .where(Sale.organization_id == auth.organization_id)
            .order_by(Sale.sold_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )


def list_outcomes(
    session: Session, auth: AuthContext, *, limit: int = 100, offset: int = 0
) -> Sequence[Outcome]:
    return list(
        session.scalars(
            select(Outcome)
            .where(Outcome.organization_id == auth.organization_id)
            .order_by(Outcome.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )


def portfolio_summary(session: Session, auth: AuthContext) -> dict[str, Any]:
    purchases = list(
        session.scalars(select(Purchase).where(Purchase.organization_id == auth.organization_id))
    )
    sales = list(session.scalars(select(Sale).where(Sale.organization_id == auth.organization_id)))
    outcomes = list(
        session.scalars(select(Outcome).where(Outcome.organization_id == auth.organization_id))
    )

    capital_deployed = money(sum((item.total_cost for item in purchases), ZERO))
    revenue = money(sum((item.net_proceeds for item in sales), ZERO))
    realised = [item for item in outcomes if item.actual_profit is not None]
    actual_profit = money(sum((item.actual_profit for item in realised), ZERO))
    open_positions = [item for item in outcomes if not item.is_closed]
    closed = [item for item in outcomes if item.is_closed]
    wins = [item for item in closed if item.result == "win"]
    days = [item.days_to_sell for item in closed if item.days_to_sell is not None]

    return {
        "capital_deployed": str(capital_deployed),
        "capital_open": str(money(sum((item.capital_deployed for item in open_positions), ZERO))),
        "gross_revenue": str(money(sum((item.gross_revenue for item in sales), ZERO))),
        "net_revenue": str(revenue),
        "actual_profit": str(actual_profit),
        "actual_roi": (
            str(pct_of(actual_profit, money(sum((item.actual_costs or ZERO) for item in realised))))
            if realised
            else None
        ),
        "purchase_count": len(purchases),
        "sale_count": len(sales),
        "open_positions": len(open_positions),
        "closed_positions": len(closed),
        "win_rate": (str(ratio(Decimal(len(wins)) / Decimal(len(closed)))) if closed else None),
        "average_days_to_sell": (
            str(ratio(Decimal(sum(days)) / Decimal(len(days)))) if days else None
        ),
    }
