"""Autonomy: policy, performance, eligibility, positions and control.

The shape of this API encodes the governance model. Reading the state is cheap
and open; changing what the system may do with money is always an explicit,
named action by a person. There is no endpoint that raises a limit as a side
effect of anything else, and the gate reports eligibility without ever acting on
it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import Auth, DbSession
from app.core.clock import iso_utc
from app.core.errors import NotFoundError, ValidationError
from app.domains.autonomy import (
    allocation,
    breakers,
    eligibility,
    engine,
    gate,
    positions,
    scorecard,
    selling,
)
from app.domains.autonomy import policy as policy_module
from app.domains.backtest import engine as backtest
from app.domains.learning import attribution
from app.domains.learning import velocity as velocity_module
from app.domains.opportunities import service as opportunities_service
from app.models.autonomy import AutonomyDecision, AutonomyEvent
from app.models.enums import AutonomyEventType, AutonomyLevel, ExecutionMode, PositionStatus
from app.schemas.autonomy import (
    AllocationRequest,
    BacktestRequest,
    EmergencyStopRequest,
    EnableAutonomyRequest,
    PolicyUpdateRequest,
    RunDecisionRequest,
)

router = APIRouter(prefix="/autonomy", tags=["autonomy"])


@router.get("")
def overview(session: DbSession, auth: Auth) -> dict[str, Any]:
    """Everything the autonomy dashboard needs, in one read."""
    policy = policy_module.view(policy_module.active_policy(session, auth))
    card = scorecard.build(session, auth)
    readings = breakers.read(session, auth)
    target = gate.next_level(policy.autonomy_level)
    evaluation = gate.evaluate(card, target) if target else None

    return {
        "policy": policy.as_dict(),
        "level": {
            "current": int(policy.autonomy_level),
            "label": policy.autonomy_level.label,
            "deploys_capital": policy.autonomy_level.deploys_capital,
            "execution_mode": policy.execution_mode.value,
        },
        "capital": {
            "authorized": str(policy.capital_limit),
            "deployed": str(
                positions.deployed_capital(session, auth, execution_mode=policy.execution_mode)
            ),
            "available": str(
                max(
                    Decimal("0"),
                    policy.capital_limit
                    - positions.deployed_capital(
                        session, auth, execution_mode=policy.execution_mode
                    ),
                )
            ),
        },
        "portfolio": positions.portfolio(session, auth, execution_mode=policy.execution_mode),
        "performance": card.as_dict(),
        "next_level": evaluation.as_dict() if evaluation else None,
        "circuit_breakers": [reading.as_dict() for reading in readings],
        "emergency_stop": {
            "enabled": policy.emergency_stop_enabled,
            "active": policy.emergency_stop_active,
            "reason": policy.emergency_stop_reason,
        },
    }


@router.get("/policy")
def read_policy(session: DbSession, auth: Auth) -> dict[str, Any]:
    policy = policy_module.view(policy_module.active_policy(session, auth))
    return {
        "policy": policy.as_dict(),
        "history": [
            {
                "version": row.version,
                "is_active": row.is_active,
                "autonomy_level": row.autonomy_level,
                "capital_limit": str(row.capital_limit),
                "execution_mode": row.execution_mode,
                "created_at": iso_utc(row.created_at),
                "created_by": row.created_by,
                "note": row.note,
            }
            for row in policy_module.history(session, auth)
        ],
    }


@router.put("/policy")
def write_policy(payload: PolicyUpdateRequest, session: DbSession, auth: Auth) -> dict[str, Any]:
    """Write a new policy version. The previous one is kept, not overwritten."""
    changes = payload.model_dump(exclude_unset=True, exclude_none=False)
    version = changes.pop("version", None)
    if not changes:
        raise ValidationError("No policy changes supplied.")
    row = policy_module.update_policy(session, auth, changes, version=version)
    session.commit()
    return policy_module.view(row).as_dict()


@router.get("/performance")
def performance(
    session: DbSession,
    auth: Auth,
    execution_mode: ExecutionMode | None = None,
) -> dict[str, Any]:
    """The scorecard. Pass an execution mode to compare shadow against live."""
    return scorecard.build(session, auth, execution_mode=execution_mode).as_dict()


@router.get("/eligibility")
def level_eligibility(
    session: DbSession, auth: Auth, target_level: int | None = None
) -> dict[str, Any]:
    """Whether the system has earned a level, and what is missing if not.

    Reports only. Nothing here changes a policy: a system that could promote
    itself on its own performance report is not governed by one.
    """
    policy = policy_module.view(policy_module.active_policy(session, auth))
    requirements = (
        gate.requirements_for(AutonomyLevel(target_level))
        if target_level is not None
        else gate.next_level(policy.autonomy_level)
    )
    if requirements is None:
        return {
            "current_level": int(policy.autonomy_level),
            "target": None,
            "summary": "No higher level is defined in the ladder.",
        }
    card = scorecard.build(session, auth)
    evaluation = gate.evaluate(card, requirements)
    return {
        "current_level": int(policy.autonomy_level),
        "current_label": policy.autonomy_level.label,
        **evaluation.as_dict(),
    }


@router.get("/ladder")
def ladder(session: DbSession, auth: Auth) -> dict[str, Any]:
    """Every level, its capital figure and what it requires."""
    policy = policy_module.view(policy_module.active_policy(session, auth))
    card = scorecard.build(session, auth)
    return {
        "current_level": int(policy.autonomy_level),
        "levels": [
            gate.evaluate(card, requirements).as_dict() for requirements in gate.DEFAULT_LADDER
        ],
    }


@router.post("/enable")
def enable(payload: EnableAutonomyRequest, session: DbSession, auth: Auth) -> dict[str, Any]:
    """Authorise a level of autonomous capital. Always explicit, never implied.

    The gate is consulted and its verdict recorded, but the authorisation is the
    person's: ``acknowledge_not_eligible`` is how they say they know the system
    has not met the criteria and are proceeding anyway. That is a legitimate
    thing for an owner of capital to do, and it is recorded as such.
    """
    requirements = gate.requirements_for(AutonomyLevel(payload.level))
    if requirements is None:
        raise ValidationError(f"Level {payload.level} is not an autonomy level with a ladder rung.")

    evaluation, _row = gate.evaluate_and_record(session, auth, requirements)
    if not evaluation.eligible and not payload.acknowledge_not_eligible:
        session.commit()
        raise ValidationError(
            f"{evaluation.summary} Set acknowledge_not_eligible to proceed anyway."
        )

    limit = payload.capital_limit or requirements.capital_limit
    if limit > requirements.capital_limit and not payload.acknowledge_not_eligible:
        raise ValidationError(
            f"Level {payload.level} authorises up to "
            f"{requirements.capital_limit} by default; a higher limit has to be "
            "acknowledged explicitly."
        )

    updated = policy_module.update_policy(
        session,
        auth,
        {
            "autonomy_level": payload.level,
            "capital_limit": limit,
            "execution_mode": payload.execution_mode.value,
            "require_human_approval": False,
            "note": payload.note or f"Autonomy enabled at level {payload.level}.",
        },
    )
    policy_module.record_event(
        session,
        auth,
        AutonomyEventType.EXPERIMENT_STARTED,
        message=(
            f"Level {payload.level} authorised with {limit} in "
            f"{payload.execution_mode.value} mode."
        ),
        actor=auth.email,
        payload={
            "level": payload.level,
            "capital_limit": str(limit),
            "eligible": evaluation.eligible,
            "acknowledged_not_eligible": payload.acknowledge_not_eligible,
        },
    )
    session.commit()
    return policy_module.view(updated).as_dict()


@router.post("/disable")
def disable(session: DbSession, auth: Auth) -> dict[str, Any]:
    """Return to human approval and zero autonomous capital."""
    updated = policy_module.update_policy(
        session,
        auth,
        {
            "autonomy_level": int(AutonomyLevel.HUMAN_APPROVAL),
            "capital_limit": Decimal("0"),
            "execution_mode": ExecutionMode.OBSERVE.value,
            "require_human_approval": True,
            "note": "Autonomy disabled.",
        },
    )
    policy_module.record_event(
        session,
        auth,
        AutonomyEventType.EXPERIMENT_STOPPED,
        message="Autonomous capital withdrawn; human approval required.",
        actor=auth.email,
    )
    session.commit()
    return policy_module.view(updated).as_dict()


@router.post("/emergency-stop")
def emergency_stop(
    payload: EmergencyStopRequest, session: DbSession, auth: Auth
) -> dict[str, Any]:
    """Stop or resume autonomous activity immediately.

    Stopping does not unwind anything. Existing positions are still held, still
    monitored and still reported; what stops is new capital.
    """
    row = policy_module.set_emergency_stop(
        session,
        auth,
        active=payload.active,
        reason=payload.reason,
        actor=auth.email,
    )
    session.commit()
    return policy_module.view(row).as_dict()


@router.get("/positions")
def list_positions(
    session: DbSession,
    auth: Auth,
    execution_mode: ExecutionMode | None = None,
    status: PositionStatus | None = None,
) -> dict[str, Any]:
    """Inventory as capital positions, not as a stock list."""
    items = positions.list_positions(
        session, auth, execution_mode=execution_mode, status=status
    )
    return {
        "items": [item.as_dict() for item in items],
        "total": len(items),
        "portfolio": positions.portfolio(session, auth, execution_mode=execution_mode),
    }


@router.get("/decisions")
def list_decisions(
    session: DbSession,
    auth: Auth,
    outcome: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Every decision, authorised or not.

    Refusals are kept as carefully as authorisations. A system that only logged
    its purchases could not demonstrate its restraint.
    """
    query = select(AutonomyDecision).where(
        AutonomyDecision.organization_id == auth.organization_id
    )
    if outcome:
        query = query.where(AutonomyDecision.outcome == outcome)
    rows = list(
        session.scalars(query.order_by(AutonomyDecision.created_at.desc()).limit(limit))
    )
    return {
        "items": [
            {
                "id": row.id,
                "opportunity_id": row.opportunity_id,
                "outcome": row.outcome,
                "reason_code": row.reason_code,
                "reason": row.reason,
                "policy_version": row.policy_version,
                "autonomy_level": row.autonomy_level,
                "execution_mode": row.execution_mode,
                "quantity": row.quantity,
                "capital_committed": (
                    None if row.capital_committed is None else str(row.capital_committed)
                ),
                "max_unit_price": (
                    None if row.max_unit_price is None else str(row.max_unit_price)
                ),
                "expected_profit": (
                    None if row.expected_profit is None else str(row.expected_profit)
                ),
                "stage_verdicts": row.stage_verdicts,
                "created_at": iso_utc(row.created_at),
            }
            for row in rows
        ],
        "total": len(rows),
    }


