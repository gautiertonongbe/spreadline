"""Choosing between opportunities, not just checking them one at a time.

Every layer below this one answers a question about a single candidate: is it
the same product, what does it earn, how fragile is it, may it be bought. None
of them answers the question an owner of capital actually has, which is about
all of them at once:

    I have $500 and eleven things that clear the policy. Which ones, and how
    many of each?

Answered one at a time, that question has a silent answer: whichever candidate
happened to be looked at first takes the capital, and the eleventh is refused
for "no capital available" without anyone ever comparing it to the first. The
portfolio limits stop concentration; they do not choose. This module chooses.

**The scarce resource is capital, so the ranking is profit per dollar of it.**
Ordering by absolute profit buys one expensive position and leaves the budget
idle; ordering by return buys the most profit the budget can hold. Ties break on
score, then on the lower risk level, then on the identifier so two runs over the
same data produce the same plan.

**The limits are applied across the slate, not against the database.** This is
the whole difference from the per-opportunity path. As each line is funded, the
budget, the day's deployment and the brand, category and marketplace exposure
all move, so the fourth line is checked against a portfolio that already
contains the first three. A plan cannot propose two positions that are each
within the brand limit and together over it.

**Nothing is required to be spent.** A plan that funds three lines and leaves
40% of the budget idle is a legitimate answer and is reported as a decision
rather than as a shortfall. Capital sitting still costs its return; capital in
a position that should not have been opened costs the position.

**A plan is a proposal.** It opens nothing. Committing it runs every line back
through the decision engine, which re-checks the emergency stop, the breakers,
eligibility and stage disagreement exactly as it does for a single decision. The
allocator can only ever propose *less* than the engine would allow, never more,
because each line is committed with its own capital as the ceiling.

**Time counts, once it has been measured.** A 20% return in ten days is worth
more than a 30% return in ninety, so where the platform has closed positions to
learn a holding period from, candidates are ranked on return *per year of
capital tied up* rather than on return alone. Where it does not, they are ranked
on return per dollar exactly as before and the plan says so. Nothing invents a
days-to-sell figure to fill the gap: an estimate conjured to make the ranking
look more sophisticated would be the most important number in it and the only
one nobody measured. See ``domains/learning/velocity.py`` for what "measured"
requires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import display_currency, money, pct_of
from app.core.security import AuthContext
from app.domains.autonomy import eligibility, engine, positions
from app.domains.autonomy import policy as policy_module
from app.domains.autonomy.policy import PolicyView
from app.domains.learning.velocity import DAYS_IN_YEAR, VelocityReport, measure
from app.models.autonomy import CapitalPosition
from app.models.catalog import Product
from app.models.enums import AutonomyEventType, ExecutionMode, PositionStatus, RiskLevel
from app.models.opportunity import Opportunity, ProfitabilitySnapshot

#: Most candidates considered in one plan. A ceiling rather than a judgement:
#: the ranking is cheap, but a plan nobody can read is not a plan.
MAX_CANDIDATES = 500

#: Risk levels in the order a tie should break, least risky first.
_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

# --- why a candidate got nothing. Shared with the decision engine where the
# meaning is identical, so a refusal reads the same in both places.
NOT_ELIGIBLE = engine.NOT_ELIGIBLE
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
POSITION_LIMIT_EXCEEDED = engine.POSITION_LIMIT_EXCEEDED
DAILY_DEPLOYMENT_EXCEEDED = engine.DAILY_DEPLOYMENT_EXCEEDED
MAX_LOSS_EXCEEDED = engine.MAX_LOSS_EXCEEDED
BRAND_EXPOSURE_EXCEEDED = engine.BRAND_EXPOSURE_EXCEEDED
CATEGORY_EXPOSURE_EXCEEDED = engine.CATEGORY_EXPOSURE_EXCEEDED
MARKETPLACE_EXPOSURE_EXCEEDED = "MARKETPLACE_EXPOSURE_EXCEEDED"
ALREADY_HELD = "ALREADY_HELD"
NO_ECONOMICS = "NO_ECONOMICS"


@dataclass
class Candidate:
    """One opportunity, with everything the ranking and the limits need."""

    opportunity: Opportunity
    title: str
    unit_cost: Decimal
    unit_profit: Decimal
    return_per_dollar: Decimal
    max_unit_price: Decimal
    brand: str | None
    category: str | None
    marketplace: str | None
    #: Days this kind of position has historically taken to sell, and the
    #: evidence behind that figure. Both absent when nothing has closed yet.
    expected_days: int | None = None
    velocity_basis: str | None = None

    @property
    def id(self) -> str:
        return self.opportunity.id

    @property
    def annualized_return(self) -> Decimal | None:
        """Return per year of capital tied up, where the holding period is known."""
        if self.expected_days is None:
            return None
        return (
            self.return_per_dollar * DAYS_IN_YEAR / Decimal(max(1, self.expected_days))
        ).quantize(Decimal("0.0001"))

    @property
    def ranking_value(self) -> Decimal:
        """What the ordering is actually on.

        Annualised where the holding period has been measured, and the plain
        return per dollar where it has not. Within one plan every candidate uses
        the same basis, because a list half-ranked on one measure and half on
        another is ordered by nothing.
        """
        annual = self.annualized_return
        return annual if annual is not None else self.return_per_dollar

    @property
    def sort_key(self) -> tuple[Decimal, Decimal, int, str]:
        """Best return first, then score, then the lower risk level.

        Negated where the order is descending so a single ``sorted`` call with
        no reverse flag produces the ranking, and the identifier at the end
        makes two runs over the same data identical.
        """
        score = self.opportunity.score or Decimal("0")
        risk = _RISK_ORDER.get(RiskLevel(self.opportunity.risk_level), 9)
        return (-self.ranking_value, -score, risk, self.id)


@dataclass
class Line:
    """A funded line in the plan."""

    rank: int
    opportunity_id: str
    title: str
    quantity: int
    unit_cost: Decimal
    capital: Decimal
    expected_profit: Decimal
    return_per_dollar: Decimal
    max_unit_price: Decimal
    limited_by: str
    brand: str | None
    category: str | None
    marketplace: str | None
    expected_days: int | None = None
    annualized_return: Decimal | None = None
    velocity_basis: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "opportunity_id": self.opportunity_id,
            "title": self.title,
            "quantity": self.quantity,
            "unit_cost": str(self.unit_cost),
            "capital": str(self.capital),
            "expected_profit": str(self.expected_profit),
            "return_per_dollar": str(self.return_per_dollar),
            "max_unit_price": str(self.max_unit_price),
            "limited_by": self.limited_by,
            "brand": self.brand,
            "category": self.category,
            "marketplace": self.marketplace,
            "expected_days": self.expected_days,
            "annualized_return": (
                None if self.annualized_return is None else str(self.annualized_return)
            ),
            "velocity_basis": self.velocity_basis,
        }


@dataclass
class Excluded:
    """A candidate that was ranked and funded nothing, and what stopped it."""

    opportunity_id: str
    title: str
    reason_code: str
    detail: str
    unit_cost: Decimal | None = None
    return_per_dollar: Decimal | None = None
    #: What one unit would have cost. Present only where money was the binding
    #: constraint, because that is the only case where more capital would help.
    capital_needed: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "title": self.title,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "unit_cost": None if self.unit_cost is None else str(self.unit_cost),
            "return_per_dollar": (
                None if self.return_per_dollar is None else str(self.return_per_dollar)
            ),
            "capital_needed": (
                None if self.capital_needed is None else str(self.capital_needed)
            ),
        }


@dataclass
class Concentration:
    """What the portfolio would hold in one brand, category or marketplace."""

    dimension: str
    key: str
    held: Decimal
    planned: Decimal
    cap: Decimal

    @property
    def total(self) -> Decimal:
        return money(self.held + self.planned)

    @property
    def share_of_cap(self) -> Decimal | None:
        return pct_of(self.total, self.cap)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "key": self.key,
            "held": str(self.held),
            "planned": str(self.planned),
            "total": str(self.total),
            "cap": str(self.cap),
            "share_of_cap": None if self.share_of_cap is None else str(self.share_of_cap),
        }


@dataclass
class AllocationPlan:
    """What to buy, what it leaves out, and what the leaving out cost."""

    policy_version: str
    execution_mode: ExecutionMode
    budget: Decimal
    already_deployed: Decimal
    lines: list[Line] = field(default_factory=list)
    excluded: list[Excluded] = field(default_factory=list)
    concentration: list[Concentration] = field(default_factory=list)
    candidates_considered: int = 0
    ineligible: dict[str, int] = field(default_factory=dict)
    may_commit: bool = False
    blocked_reason: str | None = None
    #: What the closed positions say about how long capital stays tied up. The
    #: plan holds it rather than a copy of its numbers, so the basis of the
    #: ranking and the evidence for it can never drift apart.
    velocity: VelocityReport = field(default_factory=VelocityReport)

    @property
    def ranked_on_time(self) -> bool:
        return self.velocity.measured

    @property
    def ranking_explanation(self) -> str:
        if not self.ranked_on_time:
            return (
                "Expected profit per dollar of capital, highest first. Capital is the "
                "scarce resource, so the ranking maximises what the budget earns rather "
                "than what any one position earns. Time is not in this ranking: no "
                "position has closed yet, so there is no measured holding period, and "
                "one has not been invented to stand in for it. Ties break on score, then "
                "on the lower risk level."
            )
        median = self.velocity.portfolio_median_days
        return (
            "Expected profit per year of capital tied up, highest first. A 20% return in "
            "ten days is worth more than a 30% return in ninety, and the holding period "
            f"is now measured rather than assumed: {self.velocity.sample} closed "
            f"position(s), {median} day(s) at the median. A candidate whose own segment "
            "has enough closed positions uses that segment's figure; the rest use the "
            "portfolio median, and each line says which. Ties break on score, then on "
            "the lower risk level."
        )

    @property
    def capital_allocated(self) -> Decimal:
        return money(sum((line.capital for line in self.lines), Decimal("0")))

    @property
    def expected_profit(self) -> Decimal:
        return money(sum((line.expected_profit for line in self.lines), Decimal("0")))

    @property
    def unallocated(self) -> Decimal:
        return money(self.budget - self.capital_allocated)

    @property
    def expected_return(self) -> Decimal | None:
        return pct_of(self.expected_profit, self.capital_allocated)

    @property
    def next_unfunded(self) -> Excluded | None:
        """The best candidate that only money stopped.

        The marginal value of capital, which is the number a person needs when
        deciding whether to authorise more of it. Candidates stopped by an
        exposure limit are deliberately not reported here: more money would not
        buy them, and presenting them as if it would is an argument for raising
        a limit dressed up as an arithmetic result.
        """
        fundable = [
            item
            for item in self.excluded
            if item.reason_code == BUDGET_EXHAUSTED and item.capital_needed is not None
        ]
        return fundable[0] if fundable else None

    @property
    def summary(self) -> str:
        if not self.lines:
            if not self.candidates_considered:
                return (
                    "Nothing to allocate: no candidate in the catalogue is eligible for "
                    "autonomous capital under this policy."
                )
            return (
                f"Nothing funded. {self.candidates_considered} candidate(s) cleared "
                "eligibility and every one of them was stopped by a limit. The whole "
                f"{display_currency(self.budget)} stays uncommitted."
            )
        parts = [
            f"{len(self.lines)} position(s) proposed, "
            f"{display_currency(self.capital_allocated)} of "
            f"{display_currency(self.budget)} committed",
        ]
        expected = self.expected_return
        if expected is not None:
            parts.append(
                f"for {display_currency(self.expected_profit)} expected ({expected:.1%})."
            )
        else:
            parts.append(f"for {display_currency(self.expected_profit)} expected.")
        if self.unallocated > 0:
            parts.append(
                f"{display_currency(self.unallocated)} is deliberately left uncommitted: "
                "nothing else cleared both the policy and the portfolio limits."
            )
        return " ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        next_unfunded = self.next_unfunded
        return {
            "policy_version": self.policy_version,
            "execution_mode": self.execution_mode.value,
            "summary": self.summary,
            "may_commit": self.may_commit,
            "blocked_reason": self.blocked_reason,
            "capital": {
                "budget": str(self.budget),
                "already_deployed": str(self.already_deployed),
                "allocated": str(self.capital_allocated),
                "unallocated": str(self.unallocated),
                "expected_profit": str(self.expected_profit),
                "expected_return": (
                    None if self.expected_return is None else str(self.expected_return)
                ),
            },
            "lines": [line.as_dict() for line in self.lines],
            "excluded": [item.as_dict() for item in self.excluded],
            "concentration": [entry.as_dict() for entry in self.concentration],
            "considered": {
                "eligible": self.candidates_considered,
                "ineligible": self.ineligible,
            },
            "marginal_capital": None if next_unfunded is None else next_unfunded.as_dict(),
            "ranking": self.ranking_explanation,
            "ranked_on_time": self.ranked_on_time,
            "velocity": self.velocity.as_dict(),
            "note": (
                "A proposal. Nothing is bought and no position is opened until this is "
                "committed, and committing runs every line back through the decision "
                "engine, which re-checks the emergency stop, the circuit breakers and "
                "eligibility. Placing the retail order remains a human action."
            ),
        }


# ------------------------------------------------------------------ gather


def _title(session: Session, opportunity: Opportunity) -> tuple[str, str | None, str | None]:
    """The product's title, brand and category.

    Brand and category live on the product rather than on the opportunity
    because the exposure limits are about what the capital is concentrated in,
    and that is a property of the thing being bought.
    """
    product = (
        session.get(Product, opportunity.product_id) if opportunity.product_id else None
    )
    if product is None:
        return opportunity.id, None, None
    return product.title, product.brand, product.category


def _ceiling(session: Session, opportunity: Opportunity, unit_cost: Decimal) -> Decimal:
    """The most that may be paid per unit, from the stored profitability."""
    snapshot = session.scalars(
        select(ProfitabilitySnapshot)
        .where(ProfitabilitySnapshot.opportunity_id == opportunity.id)
        .order_by(ProfitabilitySnapshot.created_at.desc())
        .limit(1)
    ).first()
    if snapshot is not None and snapshot.max_acquisition_cost is not None:
        return money(snapshot.max_acquisition_cost)
    return unit_cost


def _held_opportunities(session: Session, auth: AuthContext) -> set[str]:
    """Opportunities that already have an open position.

    Buying the same thing twice because the first position is not closed yet is
    not diversification, and the per-opportunity path never had to notice.
    """
    rows = session.scalars(
        select(CapitalPosition.opportunity_id).where(
            CapitalPosition.organization_id == auth.organization_id,
            CapitalPosition.status == PositionStatus.OPEN.value,
            CapitalPosition.opportunity_id.is_not(None),
        )
    )
    return {row for row in rows if row}


def _candidates(
    session: Session, auth: AuthContext, policy: PolicyView, velocity: VelocityReport
) -> tuple[list[Candidate], list[Excluded], dict[str, int]]:
    """Everything eligible, ranked, plus what was refused before ranking."""
    rows = list(
        session.scalars(
            select(Opportunity)
            .where(
                Opportunity.organization_id == auth.organization_id,
                Opportunity.status.not_in(
                    [
                        "purchased",
                        "listed",
                        "sold",
                        "closed",
                        "rejected",
                    ]
                ),
            )
            .order_by(Opportunity.roi.desc().nullslast())
            .limit(MAX_CANDIDATES)
        )
    )

    held = _held_opportunities(session, auth)
    candidates: list[Candidate] = []
    excluded: list[Excluded] = []
    ineligible: dict[str, int] = {}

    for row in rows:
        checks = eligibility.assess(row, policy)
        if not checks.eligible:
            code = checks.reason_code or NOT_ELIGIBLE
            ineligible[code] = ineligible.get(code, 0) + 1
            continue

        title, brand, category = _title(session, row)
        if row.id in held:
            excluded.append(
                Excluded(
                    opportunity_id=row.id,
                    title=title,
                    reason_code=ALREADY_HELD,
                    detail=(
                        "An open position on this opportunity is already held. Adding to "
                        "it is a separate decision from opening it."
                    ),
                )
            )
            continue

        unit_cost = money(row.acquisition_cost or Decimal("0"))
        unit_profit = money(row.net_profit or Decimal("0"))
        if unit_cost <= 0:
            excluded.append(
                Excluded(
                    opportunity_id=row.id,
                    title=title,
                    reason_code=NO_ECONOMICS,
                    detail="No acquisition cost is stored, so no position can be sized.",
                )
            )
            continue

        per_dollar = row.roi if row.roi is not None else pct_of(unit_profit, unit_cost)
        holding = velocity.expected_days(category=category, brand=brand)
        candidates.append(
            Candidate(
                opportunity=row,
                title=title,
                unit_cost=unit_cost,
                unit_profit=unit_profit,
                return_per_dollar=per_dollar or Decimal("0"),
                max_unit_price=_ceiling(session, row, unit_cost),
                brand=brand,
                category=category,
                marketplace=row.target_marketplace,
                expected_days=None if holding is None else holding[0],
                velocity_basis=None if holding is None else holding[1],
            )
        )

    candidates.sort(key=lambda item: item.sort_key)
    return candidates, excluded, ineligible


# ------------------------------------------------------------------ plan


def plan(
    session: Session,
    auth: AuthContext,
    policy: PolicyView,
    *,
    budget: Decimal | None = None,
) -> AllocationPlan:
    """Decide what to buy with the capital available, across every candidate.

    The walk is greedy on profit per dollar and continues past a candidate it
    cannot afford, so a cheaper line further down still gets funded out of what
    is left. That backfill is why the plan usually commits more of the budget
    than a strict "stop at the first refusal" pass would.
    """
    already = positions.deployed_capital(session, auth, execution_mode=policy.execution_mode)
    ceiling = money(policy.capital_limit - already)
    requested = money(budget) if budget is not None else ceiling
    available = max(Decimal("0"), min(requested, ceiling))

    # Measured once for the whole plan rather than per candidate: the holding
    # period is a property of the history, and re-deriving it inside the loop
    # would be the same query eleven times.
    velocity = measure(session, auth, execution_mode=policy.execution_mode)
    candidates, excluded, ineligible = _candidates(session, auth, policy, velocity)
    result = AllocationPlan(
        policy_version=policy.version,
        execution_mode=policy.execution_mode,
        budget=available,
        already_deployed=already,
        excluded=excluded,
        candidates_considered=len(candidates),
        ineligible=ineligible,
        velocity=velocity,
    )
    result.may_commit, result.blocked_reason = _commit_readiness(policy, available)

    # --- the limits, as running totals -----------------------------------
    exposure = positions.exposure(session, auth, execution_mode=policy.execution_mode)
    caps = {
        "brand": money(policy.capital_limit * policy.maximum_brand_exposure),
        "category": money(policy.capital_limit * policy.maximum_category_exposure),
        "marketplace": money(policy.capital_limit * policy.maximum_marketplace_exposure),
    }
    planned: dict[str, dict[str, Decimal]] = {"brand": {}, "category": {}, "marketplace": {}}
    remaining = available
    position_cap = policy.position_cap(policy.capital_limit)
    daily_remaining: Decimal | None = None
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
        daily_remaining = max(Decimal("0"), money(policy.max_daily_deployment - opened_today))

    rank = 0
    for candidate in candidates:
        rank += 1
        limits: list[tuple[str, Decimal, str]] = [
            ("budget", remaining, "capital left in this plan"),
            ("position_cap", position_cap, "maximum share of capital allowed in one position"),
        ]
        if daily_remaining is not None:
            limits.append(
                ("daily_deployment", daily_remaining, "capital already committed today")
            )
        if policy.max_loss_per_position is not None:
            # The honest worst case on one position is the whole position: the
            # unit could be unsellable. Deliberately conservative rather than
            # optimistic, and the same assumption the single-decision path makes.
            limits.append(
                (
                    "max_loss_per_position",
                    money(policy.max_loss_per_position),
                    "maximum loss allowed on one position",
                )
            )

        binding = min(limits, key=lambda item: item[1])
        allowance = binding[1]
        quantity = int(allowance // candidate.unit_cost)

        # --- exposure, against held plus everything funded above ----------
        exposure_block: tuple[str, Decimal, str] | None = None
        for dimension, key in (
            ("brand", candidate.brand),
            ("category", candidate.category),
            ("marketplace", candidate.marketplace),
        ):
            if not key:
                continue
            cap = caps[dimension]
            used = money(
                exposure[dimension].get(key, Decimal("0"))
                + planned[dimension].get(key, Decimal("0"))
            )
            headroom = money(cap - used)
            affordable = max(0, int(headroom // candidate.unit_cost))
            if affordable < quantity:
                quantity = affordable
                exposure_block = (dimension, headroom, key)

        if quantity <= 0:
            excluded.append(_refused(candidate, binding, exposure_block, remaining))
            continue

        capital = money(candidate.unit_cost * quantity)
        limited_by = exposure_block[0] if exposure_block is not None else binding[0]
        result.lines.append(
            Line(
                rank=rank,
                opportunity_id=candidate.id,
                title=candidate.title,
                quantity=quantity,
                unit_cost=candidate.unit_cost,
                capital=capital,
                expected_profit=money(candidate.unit_profit * quantity),
                return_per_dollar=candidate.return_per_dollar,
                max_unit_price=candidate.max_unit_price,
                limited_by=limited_by,
                brand=candidate.brand,
                category=candidate.category,
                marketplace=candidate.marketplace,
                expected_days=candidate.expected_days,
                annualized_return=candidate.annualized_return,
                velocity_basis=candidate.velocity_basis,
            )
        )

        remaining = money(remaining - capital)
        if daily_remaining is not None:
            daily_remaining = money(daily_remaining - capital)
        for dimension, key in (
            ("brand", candidate.brand),
            ("category", candidate.category),
            ("marketplace", candidate.marketplace),
        ):
            if key:
                planned[dimension][key] = money(
                    planned[dimension].get(key, Decimal("0")) + capital
                )

    result.concentration = _concentration(exposure, planned, caps)
    return result


def _refused(
    candidate: Candidate,
    binding: tuple[str, Decimal, str],
    exposure_block: tuple[str, Decimal, str] | None,
    remaining: Decimal,
) -> Excluded:
    """Why one ranked candidate funded nothing.

    An exposure limit wins the explanation over the budget when both bind: "you
    already hold too much of this brand" is actionable and "there was no money
    left" is not, and the exposure limit would still have refused it at twice
    the budget.
    """
    if exposure_block is not None:
        dimension, headroom, key = exposure_block
        code = {
            "brand": BRAND_EXPOSURE_EXCEEDED,
            "category": CATEGORY_EXPOSURE_EXCEEDED,
            "marketplace": MARKETPLACE_EXPOSURE_EXCEEDED,
        }[dimension]
        return Excluded(
            opportunity_id=candidate.id,
            title=candidate.title,
            reason_code=code,
            detail=(
                f"{display_currency(headroom)} of room left in {dimension} '{key}', "
                f"against {display_currency(candidate.unit_cost)} for one unit."
            ),
            unit_cost=candidate.unit_cost,
            return_per_dollar=candidate.return_per_dollar,
        )

    code = {
        "budget": BUDGET_EXHAUSTED,
        "position_cap": POSITION_LIMIT_EXCEEDED,
        "daily_deployment": DAILY_DEPLOYMENT_EXCEEDED,
        "max_loss_per_position": MAX_LOSS_EXCEEDED,
    }[binding[0]]
    return Excluded(
        opportunity_id=candidate.id,
        title=candidate.title,
        reason_code=code,
        detail=(
            f"{display_currency(candidate.unit_cost)} for one unit against "
            f"{display_currency(binding[1])} allowed by the {binding[2]}."
        ),
        unit_cost=candidate.unit_cost,
        return_per_dollar=candidate.return_per_dollar,
        # Only money can be topped up. A position cap or an exposure limit is a
        # rule, and reporting it as a capital shortfall would invite raising the
        # wrong thing.
        capital_needed=(
            money(candidate.unit_cost - remaining) if binding[0] == "budget" else None
        ),
    )


def _concentration(
    exposure: dict[str, dict[str, Decimal]],
    planned: dict[str, dict[str, Decimal]],
    caps: dict[str, Decimal],
) -> list[Concentration]:
    """What the portfolio would hold in each bucket the plan touches.

    Ordered by the share of the cap used, so the limit closest to binding is the
    first one read.
    """
    entries: list[Concentration] = []
    for dimension, cap in caps.items():
        keys = set(exposure.get(dimension, {})) | set(planned.get(dimension, {}))
        for key in keys:
            entries.append(
                Concentration(
                    dimension=dimension,
                    key=key,
                    held=money(exposure.get(dimension, {}).get(key, Decimal("0"))),
                    planned=money(planned.get(dimension, {}).get(key, Decimal("0"))),
                    cap=cap,
                )
            )
    entries.sort(key=lambda entry: (-(entry.share_of_cap or Decimal("0")), entry.key))
    return entries


def _commit_readiness(policy: PolicyView, budget: Decimal) -> tuple[bool, str | None]:
    """Whether this plan could be acted on, and what stops it if not.

    The plan itself is worth producing at any autonomy level: "here is what it
    would do" is exactly the evidence a person needs before raising one. So the
    blocker is reported alongside the plan rather than instead of it.
    """
    if policy.emergency_stop_active:
        return False, f"Autonomous deployment is stopped: {policy.emergency_stop_reason}"
    if not policy.autonomy_level.deploys_capital:
        return False, (
            f"Autonomy level {int(policy.autonomy_level)} ({policy.autonomy_level.label}) "
            "does not deploy capital. This plan is a recommendation."
        )
    if policy.require_human_approval:
        return False, "The policy requires human approval before any position is opened."
    if policy.execution_mode is ExecutionMode.OBSERVE:
        return False, "Execution mode is observe: the system analyses but opens no positions."
    if budget <= 0:
        return False, "No capital is available under the policy limit."
    return True, None


# ------------------------------------------------------------------ commit


@dataclass
class CommitResult:
    """What actually happened when a plan was acted on."""

    plan_summary: str
    outcomes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def authorized(self) -> list[dict[str, Any]]:
        return [item for item in self.outcomes if item["outcome"] == engine.AUTHORIZED]

    @property
    def capital_committed(self) -> Decimal:
        return money(
            sum((Decimal(item["capital"]) for item in self.authorized), Decimal("0"))
        )

    @property
    def summary(self) -> str:
        if not self.outcomes:
            return "Nothing to commit: the plan funded no lines."
        refused = len(self.outcomes) - len(self.authorized)
        parts = [
            f"{len(self.authorized)} of {len(self.outcomes)} line(s) authorised, "
            f"{display_currency(self.capital_committed)} committed."
        ]
        if refused:
            parts.append(
                f"{refused} line(s) were refused by the decision engine on the way "
                "through, which is the engine doing its job rather than the plan "
                "failing: a plan is checked again at the moment it is acted on."
            )
        return " ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "plan_summary": self.plan_summary,
            "authorized": len(self.authorized),
            "refused": len(self.outcomes) - len(self.authorized),
            "capital_committed": str(self.capital_committed),
            "outcomes": self.outcomes,
        }


def commit(
    session: Session,
    auth: AuthContext,
    proposal: AllocationPlan,
    *,
    actor: str = "system",
) -> CommitResult:
    """Act on a plan by running every line through the decision engine.

    The allocator never opens a position itself. Each line is handed to
    ``engine.decide`` with its own capital as the ceiling, so the engine's gates
    all still apply and the engine can only authorise the line or less. A line
    refused here is recorded exactly as a single refused decision would be.
    """
    if not proposal.may_commit:
        raise ValueError(proposal.blocked_reason or "This plan cannot be committed.")

    result = CommitResult(plan_summary=proposal.summary)
    for line in proposal.lines:
        opportunity = session.get(Opportunity, line.opportunity_id)
        if opportunity is None:
            continue
        decision, row, position = engine.authorize(
            session,
            auth,
            opportunity,
            available_capital=line.capital,
        )
        result.outcomes.append(
            {
                "opportunity_id": line.opportunity_id,
                "title": line.title,
                "planned_quantity": line.quantity,
                "planned_capital": str(line.capital),
                "outcome": decision.outcome,
                "reason_code": decision.reason_code,
                "reason": decision.reason,
                "quantity": decision.quantity,
                "capital": str(decision.capital),
                "decision_id": None if row is None else row.id,
                "position_id": None if position is None else position.id,
            }
        )

    policy_module.record_event(
        session,
        auth,
        AutonomyEventType.PLAN_COMMITTED,
        message=result.summary,
        actor=actor,
        payload={
            "lines": len(proposal.lines),
            "authorized": len(result.authorized),
            "capital_committed": str(result.capital_committed),
            "policy_version": proposal.policy_version,
        },
    )
    session.flush()
    return result
