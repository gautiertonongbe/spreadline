"""Inventory held as a capital position.

A purchased product is not a row in a stock list, it is capital committed at a
price with an expected return and a holding period. Treating it that way is what
makes the portfolio measurable, and what makes shadow mode meaningful: a shadow
position is tracked against the same real market data and measured the same way,
and the only difference is whether money moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import ensure_utc, iso_utc, utcnow
from app.core.money import money, pct_of
from app.core.security import AuthContext
from app.models.autonomy import CapitalPosition
from app.models.enums import ExecutionMode, PositionStatus


@dataclass
class PositionView:
    row: CapitalPosition
    #: Days since the position was opened. Inventory age is a policy limit, so
    #: it is computed once here rather than in each caller.
    days_held: int

    @property
    def unrealized_value(self) -> Decimal:
        """What the unsold units are expected to be worth, at the expected profit.

        Deliberately *expected*, not marked to market. Marking inventory to the
        current market price every day would produce an equity curve that looks
        precise and is built on a price nobody has been offered.
        """
        remaining = max(0, self.row.quantity - self.row.quantity_sold)
        if remaining == 0 or self.row.expected_unit_profit is None:
            return money(remaining * self.row.unit_cost)
        return money(remaining * (self.row.unit_cost + self.row.expected_unit_profit))

    def as_dict(self) -> dict[str, Any]:
        row = self.row
        return {
            "id": row.id,
            "opportunity_id": row.opportunity_id,
            "decision_id": row.decision_id,
            "product_id": row.product_id,
            "purchase_id": row.purchase_id,
            "execution_mode": row.execution_mode,
            "status": row.status,
            "title": row.title,
            "brand": row.brand,
            "category": row.category,
            "source_marketplace": row.source_marketplace,
            "target_marketplace": row.target_marketplace,
            "quantity": row.quantity,
            "quantity_sold": row.quantity_sold,
            "unit_cost": str(row.unit_cost),
            "capital_invested": str(row.capital_invested),
            "expected_profit": None if row.expected_profit is None else str(row.expected_profit),
            "expected_roi": None if row.expected_roi is None else str(row.expected_roi),
            "realized_profit": str(row.realized_profit),
            "realized_proceeds": str(row.realized_proceeds),
            "unrealized_value": str(self.unrealized_value),
            "risk_level": row.risk_level,
            "days_held": self.days_held,
            "opened_at": iso_utc(row.opened_at),
            "closed_at": iso_utc(row.closed_at),
        }


def view(row: CapitalPosition, *, now: datetime | None = None) -> PositionView:
    end = ensure_utc(row.closed_at) if row.closed_at else (now or utcnow())
    return PositionView(row=row, days_held=max(0, (end - ensure_utc(row.opened_at)).days))


def open_position(
    session: Session,
    auth: AuthContext,
    *,
    execution_mode: ExecutionMode,
    quantity: int,
    unit_cost: Decimal,
    opportunity_id: str | None = None,
    decision_id: str | None = None,
    product_id: str | None = None,
    purchase_id: str | None = None,
    title: str | None = None,
    brand: str | None = None,
    category: str | None = None,
    source_marketplace: str | None = None,
    target_marketplace: str | None = None,
    expected_unit_profit: Decimal | None = None,
    expected_roi: Decimal | None = None,
    risk_level: str | None = None,
) -> CapitalPosition:
    """Record capital committed to one product.

    A shadow position carries no ``purchase_id``, which is how the two are told
    apart without trusting a mode string alone.
    """
    unit_cost = money(unit_cost)
    row = CapitalPosition(
        organization_id=auth.organization_id,
        opportunity_id=opportunity_id,
        decision_id=decision_id,
        product_id=product_id,
        purchase_id=purchase_id if execution_mode is ExecutionMode.LIVE else None,
        execution_mode=execution_mode.value,
        status=PositionStatus.OPEN.value,
        title=title,
        brand=brand,
        category=category,
        source_marketplace=source_marketplace,
        target_marketplace=target_marketplace,
        quantity=quantity,
        unit_cost=unit_cost,
        capital_invested=money(unit_cost * quantity),
        expected_unit_profit=(
            None if expected_unit_profit is None else money(expected_unit_profit)
        ),
        expected_profit=(
            None if expected_unit_profit is None else money(expected_unit_profit * quantity)
        ),
        expected_roi=expected_roi,
        risk_level=risk_level,
        opened_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def record_sale(
    session: Session,
    auth: AuthContext,
    position_id: str,
    *,
    quantity: int,
    unit_proceeds: Decimal,
) -> CapitalPosition | None:
    """Record units sold out of a position, closing it when it is empty.

    ``unit_proceeds`` is net of selling fees: what actually arrived per unit.
    """
    row = session.scalar(
        select(CapitalPosition).where(
            CapitalPosition.organization_id == auth.organization_id,
            CapitalPosition.id == position_id,
        )
    )
    if row is None:
        return None

    sold = min(quantity, row.quantity - row.quantity_sold)
    proceeds = money(unit_proceeds * sold)
    row.quantity_sold += sold
    row.realized_proceeds = money(row.realized_proceeds + proceeds)
    row.realized_profit = money(row.realized_proceeds - (row.unit_cost * row.quantity_sold))
    if row.quantity_sold >= row.quantity:
        row.status = PositionStatus.CLOSED.value
        row.closed_at = utcnow()
    session.flush()
    return row


def list_positions(
    session: Session,
    auth: AuthContext,
    *,
    execution_mode: ExecutionMode | None = None,
    status: PositionStatus | None = None,
) -> list[PositionView]:
    query = select(CapitalPosition).where(
        CapitalPosition.organization_id == auth.organization_id
    )
    if execution_mode is not None:
        query = query.where(CapitalPosition.execution_mode == execution_mode.value)
    if status is not None:
        query = query.where(CapitalPosition.status == status.value)
    query = query.order_by(CapitalPosition.opened_at.desc())
    return [view(row) for row in session.scalars(query)]


def deployed_capital(
    session: Session, auth: AuthContext, *, execution_mode: ExecutionMode | None = None
) -> Decimal:
    """Capital currently committed to open positions.

    This is the figure the capital limit is checked against, so it counts open
    positions only: capital returned by a closed position is available again.
    """
    return money(
        sum(
            (
                item.row.capital_invested
                for item in list_positions(
                    session, auth, execution_mode=execution_mode, status=PositionStatus.OPEN
                )
            ),
            Decimal("0"),
        )
    )


def portfolio(
    session: Session, auth: AuthContext, *, execution_mode: ExecutionMode | None = None
) -> dict[str, Any]:
    """The portfolio as an investment position rather than a stock list."""
    items = list_positions(session, auth, execution_mode=execution_mode)
    open_items = [item for item in items if item.row.status == PositionStatus.OPEN.value]
    closed_items = [item for item in items if item.row.status == PositionStatus.CLOSED.value]

    deployed = money(sum((item.row.capital_invested for item in open_items), Decimal("0")))
    realized = money(sum((item.row.realized_profit for item in closed_items), Decimal("0")))
    expected = money(
        sum(((item.row.expected_profit or Decimal("0")) for item in open_items), Decimal("0"))
    )
    unrealized = money(sum((item.unrealized_value for item in open_items), Decimal("0")))
    closed_capital = money(
        sum((item.row.capital_invested for item in closed_items), Decimal("0"))
    )

    ages = [item.days_held for item in open_items]
    return {
        "open_positions": len(open_items),
        "closed_positions": len(closed_items),
        "capital_deployed": str(deployed),
        "expected_profit": str(expected),
        "unrealized_value": str(unrealized),
        "realized_profit": str(realized),
        "realized_roi": (
            None if closed_capital <= 0 else str(pct_of(realized, closed_capital))
        ),
        "oldest_position_days": max(ages) if ages else 0,
        "average_age_days": (sum(ages) // len(ages)) if ages else 0,
        "by_mode": {
            mode.value: len([item for item in items if item.row.execution_mode == mode.value])
            for mode in ExecutionMode
        },
    }


def exposure(
    session: Session, auth: AuthContext, *, execution_mode: ExecutionMode | None = None
) -> dict[str, dict[str, Decimal]]:
    """Open capital by brand, category and marketplace.

    Read by the allocator so a policy's diversification limits are checked
    against what is actually held, not only against the plan being built.
    """
    items = list_positions(
        session, auth, execution_mode=execution_mode, status=PositionStatus.OPEN
    )
    buckets: dict[str, dict[str, Decimal]] = {"brand": {}, "category": {}, "marketplace": {}}
    for item in items:
        row = item.row
        for key, value in (
            ("brand", row.brand),
            ("category", row.category),
            ("marketplace", row.target_marketplace),
        ):
            if not value:
                continue
            buckets[key][value] = money(
                buckets[key].get(value, Decimal("0")) + row.capital_invested
            )
    return buckets
