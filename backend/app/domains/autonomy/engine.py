"""The autonomous decision process.

    candidate -> identity -> pricing -> demand -> competition -> profitability
              -> risk -> eligibility -> capital allocation -> policy -> decision

Each stage is one of the existing deterministic engines under the name an
investment organisation would use for it, and each records what it concluded.
Naming them is what makes a decision auditable stage by stage, and what lets two
stages disagree in a way the system escalates rather than averages away.

Three properties of this module are load-bearing:

**The deterministic engines control eligibility.** Nothing reasons its way past
a failed check. There is no weighting, no majority and no override path inside
the system: one mandatory failure ends it.

**Disagreement escalates.** If underwriting says proceed and risk says review,
the answer is not the average of the two. It is a human.

**Nothing here is an order.** Spreadline decides what, where, how much and at
what maximum price. Placing the order stays a human action until an authorised
supplier integration exists, and the authorisation record is shaped so that
adding one changes an executor, not this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import iso_utc, utcnow
from app.core.money import display_currency, display_score, money
from app.core.security import AuthContext
from app.domains.autonomy import breakers, eligibility, positions
from app.domains.autonomy import policy as policy_module
from app.domains.autonomy.policy import PolicyView
from app.models.autonomy import AgentRun, AutonomyDecision, CapitalPosition
from app.models.catalog import Product
from app.models.enums import (
    AgentStage,
    AgentVerdict,
    AutonomyEventType,
    ExecutionMode,
    RiskLevel,
)
from app.models.opportunity import Opportunity, ProfitabilitySnapshot, RiskAssessment

# --- outcomes -------------------------------------------------------------
AUTHORIZED = "authorized"
BLOCKED = "blocked"
ESCALATED = "escalated"

# --- reason codes. Stable, countable, assertable. -------------------------
CAPITAL_LIMIT_EXCEEDED = "CAPITAL_LIMIT_EXCEEDED"
POSITION_LIMIT_EXCEEDED = "POSITION_LIMIT_EXCEEDED"
DAILY_DEPLOYMENT_EXCEEDED = "DAILY_DEPLOYMENT_EXCEEDED"
MAX_LOSS_EXCEEDED = "MAX_LOSS_EXCEEDED"
BRAND_EXPOSURE_EXCEEDED = "BRAND_EXPOSURE_EXCEEDED"
CATEGORY_EXPOSURE_EXCEEDED = "CATEGORY_EXPOSURE_EXCEEDED"
MARKETPLACE_EXPOSURE_EXCEEDED = "MARKETPLACE_EXPOSURE_EXCEEDED"
EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
CIRCUIT_BREAKER_TRIPPED = "CIRCUIT_BREAKER_TRIPPED"
HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
AUTONOMY_LEVEL_TOO_LOW = "AUTONOMY_LEVEL_TOO_LOW"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
NO_CAPITAL_AVAILABLE = "NO_CAPITAL_AVAILABLE"
AGENTS_DISAGREE = "AGENTS_DISAGREE"
NOTHING_TO_DO = "NOTHING_TO_DO"


@dataclass
class StageResult:
    """What one stage concluded, and the evidence behind it."""

    stage: AgentStage
    verdict: AgentVerdict
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    engine_version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "verdict": self.verdict.value,
            "summary": self.summary,
            "engine_version": self.engine_version,
        }


@dataclass
class DecisionResult:
    outcome: str
    reason_code: str | None
    reason: str
    stages: list[StageResult] = field(default_factory=list)
    quantity: int = 0
    unit_cost: Decimal = Decimal("0")
    capital: Decimal = Decimal("0")
    expected_profit: Decimal = Decimal("0")
    expected_roi: Decimal | None = None
    max_unit_price: Decimal | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def authorized(self) -> bool:
        return self.outcome == AUTHORIZED

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "quantity": self.quantity,
            "unit_cost": str(self.unit_cost),
            "capital": str(self.capital),
            "expected_profit": str(self.expected_profit),
            "expected_roi": None if self.expected_roi is None else str(self.expected_roi),
            "max_unit_price": (None if self.max_unit_price is None else str(self.max_unit_price)),
            "stages": [stage.as_dict() for stage in self.stages],
        }


def _snapshot(session: Session, opportunity_id: str) -> ProfitabilitySnapshot | None:
    return session.scalar(
        select(ProfitabilitySnapshot)
        .where(
            ProfitabilitySnapshot.opportunity_id == opportunity_id,
            ProfitabilitySnapshot.scenario == "base",
        )
        .order_by(ProfitabilitySnapshot.created_at.desc())
    )


def _stage(stage: AgentStage, verdict: AgentVerdict, summary: str, **payload: Any) -> StageResult:
    return StageResult(stage=stage, verdict=verdict, summary=summary, payload=payload)


def _underwriting(opportunity: Opportunity, snapshot: ProfitabilitySnapshot | None) -> StageResult:
    """The economics, as the investment analyst would read them."""
    if snapshot is None or opportunity.net_profit is None:
        return _stage(
            AgentStage.UNDERWRITING,
            AgentVerdict.INCONCLUSIVE,
            "No stored economics to underwrite.",
        )
    profitable = opportunity.net_profit > 0
    return _stage(
        AgentStage.UNDERWRITING,
        AgentVerdict.PROCEED if profitable else AgentVerdict.REJECT,
        (
            f"{display_currency(opportunity.net_profit)} per item on "
            f"{display_currency(opportunity.acquisition_cost)} of capital."
        ),
        net_profit=str(opportunity.net_profit),
        roi=None if opportunity.roi is None else str(opportunity.roi),
        max_acquisition_cost=(
            None if snapshot.max_acquisition_cost is None else str(snapshot.max_acquisition_cost)
        ),
    )


def _risk(session: Session, opportunity: Opportunity, policy: PolicyView) -> StageResult:
    """The risk officer's read.

    Deliberately a different question from the one eligibility asks. Eligibility
    checks the aggregate level against the policy ceiling; this stage looks at
    the breakdown underneath it and objects to two things the aggregate hides:

    * an individual high-severity signal, which averaging into a level is
      exactly how a clearance price or a rank collapse gets waved through;
    * a category that could not be assessed at all, because "no evidence" and
      "no risk" produce the same aggregate and are opposite findings.

    When this stage objects and the others do not, the decision escalates. That
    only means something because the questions differ: two stages that check the
    same threshold can never disagree, and a disagreement path that cannot fire
    is not a safeguard.
    """
    level = RiskLevel(opportunity.risk_level)
    within = level.rank <= policy.maximum_risk.rank

    assessment = session.scalar(
        select(RiskAssessment)
        .where(RiskAssessment.opportunity_id == opportunity.id)
        .order_by(RiskAssessment.created_at.desc())
    )
    signals = (assessment.signals if assessment else []) or []
    categories = (assessment.categories if assessment else []) or []
    elevated = [
        signal
        for signal in signals
        if isinstance(signal, dict) and signal.get("severity") in {"high", "critical"}
    ]
    unassessed = [
        item for item in categories if isinstance(item, dict) and not item.get("has_evidence", True)
    ]

    if not within:
        verdict = AgentVerdict.REVIEW
        summary = f"Risk is {level.value}; the policy allows up to {policy.maximum_risk.value}."
    elif elevated:
        verdict = AgentVerdict.REVIEW
        summary = (
            f"Risk level is {level.value} and within the mandate, but "
            f"{len(elevated)} high-severity signal(s) were raised: "
            f"{elevated[0].get('message', '')}"
        )
    elif unassessed:
        verdict = AgentVerdict.REVIEW
        summary = (
            f"Risk level is {level.value}, but "
            f"{len(unassessed)} risk category(ies) could not be assessed at all: "
            + ", ".join(str(item.get("label") or item.get("category")) for item in unassessed)
        )
    else:
        verdict = AgentVerdict.PROCEED
        summary = f"Risk is {level.value}; the policy allows up to {policy.maximum_risk.value}."

    return _stage(
        AgentStage.RISK,
        verdict,
        summary,
        risk_level=level.value,
        risk_score=None if opportunity.risk_score is None else str(opportunity.risk_score),
        elevated_signals=[signal.get("code") for signal in elevated],
        unassessed_categories=[item.get("category") for item in unassessed],
    )


def decide(
    session: Session,
    auth: AuthContext,
    opportunity: Opportunity,
    *,
    available_capital: Decimal | None = None,
    primary_blocker: str | None = None,
    persist: bool = True,
) -> tuple[DecisionResult, AutonomyDecision | None]:
    """Run the full process for one opportunity and record the result.

    Returns the decision and the stored authorization record. A blocked decision
    is recorded exactly as carefully as an authorised one: the refusals are the
    evidence that the limits work, and a system that only logs its purchases
    cannot demonstrate its restraint.
    """
    policy_row = policy_module.active_policy(session, auth)
    policy = policy_module.view(policy_row)
    snapshot = _snapshot(session, opportunity.id)
    stages: list[StageResult] = []

    # --- scout -----------------------------------------------------------
    stages.append(
        _stage(
            AgentStage.SCOUT,
            AgentVerdict.PROCEED,
            f"Candidate scored {display_score(opportunity.score)} out of 100 and was "
            f"called a {opportunity.recommendation}.",
            score=None if opportunity.score is None else str(opportunity.score),
            recommendation=opportunity.recommendation,
        )
    )
    stages.append(_underwriting(opportunity, snapshot))
    stages.append(_risk(session, opportunity, policy))

    # --- eligibility -----------------------------------------------------
    checks = eligibility.assess(opportunity, policy, primary_blocker=primary_blocker)
    stages.append(
        StageResult(
            stage=AgentStage.ELIGIBILITY,
            verdict=AgentVerdict.PROCEED if checks.eligible else AgentVerdict.REJECT,
            summary=checks.summary,
            payload=checks.as_dict(),
        )
    )

    def finish(
        outcome: str,
        code: str | None,
        reason: str,
        *,
        quantity: int = 0,
        unit_cost: Decimal = Decimal("0"),
        capital: Decimal = Decimal("0"),
        expected_profit: Decimal = Decimal("0"),
        expected_roi: Decimal | None = None,
        max_unit_price: Decimal | None = None,
    ) -> tuple[DecisionResult, AutonomyDecision | None]:
        stages.append(
            _stage(
                AgentStage.DECISION,
                AgentVerdict.PROCEED if outcome == AUTHORIZED else AgentVerdict.REJECT,
                reason,
            )
        )
        result = DecisionResult(
            outcome=outcome,
            reason_code=code,
            reason=reason,
            stages=stages,
            quantity=quantity,
            unit_cost=unit_cost,
            capital=capital,
            expected_profit=expected_profit,
            expected_roi=expected_roi,
            max_unit_price=max_unit_price,
            evidence=_evidence(opportunity, snapshot, policy, checks),
        )
        row = _persist(session, auth, opportunity, policy, result) if persist else None
        return result, row

    if not checks.eligible:
        return finish(BLOCKED, checks.reason_code or NOT_ELIGIBLE, checks.summary)

    # --- disagreement ----------------------------------------------------
    # Averaging opinions is how a risk objection gets diluted by two confident
    # yeses. When the stages that have an opinion do not share it, the decision
    # belongs to a person.
    opinions = {
        stage.stage: stage.verdict
        for stage in stages
        if stage.verdict is not AgentVerdict.INCONCLUSIVE
        and stage.stage in {AgentStage.UNDERWRITING, AgentStage.RISK, AgentStage.ELIGIBILITY}
    }
    if AgentVerdict.PROCEED in opinions.values() and (
        AgentVerdict.REVIEW in opinions.values() or AgentVerdict.REJECT in opinions.values()
    ):
        disagreeing = ", ".join(
            f"{stage.value}={verdict.value}" for stage, verdict in opinions.items()
        )
        return finish(
            ESCALATED,
            AGENTS_DISAGREE,
            f"Stages disagree on risk-adjusted attractiveness ({disagreeing}). "
            "Escalated for a human rather than averaged.",
        )

    # --- policy gates before any capital is sized ------------------------
    if policy.emergency_stop_active:
        return finish(
            BLOCKED,
            EMERGENCY_STOP_ACTIVE,
            f"Autonomous deployment is stopped: {policy.emergency_stop_reason}",
        )

    live_breakers = breakers.tripped(session, auth)
    if live_breakers:
        names = ", ".join(row.label for row in live_breakers)
        return finish(
            BLOCKED,
            CIRCUIT_BREAKER_TRIPPED,
            f"Capital circuit breaker tripped ({names}). A person must reset it.",
        )

    if not policy.autonomy_level.deploys_capital:
        return finish(
            BLOCKED,
            AUTONOMY_LEVEL_TOO_LOW,
            f"Autonomy level {int(policy.autonomy_level)} ({policy.autonomy_level.label}) "
            "does not deploy capital. Recommendation only.",
        )
    if policy.require_human_approval:
        return finish(
            BLOCKED,
            HUMAN_APPROVAL_REQUIRED,
            "The policy requires human approval before any position is opened.",
        )
    if policy.execution_mode is ExecutionMode.OBSERVE:
        return finish(
            BLOCKED,
            AUTONOMY_LEVEL_TOO_LOW,
            "Execution mode is observe: the system analyses but opens no positions.",
        )

    # --- capital ---------------------------------------------------------
    already = positions.deployed_capital(session, auth, execution_mode=policy.execution_mode)
    budget = money(available_capital if available_capital is not None else policy.capital_limit)
    budget = min(budget, money(policy.capital_limit - already))
    if budget <= 0:
        return finish(
            BLOCKED,
            NO_CAPITAL_AVAILABLE,
            f"No capital available: {display_currency(already)} of the "
            f"{display_currency(policy.capital_limit)} limit is already deployed.",
        )

    allocation = _allocate(session, auth, opportunity, snapshot, policy, budget)
    stages.append(
        StageResult(
            stage=AgentStage.CAPITAL,
            verdict=(AgentVerdict.PROCEED if allocation["quantity"] > 0 else AgentVerdict.REJECT),
            summary=allocation["summary"],
            payload=allocation,
        )
    )
    if allocation["quantity"] <= 0:
        return finish(BLOCKED, allocation["reason_code"], allocation["summary"])

    stages.append(
        _stage(
            AgentStage.POLICY,
            AgentVerdict.PROCEED,
            f"Within policy {policy.version} at level {int(policy.autonomy_level)}.",
            policy_version=policy.version,
        )
    )

    capital = money(allocation["capital"])
    return finish(
        AUTHORIZED,
        None,
        (
            f"Authorised {allocation['quantity']} unit(s) at up to "
            f"{display_currency(allocation['max_unit_price'])} each, "
            f"{display_currency(capital)} committed."
        ),
        quantity=allocation["quantity"],
        unit_cost=money(allocation["unit_cost"]),
        capital=capital,
        expected_profit=money(allocation["expected_profit"]),
        expected_roi=opportunity.roi,
        max_unit_price=money(allocation["max_unit_price"]),
    )


def stored_blocker(opportunity: Opportunity) -> str | None:
    """The first unresolved gate on the stored explanation, hard gates first.

    Read rather than re-derived, so eligibility and the recommendation cannot
    disagree about what is wrong with a candidate.
    """
    gates = (opportunity.explanation or {}).get("gates") or []
    failed = [item for item in gates if isinstance(item, dict) and not item.get("passed", True)]
    if not failed:
        return None
    failed.sort(key=lambda item: not item.get("hard", False))
    return str(failed[0].get("label") or "") or None


def authorize(
    session: Session,
    auth: AuthContext,
    opportunity: Opportunity,
    *,
    available_capital: Decimal | None = None,
) -> tuple[DecisionResult, AutonomyDecision | None, CapitalPosition | None]:
    """Decide, and open the position when the decision authorises one.

    One function rather than two steps a caller has to remember, because the
    two steps have to happen together: a decision recorded as authorised with
    no position behind it reports capital that is not held. Every caller that
    acts on a decision goes through here.
    """
    result, row = decide(
        session,
        auth,
        opportunity,
        available_capital=available_capital,
        primary_blocker=stored_blocker(opportunity),
    )

    position: CapitalPosition | None = None
    if result.authorized and row is not None:
        policy = policy_module.view(policy_module.active_policy(session, auth))
        product = (
            session.get(Product, opportunity.product_id) if opportunity.product_id else None
        )
        position = positions.open_position(
            session,
            auth,
            execution_mode=policy.execution_mode,
            quantity=result.quantity,
            unit_cost=result.unit_cost,
            opportunity_id=opportunity.id,
            decision_id=row.id,
            product_id=opportunity.product_id,
            title=product.title if product else None,
            brand=product.brand if product else None,
            category=product.category if product else None,
            source_marketplace=opportunity.source_marketplace,
            target_marketplace=opportunity.target_marketplace,
            expected_unit_profit=opportunity.net_profit,
            expected_roi=opportunity.roi,
            risk_level=opportunity.risk_level,
        )

        # An authorisation that nobody can act on and nobody can close out is
        # where the system used to stop learning: the position stayed at the
        # figures that were authorised and was never corrected by the ones that
        # were paid. The instruction is the thing handed across that boundary
        # and the thing handed back.
        from app.domains.execution import service as execution

        execution.issue(
            session,
            auth,
            decision=row,
            position=position,
            opportunity=opportunity,
        )

    # Evaluated after every decision, not only after a purchase: a breaker that
    # only ever reads on the happy path is not a breaker.
    breakers.evaluate(session, auth)
    return result, row, position


def _allocate(
    session: Session,
    auth: AuthContext,
    opportunity: Opportunity,
    snapshot: ProfitabilitySnapshot | None,
    policy: PolicyView,
    budget: Decimal,
) -> dict[str, Any]:
    """Size the position, and say which limit decided the size.

    Sometimes the right answer is zero. The system is never required to deploy
    capital it has, and a limit that produces zero units is reported as the
    binding limit rather than quietly rounded up to one.
    """
    unit_cost = money(opportunity.acquisition_cost or Decimal("0"))
    if unit_cost <= 0:
        return {
            "quantity": 0,
            "reason_code": NO_CAPITAL_AVAILABLE,
            "summary": "No acquisition cost stored, so no position can be sized.",
            "capital": "0",
            "unit_cost": "0",
            "expected_profit": "0",
            "max_unit_price": "0",
        }

    unit_profit = money(opportunity.net_profit or Decimal("0"))
    limits: list[tuple[str, Decimal, str]] = [
        ("budget", budget, "capital available under the policy limit"),
        (
            "position_cap",
            policy.position_cap(policy.capital_limit),
            "maximum share of capital allowed in one position",
        ),
    ]
    if policy.max_daily_deployment is not None:
        opened_today = money(
            sum(
                (
                    item.row.capital_invested
                    for item in positions.list_positions(
                        session, auth, execution_mode=policy.execution_mode
                    )
                    if item.days_held == 0
                ),
                Decimal("0"),
            )
        )
        limits.append(
            (
                "daily_deployment",
                money(policy.max_daily_deployment - opened_today),
                "capital already committed today",
            )
        )

    exposure = positions.exposure(session, auth, execution_mode=policy.execution_mode)
    # Brand and category live on the product, not on the opportunity: the
    # exposure limits are about what the capital is concentrated in, and that is
    # a property of the thing being bought.
    product = session.get(Product, opportunity.product_id) if opportunity.product_id else None
    attributes = {
        "brand": product.brand if product else None,
        "category": product.category if product else None,
        # Marketplace is the opportunity's, not the product's: the same product
        # bought to sell on two marketplaces is two different concentrations.
        "marketplace": opportunity.target_marketplace,
    }
    caps = {
        "brand": money(policy.capital_limit * policy.maximum_brand_exposure),
        "category": money(policy.capital_limit * policy.maximum_category_exposure),
        "marketplace": money(policy.capital_limit * policy.maximum_marketplace_exposure),
    }

    allowance = min(value for _, value, _ in limits)
    binding = min(limits, key=lambda item: item[1])

    quantity = int(allowance // unit_cost) if unit_cost > 0 else 0

    # Expected loss per position, where the policy sets one. The stress test's
    # worst case is the honest downside; falling back to the whole position is
    # deliberately conservative rather than optimistic.
    if policy.max_loss_per_position is not None and quantity > 0:
        worst_case_unit_loss = unit_cost
        affordable = int(money(policy.max_loss_per_position) // worst_case_unit_loss)
        if affordable < quantity:
            quantity = affordable
            binding = (
                "max_loss_per_position",
                money(policy.max_loss_per_position),
                "maximum expected loss allowed on one position",
            )

    if quantity <= 0:
        return {
            "quantity": 0,
            "reason_code": (
                POSITION_LIMIT_EXCEEDED
                if binding[0] == "position_cap"
                else DAILY_DEPLOYMENT_EXCEEDED
                if binding[0] == "daily_deployment"
                else MAX_LOSS_EXCEEDED
                if binding[0] == "max_loss_per_position"
                else CAPITAL_LIMIT_EXCEEDED
            ),
            "summary": (
                f"No units affordable: {display_currency(unit_cost)} per unit against "
                f"{display_currency(binding[1])} allowed by the {binding[2]}."
            ),
            "capital": "0",
            "unit_cost": str(unit_cost),
            "expected_profit": "0",
            "max_unit_price": str(unit_cost),
        }

    capital = money(unit_cost * quantity)

    for label, code in (
        ("brand", BRAND_EXPOSURE_EXCEEDED),
        ("category", CATEGORY_EXPOSURE_EXCEEDED),
        ("marketplace", MARKETPLACE_EXPOSURE_EXCEEDED),
    ):
        cap = caps[label]
        key = attributes.get(label)
        held = exposure[label].get(key or "", Decimal("0")) if key else Decimal("0")
        if key and money(held + capital) > cap:
            return {
                "quantity": 0,
                "reason_code": code,
                "summary": (
                    f"Would put {display_currency(held + capital)} into {label} "
                    f"'{key}', above the {display_currency(cap)} the policy allows."
                ),
                "capital": "0",
                "unit_cost": str(unit_cost),
                "expected_profit": "0",
                "max_unit_price": str(unit_cost),
            }

    ceiling = (
        snapshot.max_acquisition_cost
        if snapshot is not None and snapshot.max_acquisition_cost is not None
        else unit_cost
    )
    return {
        "quantity": quantity,
        "reason_code": None,
        "summary": (
            f"{quantity} unit(s) at {display_currency(unit_cost)}, "
            f"{display_currency(capital)} committed. Limited by the {binding[2]}."
        ),
        "capital": str(capital),
        "unit_cost": str(unit_cost),
        "expected_profit": str(money(unit_profit * quantity)),
        "max_unit_price": str(money(ceiling)),
        "limited_by": binding[0],
    }


def _evidence(
    opportunity: Opportunity,
    snapshot: ProfitabilitySnapshot | None,
    policy: PolicyView,
    checks: eligibility.EligibilityResult,
) -> dict[str, Any]:
    """Everything the decision rested on, frozen.

    "Why did Spreadline buy this" has to be answerable from this dict alone, six
    months later, without the surrounding rows still being what they were.
    """
    return {
        "captured_at": iso_utc(utcnow()),
        "opportunity": {
            "id": opportunity.id,
            "score": None if opportunity.score is None else str(opportunity.score),
            "recommendation": opportunity.recommendation,
            "source_marketplace": opportunity.source_marketplace,
            "target_marketplace": opportunity.target_marketplace,
            "acquisition_cost": (
                None if opportunity.acquisition_cost is None else str(opportunity.acquisition_cost)
            ),
            "expected_sale_price": (
                None
                if opportunity.expected_sale_price is None
                else str(opportunity.expected_sale_price)
            ),
            "net_profit": (None if opportunity.net_profit is None else str(opportunity.net_profit)),
            "roi": None if opportunity.roi is None else str(opportunity.roi),
            "margin": None if opportunity.margin is None else str(opportunity.margin),
            "risk_level": opportunity.risk_level,
            "match_confidence": (
                None if opportunity.match_confidence is None else str(opportunity.match_confidence)
            ),
            "data_quality_score": (
                None
                if opportunity.data_quality_score is None
                else str(opportunity.data_quality_score)
            ),
            "explanation": opportunity.explanation,
            "score_components": opportunity.score_components,
        },
        "economics": (
            None
            if snapshot is None
            else {
                "sale_price": str(snapshot.sale_price),
                "acquisition_cost": str(snapshot.acquisition_cost),
                "total_fees": str(snapshot.total_fees),
                "total_cost": str(snapshot.total_cost),
                "net_profit": str(snapshot.net_profit),
                "max_acquisition_cost": (
                    None
                    if snapshot.max_acquisition_cost is None
                    else str(snapshot.max_acquisition_cost)
                ),
                "assumptions_version": snapshot.assumptions_version,
                "assumptions": snapshot.assumptions,
            }
        ),
        "policy": policy.as_dict(),
        "eligibility": checks.as_dict(),
    }


def _persist(
    session: Session,
    auth: AuthContext,
    opportunity: Opportunity,
    policy: PolicyView,
    result: DecisionResult,
) -> AutonomyDecision:
    row = AutonomyDecision(
        organization_id=auth.organization_id,
        opportunity_id=opportunity.id,
        policy_id=policy.id,
        policy_version=policy.version,
        autonomy_level=int(policy.autonomy_level),
        execution_mode=policy.execution_mode.value,
        outcome=result.outcome,
        reason_code=result.reason_code,
        reason=result.reason[:1000],
        quantity=result.quantity or None,
        max_unit_price=result.max_unit_price,
        capital_committed=result.capital or None,
        expected_profit=result.expected_profit or None,
        expected_roi=result.expected_roi,
        risk_level=opportunity.risk_level,
        evidence=result.evidence,
        stage_verdicts={stage.stage.value: stage.verdict.value for stage in result.stages},
    )
    session.add(row)
    session.flush()

    for stage in result.stages:
        session.add(
            AgentRun(
                organization_id=auth.organization_id,
                opportunity_id=opportunity.id,
                decision_id=row.id,
                stage=stage.stage.value,
                verdict=stage.verdict.value,
                summary=stage.summary[:1000],
                payload=stage.payload,
                engine_version=stage.engine_version,
                duration_ms=None,
            )
        )

    event_type = {
        AUTHORIZED: AutonomyEventType.DECISION_AUTHORIZED,
        BLOCKED: AutonomyEventType.DECISION_BLOCKED,
        ESCALATED: AutonomyEventType.DECISION_ESCALATED,
    }[result.outcome]
    policy_module.record_event(
        session,
        auth,
        event_type,
        message=result.reason,
        opportunity_id=opportunity.id,
        decision_id=row.id,
        payload={"reason_code": result.reason_code, "capital": str(result.capital)},
    )
    session.flush()
    return row