@router.get("/decisions/{decision_id}")
def read_decision(decision_id: str, session: DbSession, auth: Auth) -> dict[str, Any]:
    """One decision, with the evidence exactly as it was when it was made."""
    row = session.scalar(
        select(AutonomyDecision).where(
            AutonomyDecision.organization_id == auth.organization_id,
            AutonomyDecision.id == decision_id,
        )
    )
    if row is None:
        raise NotFoundError(f"No decision {decision_id}.")
    return {
        "id": row.id,
        "opportunity_id": row.opportunity_id,
        "outcome": row.outcome,
        "reason_code": row.reason_code,
        "reason": row.reason,
        "policy_version": row.policy_version,
        "autonomy_level": row.autonomy_level,
        "execution_mode": row.execution_mode,
        "quantity": row.quantity,
        "capital_committed": (
            None if row.capital_committed is None else str(row.capital_committed)
        ),
        "max_unit_price": None if row.max_unit_price is None else str(row.max_unit_price),
        "expected_profit": None if row.expected_profit is None else str(row.expected_profit),
        "stage_verdicts": row.stage_verdicts,
        "evidence": row.evidence,
        "created_at": iso_utc(row.created_at),
        "runs": [
            {
                "stage": run.stage,
                "verdict": run.verdict,
                "summary": run.summary,
                "payload": run.payload,
                "created_at": iso_utc(run.created_at),
            }
            for run in row.runs
        ],
    }


