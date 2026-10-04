"""The boundary between deciding and buying, and the way back across it.

Spreadline decides what, where, how many and at what maximum price. It does not
place retail orders, and nothing here does either: no checkout, no purchasing
bot, no stored card, no browser driving a shop. That is a product decision, not
a missing feature, and it is why this module deals in *instructions* and
*records* rather than in orders.

What was missing was not the outgoing half. It was the return.

An authorised decision opened a position at the figures that were **authorised**
and nothing ever corrected it with the figures that were **paid**. Buy three at
up to $35.09, actually find two at $37.20, and the platform went on measuring
its own accuracy against a purchase that never happened. Every realised return,
every scorecard number and every holding period downstream inherited that
fiction.

So an authorisation now produces an instruction that can be closed out, and
closing it out does three things:

**It records what actually happened**, including nothing happening, which is an
outcome and not an absence of one.

**It names the departures rather than absorbing them.** Paying above the
ceiling is the interesting case: the ceiling is the condition the authorisation
rested on, so an order placed above it is recorded as a departure with a code,
not quietly averaged into the cost basis. The purchase is still recorded --
refusing to write down something that already happened would only make the
books wrong -- but the record says the decision and the execution disagreed.

**It corrects the position to reality.** Quantity, unit cost and capital are
rewritten to what was bought at what was paid. This is the point of the whole
module: measurement downstream is only worth having if it measures the trade
that happened.

Instructions expire. "Buy at up to $35.09" is true while the price that
justified it holds and not indefinitely, and an expired instruction is refused
rather than executed late.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import ensure_utc, iso_utc, utcnow
from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.money import display_currency, money
from app.core.security import AuthContext
from app.domains.autonomy import policy as policy_module
from app.models.autonomy import AutonomyDecision, CapitalPosition, ExecutionInstruction
from app.models.catalog import MarketplaceListing
from app.models.enums import AutonomyEventType, PositionStatus
from app.models.opportunity import Opportunity
from app.models.portfolio import Purchase


class InstructionStatus(StrEnum):
    PENDING = "pending"
    EXECUTED = "executed"
    PARTIAL = "partial"
    NOT_EXECUTED = "not_executed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


#: Stable codes for the ways an execution can depart from its authorisation.
#: Counted and asserted on, so they are part of the contract rather than prose.
PAID_ABOVE_CEILING = "PAID_ABOVE_CEILING"
OVER_QUANTITY = "OVER_QUANTITY"
UNDER_QUANTITY = "UNDER_QUANTITY"
EXPIRED_WHEN_EXECUTED = "EXPIRED_WHEN_EXECUTED"


@dataclass
class ExecutionInput:
    """What a person reports after acting on an instruction."""

    quantity: int
    unit_price: Decimal | None = None
    order_reference: str | None = None
    shipping_cost: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    other_costs: Decimal = Decimal("0")
    notes: str | None = None


def view(row: ExecutionInstruction) -> dict[str, Any]:
    return {
        "id": row.id,
        "status": row.status,
        "executor": row.executor,
        "title": row.title,
        "decision_id": row.decision_id,
        "position_id": row.position_id,
        "opportunity_id": row.opportunity_id,
        "purchase_id": row.purchase_id,
        "source_marketplace": row.source_marketplace,
        "source_external_id": row.source_external_id,
        "source_url": row.source_url,
        "quantity_authorized": row.quantity_authorized,
        "max_unit_price": str(row.max_unit_price),
        "capital_authorized": str(row.capital_authorized),
        "expires_at": iso_utc(row.expires_at),
        "is_expired": is_expired(row),
        "quantity_executed": row.quantity_executed,
        "unit_price_paid": (
            None if row.unit_price_paid is None else str(row.unit_price_paid)
        ),
        "capital_spent": None if row.capital_spent is None else str(row.capital_spent),
        "executed_at": iso_utc(row.executed_at),
        "executed_by": row.executed_by,
        "order_reference": row.order_reference,
        "variances": row.variances,
        "notes": row.notes,
        "created_at": iso_utc(row.created_at),
        "summary": summary(row),
    }


def is_expired(row: ExecutionInstruction) -> bool:
    if row.expires_at is None:
        return False
    if row.status != InstructionStatus.PENDING.value:
        return False
    return ensure_utc(row.expires_at) <= utcnow()


def summary(row: ExecutionInstruction) -> str:
    """One sentence a person can act on, or read the result of."""
    if row.status == InstructionStatus.PENDING.value:
        if is_expired(row):
            return (
                f"Expired before it was acted on. It authorised {row.quantity_authorized} "
                f"unit(s) at up to {display_currency(row.max_unit_price)}; the price that "
                "justified that is no longer current, so it needs deciding again."
            )
        return (
            f"Buy up to {row.quantity_authorized} unit(s) of {row.title or 'this product'} "
            f"at no more than {display_currency(row.max_unit_price)} each, on "
            f"{row.source_marketplace or 'the source marketplace'}. Placing the order is "
            "a human action."
        )
    if row.status == InstructionStatus.NOT_EXECUTED.value:
        return f"Not bought. {row.notes or 'No reason recorded.'}"
    if row.status == InstructionStatus.CANCELLED.value:
        return f"Cancelled before it was acted on. {row.notes or ''}".strip()
    if row.status == InstructionStatus.EXPIRED.value:
        return "Expired without being acted on."

    paid = display_currency(row.unit_price_paid or Decimal("0"))
    parts = [
        f"Bought {row.quantity_executed} of {row.quantity_authorized} authorised at "
        f"{paid} each."
    ]
    if PAID_ABOVE_CEILING in row.variances:
        parts.append(
            f"Above the {display_currency(row.max_unit_price)} ceiling the decision rested "
            "on, so the position is not the one that was underwritten."
        )
    if UNDER_QUANTITY in row.variances:
        parts.append("Fewer units than authorised, which is ordinary: stock runs out.")
    if OVER_QUANTITY in row.variances:
        parts.append("More units than authorised, which the limits did not approve.")
    return " ".join(parts)


# ------------------------------------------------------------------ issue


def issue(
    session: Session,
    auth: AuthContext,
    *,
    decision: AutonomyDecision,
    position: CapitalPosition | None,
    opportunity: Opportunity | None,
    executor: str = "human",
) -> ExecutionInstruction:
    """Turn an authorised decision into something a person can act on.

    Carries the ceiling and an expiry, because an authorisation to pay up to a
    price is only as good as the price that justified it.
    """
    listing = None
    if opportunity is not None and opportunity.source_listing_id:
        listing = session.get(MarketplaceListing, opportunity.source_listing_id)

    row = ExecutionInstruction(
        organization_id=auth.organization_id,
        decision_id=decision.id,
        position_id=None if position is None else position.id,
        opportunity_id=None if opportunity is None else opportunity.id,
        product_id=None if opportunity is None else opportunity.product_id,
        status=InstructionStatus.PENDING.value,
        executor=executor,
        title=None if position is None else position.title,
        source_marketplace=None if opportunity is None else opportunity.source_marketplace,
        source_external_id=None if listing is None else listing.external_id,
        source_url=None if listing is None else listing.url,
        quantity_authorized=decision.quantity or 0,
        max_unit_price=money(decision.max_unit_price or Decimal("0")),
        capital_authorized=money(decision.capital_committed or Decimal("0")),
        expires_at=utcnow() + timedelta(hours=settings.execution_instruction_ttl_hours),
    )
    session.add(row)
    session.flush()
    return row


def get(session: Session, auth: AuthContext, instruction_id: str) -> ExecutionInstruction:
    row = session.scalar(
        select(ExecutionInstruction).where(
            ExecutionInstruction.organization_id == auth.organization_id,
            ExecutionInstruction.id == instruction_id,
        )
    )
    if row is None:
        raise NotFoundError("No such execution instruction.")
    return row


def outstanding(session: Session, auth: AuthContext) -> list[ExecutionInstruction]:
    """Everything waiting on a person, newest first."""
    rows = session.scalars(
        select(ExecutionInstruction)
        .where(
            ExecutionInstruction.organization_id == auth.organization_id,
            ExecutionInstruction.status == InstructionStatus.PENDING.value,
        )
        .order_by(ExecutionInstruction.created_at.desc())
    )
    return list(rows)


def listing(
    session: Session,
    auth: AuthContext,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[ExecutionInstruction]:
    query = select(ExecutionInstruction).where(
        ExecutionInstruction.organization_id == auth.organization_id
    )
    if status:
        query = query.where(ExecutionInstruction.status == status)
    rows = session.scalars(
        query.order_by(ExecutionInstruction.created_at.desc()).limit(limit)
    )
    return list(rows)


# ------------------------------------------------------------------ close out


def record_execution(
    session: Session,
    auth: AuthContext,
    instruction_id: str,
    payload: ExecutionInput,
    *,
    actor: str = "system",
) -> ExecutionInstruction:
    """Record what was actually bought, and correct everything downstream.

    Refuses to reopen an instruction that has already been closed out: a second
    report against the same authorisation is a second purchase, and that needs
    its own decision rather than an edit to this one.
    """
    row = get(session, auth, instruction_id)
    if row.status != InstructionStatus.PENDING.value:
        raise ConflictError(
            f"This instruction is already {row.status}. Recording a second purchase "
            "against one authorisation would hide the second decision."
        )
    if payload.quantity < 0:
        raise ValidationError("Quantity cannot be negative.")

    expired = is_expired(row)
    variances: list[str] = []

    # --- nothing bought ---------------------------------------------------
    if payload.quantity == 0:
        row.status = (
            InstructionStatus.EXPIRED.value if expired else InstructionStatus.NOT_EXECUTED.value
        )
        row.executed_at = utcnow()
        row.executed_by = actor
        row.notes = payload.notes
        _release_position(session, row, reason="nothing was bought")
        _record_event(session, auth, row, actor)
        session.flush()
        return row

    if payload.unit_price is None:
        raise ValidationError("A unit price is required when anything was bought.")
    if payload.unit_price <= 0:
        raise ValidationError("A unit price must be above zero.")

    unit_price = money(payload.unit_price)
    if expired:
        # Recorded rather than refused: it already happened, and a system that
        # declines to write down a real purchase only makes the books wrong.
        variances.append(EXPIRED_WHEN_EXECUTED)
    if unit_price > row.max_unit_price:
        variances.append(PAID_ABOVE_CEILING)
    if payload.quantity > row.quantity_authorized:
        variances.append(OVER_QUANTITY)
    elif payload.quantity < row.quantity_authorized:
        variances.append(UNDER_QUANTITY)

    extras = money(payload.shipping_cost + payload.tax + payload.other_costs)
    spent = money(unit_price * payload.quantity + extras)

    row.status = (
        InstructionStatus.PARTIAL.value
        if payload.quantity < row.quantity_authorized
        else InstructionStatus.EXECUTED.value
    )
    row.quantity_executed = payload.quantity
    row.unit_price_paid = unit_price
    row.capital_spent = spent
    row.executed_at = utcnow()
    row.executed_by = actor
    row.order_reference = payload.order_reference
    row.variances = variances
    row.notes = payload.notes

    purchase = _record_purchase(session, auth, row, payload, unit_price, extras)
    if purchase is not None:
        row.purchase_id = purchase.id

    _correct_position(session, row, unit_price, payload.quantity, purchase)
    _record_event(session, auth, row, actor)
    session.flush()
    return row


def cancel(
    session: Session,
    auth: AuthContext,
    instruction_id: str,
    *,
    reason: str,
    actor: str = "system",
) -> ExecutionInstruction:
    """Withdraw an instruction nobody is going to act on."""
    row = get(session, auth, instruction_id)
    if row.status != InstructionStatus.PENDING.value:
        raise ConflictError(f"This instruction is already {row.status}.")
    row.status = InstructionStatus.CANCELLED.value
    row.notes = reason
    row.executed_at = utcnow()
    row.executed_by = actor
    _release_position(session, row, reason=f"instruction cancelled: {reason}")
    _record_event(session, auth, row, actor)
    session.flush()
    return row


def expire_overdue(session: Session, auth: AuthContext) -> int:
    """Mark instructions that ran out of time. Safe to run repeatedly."""
    rows = [row for row in outstanding(session, auth) if is_expired(row)]
    for row in rows:
        row.status = InstructionStatus.EXPIRED.value
        _release_position(session, row, reason="instruction expired")
    session.flush()
    return len(rows)


# ------------------------------------------------------------------ effects


def _record_purchase(
    session: Session,
    auth: AuthContext,
    row: ExecutionInstruction,
    payload: ExecutionInput,
    unit_price: Decimal,
    extras: Decimal,
) -> Purchase | None:
    """Write the purchase into the books, where there is a product to write."""
    if not row.product_id:
        return None
    purchase = Purchase(
        organization_id=auth.organization_id,
        opportunity_id=row.opportunity_id,
        product_id=row.product_id,
        source_marketplace=row.source_marketplace,
        sourcing_channel="online_arbitrage",
        order_reference=payload.order_reference,
        quantity=payload.quantity,
        unit_price=unit_price,
        shipping_cost=money(payload.shipping_cost),
        tax=money(payload.tax),
        other_costs=money(payload.other_costs),
        total_cost=money(unit_price * payload.quantity + extras),
        purchased_at=utcnow(),
        notes=payload.notes,
    )
    session.add(purchase)
    session.flush()
    return purchase


def _correct_position(
    session: Session,
    row: ExecutionInstruction,
    unit_price: Decimal,
    quantity: int,
    purchase: Purchase | None,
) -> None:
    """Rewrite the position to what was actually bought at what was paid.

    The reason this module exists. A position opened at the authorised figures
    and never corrected makes every downstream measurement -- realised return,
    prediction accuracy, holding period -- a comparison against a purchase that
    did not happen.
    """
    if not row.position_id:
        return
    position = session.get(CapitalPosition, row.position_id)
    if position is None:
        return

    position.quantity = quantity
    position.unit_cost = unit_price
    position.capital_invested = money(unit_price * quantity)
    if position.expected_unit_profit is not None:
        position.expected_profit = money(position.expected_unit_profit * quantity)
    if purchase is not None:
        position.purchase_id = purchase.id
    session.flush()


def _release_position(session: Session, row: ExecutionInstruction, *, reason: str) -> None:
    """Let go of capital that was never actually committed.

    A position held open against an order nobody placed is capital the system
    believes is working and is not, and every limit downstream is computed from
    it. Cancelled rather than deleted: the decision happened and the record of
    it stays.
    """
    if not row.position_id:
        return
    position = session.get(CapitalPosition, row.position_id)
    if position is None or position.status != PositionStatus.OPEN.value:
        return
    position.status = PositionStatus.CANCELLED.value
    position.closed_at = utcnow()
    position.notes = f"{position.notes + ' ' if position.notes else ''}Cancelled: {reason}."
    session.flush()


def _record_event(
    session: Session, auth: AuthContext, row: ExecutionInstruction, actor: str
) -> None:
    policy_module.record_event(
        session,
        auth,
        AutonomyEventType.EXECUTION_RECORDED,
        message=summary(row),
        actor=actor,
        opportunity_id=row.opportunity_id,
        decision_id=row.decision_id,
        payload={
            "instruction_id": row.id,
            "status": row.status,
            "quantity_executed": row.quantity_executed,
            "variances": row.variances,
        },
    )


# ------------------------------------------------------------------ report


def report(session: Session, auth: AuthContext) -> dict[str, Any]:
    """Everything outstanding, and how faithfully past ones were carried out."""
    rows = listing(session, auth, limit=200)
    pending = [row for row in rows if row.status == InstructionStatus.PENDING.value]
    closed = [
        row
        for row in rows
        if row.status in {InstructionStatus.EXECUTED.value, InstructionStatus.PARTIAL.value}
    ]
    with_variance = [row for row in closed if row.variances]

    by_variance: dict[str, int] = {}
    for row in closed:
        for code in row.variances:
            by_variance[code] = by_variance.get(code, 0) + 1

    if not rows:
        fidelity = (
            "Nothing has been authorised for execution yet. An authorised decision "
            "produces an instruction here, and closing it out is what tells the system "
            "what was really paid."
        )
    elif not closed:
        fidelity = (
            f"{len(pending)} instruction(s) waiting on a person. Nothing has been closed "
            "out yet, so no realised figure anywhere in the product has been checked "
            "against a real purchase."
        )
    else:
        faithful = len(closed) - len(with_variance)
        fidelity = (
            f"{faithful} of {len(closed)} closed instruction(s) were carried out exactly "
            "as authorised."
        )
        if with_variance:
            fidelity += (
                f" {len(with_variance)} departed from the authorisation; the codes say how."
            )

    return {
        "summary": fidelity,
        "outstanding": [view(row) for row in pending],
        "recent": [view(row) for row in rows[:50]],
        "counts": {
            "pending": len(pending),
            "closed": len(closed),
            "with_variance": len(with_variance),
            "by_variance": by_variance,
        },
        "note": (
            "Spreadline decides what to buy, how many and at what maximum price. It does "
            "not place retail orders: there is no checkout, no purchasing bot and no "
            "stored payment method here, and an authorised supplier integration would "
            "read exactly these fields rather than change this layer."
        ),
    }
