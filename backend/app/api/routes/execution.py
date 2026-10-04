"""Execution: what a person has been asked to buy, and what they actually did.

Spreadline decides what, how many and at what maximum price. It does not place
retail orders, and none of these routes does either: there is no checkout here,
no purchasing bot, no stored payment method. They hand an instruction across the
boundary and take a record back.

The return leg is the point. Without it a position stays at the figures that
were authorised rather than the ones that were paid, and every realised number
downstream measures a purchase that never happened.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import Auth, DbSession
from app.domains.execution import service as execution
from app.schemas.execution import CancelInstructionRequest, RecordExecutionRequest

router = APIRouter(prefix="/execution", tags=["execution"])


@router.get("")
def overview(session: DbSession, auth: Auth) -> dict[str, Any]:
    """Everything waiting on a person, and how faithfully past ones were carried out."""
    execution.expire_overdue(session, auth)
    body = execution.report(session, auth)
    session.commit()
    return body


@router.get("/instructions")
def list_instructions(
    session: DbSession,
    auth: Auth,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    rows = execution.listing(session, auth, status=status, limit=limit)
    return {"items": [execution.view(row) for row in rows], "total": len(rows)}


@router.get("/instructions/{instruction_id}")
def get_instruction(instruction_id: str, session: DbSession, auth: Auth) -> dict[str, Any]:
    return execution.view(execution.get(session, auth, instruction_id))


@router.post("/instructions/{instruction_id}/record")
def record(
    instruction_id: str,
    payload: RecordExecutionRequest,
    session: DbSession,
    auth: Auth,
) -> dict[str, Any]:
    """Report what was actually bought against one authorisation.

    A quantity of zero is a valid report and the honest one when the stock was
    gone: it closes the instruction, releases the capital the position was
    holding, and is recorded as an outcome rather than left pending forever.
    """
    row = execution.record_execution(
        session,
        auth,
        instruction_id,
        execution.ExecutionInput(
            quantity=payload.quantity,
            unit_price=payload.unit_price,
            order_reference=payload.order_reference,
            shipping_cost=payload.shipping_cost,
            tax=payload.tax,
            other_costs=payload.other_costs,
            notes=payload.notes,
        ),
        actor=auth.actor,
    )
    session.commit()
    return execution.view(row)


@router.post("/instructions/{instruction_id}/cancel")
def cancel(
    instruction_id: str,
    payload: CancelInstructionRequest,
    session: DbSession,
    auth: Auth,
) -> dict[str, Any]:
    row = execution.cancel(
        session, auth, instruction_id, reason=payload.reason, actor=auth.actor
    )
    session.commit()
    return execution.view(row)