@router.post("/decisions/run")
def run_decision(
    payload: RunDecisionRequest, session: DbSession, auth: Auth
) -> dict[str, Any]:
    """Run the decision process over one opportunity.

    Opens a position when the decision authorises one and the policy is in
    shadow or live mode. In live mode the position records committed capital and
    a maximum price; the retail order itself remains a human action.
    """
    opportunity = opportunities_service.get_opportunity(session, auth, payload.opportunity_id)
    result, row, position = engine.authorize(
        session,
        auth,
        opportunity,
        available_capital=payload.available_capital,
    )
    session.commit()
    return {
        "decision": result.as_dict(),
        "decision_id": row.id if row else None,
        "position": positions.view(position).as_dict() if position else None,
    }


@router.get("/opportunities/{opportunity_id}/eligibility")
def opportunity_eligibility(
    opportunity_id: str, session: DbSession, auth: Auth
) -> dict[str, Any]:
    """Whether one opportunity could be bought autonomously, and why not."""
    opportunity = opportunities_service.get_opportunity(session, auth, opportunity_id)
    policy = policy_module.view(policy_module.active_policy(session, auth))
    return eligibility.assess(
        opportunity, policy, primary_blocker=engine.stored_blocker(opportunity)
    ).as_dict()


@router.get("/circuit-breakers")
def list_breakers(session: DbSession, auth: Auth) -> dict[str, Any]:
    readings = breakers.read(session, auth)
    return {
        "items": [reading.as_dict() for reading in readings],
        "any_tripped": any(
            reading.row.state == "tripped" and reading.row.is_enabled for reading in readings
        ),
    }


@router.post("/circuit-breakers/{breaker_id}/reset")
def reset_breaker(breaker_id: str, session: DbSession, auth: Auth) -> dict[str, Any]:
    """Clear one breaker. Always a human action."""
    row = breakers.reset(session, auth, breaker_id, actor=auth.email)
    if row is None:
        raise NotFoundError(f"No circuit breaker {breaker_id}.")
    session.commit()
    return {"id": row.id, "code": row.code, "state": row.state, "reset_by": row.reset_by}


@router.get("/sell-review")
def sell_review(
    session: DbSession,
    auth: Auth,
    execution_mode: ExecutionMode | None = None,
) -> dict[str, Any]:
    """What to do with every open position.

    Buying well is half the decision. This is the other half, answered from
    different evidence: what the exit market is doing now, how long the capital
    has been tied up, and what is still left to make.

    Recommendations only. Nothing here lists, reprices or sells.
    """
    policy = policy_module.view(policy_module.active_policy(session, auth))
    return selling.review(session, auth, policy, execution_mode=execution_mode)


@router.get("/learning")
def learning(session: DbSession, auth: Auth) -> dict[str, Any]:
    """Where the predictions are consistently wrong.

    The scorecard says how accurate the system has been overall. An average
    error is the one number guaranteed to hide a pattern, so this segments the
    closed outcomes and looks for bias with enough consistency behind it to be
    worth acting on.

    Findings, never actions. Nothing here retunes a weight or an assumption: an
    engine that silently adjusts itself on twelve observations is how a small
    sampling accident becomes a permanent rule.
    """
    return attribution.attribute(session, auth).as_dict()


@router.get("/velocity")
def velocity(session: DbSession, auth: Auth) -> dict[str, Any]:
    """How long capital actually stays tied up, measured from closed positions.

    Every other return figure in the product is a return on capital with no time
    in it, and a 20% return in ten days is worth more than a 30% return in
    ninety. This is the missing half, and it is measured rather than assumed: no
    closed positions means no holding period, and nothing is invented to stand
    in for one.
    """
    return velocity_module.measure(session, auth).as_dict()


@router.get("/allocation")
def allocation_plan(
    session: DbSession,
    auth: Auth,
    budget: Decimal | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """What to buy with the capital available, across every eligible candidate.

    The question the per-opportunity path cannot answer. Decided one at a time,
    whichever candidate is looked at first takes the capital and the rest are
    refused for "no capital available" without ever being compared to it. Here
    they are ranked against each other on profit per dollar, and the portfolio
    limits are applied across the whole slate rather than against the database.

    Read-only. This proposes; it opens nothing.
    """
    policy = policy_module.view(policy_module.active_policy(session, auth))
    return allocation.plan(session, auth, policy, budget=budget).as_dict()


@router.post("/allocation/commit")
def commit_allocation(
    payload: AllocationRequest, session: DbSession, auth: Auth
) -> dict[str, Any]:
    """Act on the plan, one line at a time, through the decision engine.

    The plan is recomputed here rather than taken from the request: a client
    that could post its own lines could post a quantity nobody proposed. Every
    line then goes through ``engine.decide`` with its own capital as the
    ceiling, so the emergency stop, the circuit breakers, eligibility and stage
    disagreement all still apply, and a line the engine refuses is recorded as
    carefully as one it authorises.
    """
    policy = policy_module.view(policy_module.active_policy(session, auth))
    proposal = allocation.plan(session, auth, policy, budget=payload.budget)
    if not proposal.may_commit:
        raise ValidationError(proposal.blocked_reason or "This plan cannot be committed.")
    if (
        payload.expect_capital is not None
        and proposal.capital_allocated != payload.expect_capital
    ):
        raise ValidationError(
            "The plan has changed since it was read: it now commits "
            f"{proposal.capital_allocated} rather than {payload.expect_capital}. "
            "Read it again before committing."
        )
    result = allocation.commit(session, auth, proposal, actor=auth.actor)
    session.commit()
    return result.as_dict()


@router.post("/backtest")
def run_backtest(
    payload: BacktestRequest, session: DbSession, auth: Auth
) -> dict[str, Any]:
    """Replay the active policy against the history Spreadline recorded itself.

    The strongest evidence available before real capital moves, and worthless if
    it cheats: at each simulated day only observations recorded on or before
    that day are visible, and profitability is recomputed by the same engine the
    live path uses at that day's prices.

    The result always carries its caveats. The exit rule is an assumption, not a
    measurement, and a return figure whose selling assumption is hidden is a
    number pretending to be one.
    """
    policy = policy_module.view(policy_module.active_policy(session, auth))
    result = backtest.run(
        session,
        auth,
        policy,
        days=payload.days,
        starting_capital=payload.starting_capital,
        step_days=payload.step_days,
    )
    return result.as_dict()


@router.get("/events")
def list_events(
    session: DbSession, auth: Auth, limit: int = Query(default=100, ge=1, le=500)
) -> dict[str, Any]:
    """The audit log. Every autonomous action and every instruction given to it."""
    rows = list(
        session.scalars(
            select(AutonomyEvent)
            .where(AutonomyEvent.organization_id == auth.organization_id)
            .order_by(AutonomyEvent.created_at.desc())
            .limit(limit)
        )
    )
    return {
        "items": [
            {
                "id": row.id,
                "type": row.event_type,
                "actor": row.actor,
                "message": row.message,
                "opportunity_id": row.opportunity_id,
                "decision_id": row.decision_id,
                "payload": row.payload,
                "created_at": iso_utc(row.created_at),
            }
            for row in rows
        ]
    }
