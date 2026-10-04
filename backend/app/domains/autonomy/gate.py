"""Whether Spreadline has earned the next level of autonomy.

Two rules govern this module, and they are the reason it exists separately from
everything else:

**The gate reports, it does not act.** Nothing here raises a limit, changes a
policy or unlocks a level. It answers "is the system eligible for level N, and
if not, what is missing". A person acts on the answer. A system that could
promote itself on its own performance report is not governed by a policy.

**A criterion with no sample is not met.** Not "met by default", not "assumed
pass". Eighty per cent precision over four decisions is not evidence of
anything, so the sample size is a criterion in its own right and every ratio is
checked against it.

The thresholds are configurable defaults, not permanent business rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.money import display_currency, money
from app.core.security import AuthContext
from app.domains.autonomy import scorecard as scorecard_module
from app.domains.autonomy.scorecard import Scorecard
from app.models.autonomy import AgentPerformanceSnapshot, AutonomyGateResult
from app.models.enums import AutonomyLevel


@dataclass(frozen=True)
class Criterion:
    """One requirement for a level.

    ``direction`` says which way the comparison runs: ``min`` for things that
    must be at least the threshold (precision, return), ``max`` for things that
    must be at most (loss rate, drawdown).
    """

    key: str
    label: str
    threshold: Decimal
    direction: str = "min"
    unit: str = "ratio"
    #: Ratios are only evidence once there is a sample behind them.
    requires_sample: int = 0


@dataclass(frozen=True)
class LevelRequirements:
    level: AutonomyLevel
    capital_limit: Decimal
    criteria: tuple[Criterion, ...]
    description: str


#: The default ladder. Each rung asks for more evidence than the last, and the
#: capital figures are the ones the operator authorises, not ones the system
#: assumes.
DEFAULT_LADDER: tuple[LevelRequirements, ...] = (
    LevelRequirements(
        level=AutonomyLevel.CONTROLLED,
        capital_limit=money("100"),
        description=(
            "A deliberately small first experiment. Enough to test matching, "
            "pricing, risk, allocation and turnover against real outcomes; too "
            "little to matter if every one of them is wrong."
        ),
        criteria=(
            Criterion("decisions_total", "Decisions recorded", Decimal("10"), unit="count"),
            Criterion(
                "shadow_positions_closed",
                "Shadow positions closed",
                Decimal("5"),
                unit="count",
            ),
        ),
    ),
    LevelRequirements(
        level=AutonomyLevel.LIMITED,
        capital_limit=money("500"),
        description="Unlocked only after the controlled experiment has produced results.",
        criteria=(
            Criterion("decisions_total", "Decisions recorded", Decimal("20"), unit="count"),
            Criterion("buy_precision", "Buy precision", Decimal("0.80"), requires_sample=20),
            Criterion("realized_roi", "Realised return", Decimal("0.15"), requires_sample=20),
            Criterion(
                "loss_rate", "Loss rate", Decimal("0.10"), direction="max", requires_sample=20
            ),
            Criterion(
                "max_drawdown",
                "Maximum drawdown",
                Decimal("0.25"),
                direction="max",
                requires_sample=20,
            ),
        ),
    ),
    LevelRequirements(
        level=AutonomyLevel.EXPANDED,
        capital_limit=money("2000"),
        description="Stable performance over a larger sample, across more than one category.",
        criteria=(
            Criterion("decisions_total", "Decisions recorded", Decimal("50"), unit="count"),
            Criterion("buy_precision", "Buy precision", Decimal("0.85"), requires_sample=50),
            Criterion("realized_roi", "Realised return", Decimal("0.18"), requires_sample=50),
            Criterion(
                "prediction_accuracy",
                "Prediction accuracy",
                Decimal("0.80"),
                requires_sample=20,
            ),
            Criterion(
                "loss_rate", "Loss rate", Decimal("0.10"), direction="max", requires_sample=50
            ),
        ),
    ),
    LevelRequirements(
        level=AutonomyLevel.PORTFOLIO,
        capital_limit=money("10000"),
        description="Multiple simultaneous positions, demonstrated turnover, controlled drawdown.",
        criteria=(
            Criterion("decisions_total", "Decisions recorded", Decimal("100"), unit="count"),
            Criterion("buy_precision", "Buy precision", Decimal("0.85"), requires_sample=100),
            Criterion("realized_roi", "Realised return", Decimal("0.18"), requires_sample=100),
            Criterion(
                "prediction_accuracy",
                "Prediction accuracy",
                Decimal("0.85"),
                requires_sample=50,
            ),
            Criterion("sell_through", "Sell-through", Decimal("0.80"), requires_sample=50),
            Criterion(
                "max_drawdown",
                "Maximum drawdown",
                Decimal("0.15"),
                direction="max",
                requires_sample=100,
            ),
        ),
    ),
)


@dataclass
class CriterionResult:
    criterion: Criterion
    actual: Decimal | None
    sample: int
    met: bool
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.criterion.key,
            "label": self.criterion.label,
            "required": str(self.criterion.threshold),
            "direction": self.criterion.direction,
            "unit": self.criterion.unit,
            "actual": None if self.actual is None else str(self.actual),
            "sample": self.sample,
            "met": self.met,
            "note": self.note,
        }


@dataclass
class GateEvaluation:
    target: LevelRequirements
    eligible: bool
    results: list[CriterionResult] = field(default_factory=list)

    @property
    def unmet(self) -> list[CriterionResult]:
        return [result for result in self.results if not result.met]

    @property
    def summary(self) -> str:
        level = self.target.level
        if self.eligible:
            return (
                f"Eligible for level {int(level)} ({level.label}), "
                f"{display_currency(self.target.capital_limit)}. "
                "Authorising it is a person's decision, not an automatic one."
            )
        first = self.unmet[0]
        extra = f" and {len(self.unmet) - 1} other requirement(s)" if len(self.unmet) > 1 else ""
        return f"Not yet eligible for level {int(level)} ({level.label}). {first.note}{extra}."

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_level": int(self.target.level),
            "target_level_label": self.target.level.label,
            "target_capital_limit": str(self.target.capital_limit),
            "description": self.target.description,
            "eligible": self.eligible,
            "summary": self.summary,
            "criteria": [result.as_dict() for result in self.results],
        }


def requirements_for(level: AutonomyLevel) -> LevelRequirements | None:
    return next((entry for entry in DEFAULT_LADDER if entry.level is level), None)


def next_level(current: AutonomyLevel) -> LevelRequirements | None:
    """The next rung above the current level, or None at the top of the ladder."""
    return next((entry for entry in DEFAULT_LADDER if int(entry.level) > int(current)), None)


def _actual(
    scorecard: Scorecard, key: str, extra: dict[str, Decimal]
) -> tuple[Decimal | None, int]:
    if key in extra:
        return extra[key], int(extra[key])
    metric = scorecard.get(key)
    if metric is None:
        return None, 0
    return metric.value, metric.sample


def evaluate(
    scorecard: Scorecard,
    target: LevelRequirements,
    *,
    extra_counts: dict[str, Decimal] | None = None,
) -> GateEvaluation:
    """Check the scorecard against one level's requirements."""
    extra = dict(extra_counts or {})
    extra.setdefault("decisions_total", Decimal(scorecard.decisions_total))

    results: list[CriterionResult] = []
    for criterion in target.criteria:
        actual, sample = _actual(scorecard, criterion.key, extra)

        if actual is None:
            results.append(
                CriterionResult(
                    criterion=criterion,
                    actual=None,
                    sample=sample,
                    met=False,
                    note=f"{criterion.label} has not been measured yet",
                )
            )
            continue

        if criterion.requires_sample and sample < criterion.requires_sample:
            results.append(
                CriterionResult(
                    criterion=criterion,
                    actual=actual,
                    sample=sample,
                    met=False,
                    note=(
                        f"{criterion.label} needs {criterion.requires_sample} closed "
                        f"position(s) behind it, and has {sample}"
                    ),
                )
            )
            continue

        met = (
            actual >= criterion.threshold
            if criterion.direction == "min"
            else actual <= criterion.threshold
        )
        comparison = "at least" if criterion.direction == "min" else "no more than"
        results.append(
            CriterionResult(
                criterion=criterion,
                actual=actual,
                sample=sample,
                met=met,
                note=(f"{criterion.label} is {actual}, needs {comparison} {criterion.threshold}"),
            )
        )

    return GateEvaluation(
        target=target,
        eligible=all(result.met for result in results),
        results=results,
    )


def evaluate_and_record(
    session: Session,
    auth: AuthContext,
    target: LevelRequirements,
    *,
    extra_counts: dict[str, Decimal] | None = None,
) -> tuple[GateEvaluation, AutonomyGateResult]:
    """Evaluate the gate and freeze the evidence it was evaluated on.

    The snapshot is stored because the case for granting a level has to survive
    the data moving on. Six weeks later "why was this authorised" must be
    answerable from the record, not recomputed from a database that has changed.
    """
    card = scorecard_module.build(session, auth)
    evaluation = evaluate(card, target, extra_counts=extra_counts)

    snapshot = AgentPerformanceSnapshot(
        organization_id=auth.organization_id,
        metrics=scorecard_module.snapshot_payload(card),
        decisions_total=card.decisions_total,
        decisions_autonomous=card.decisions_autonomous,
        note=f"Evaluated for level {int(target.level)}.",
    )
    session.add(snapshot)
    session.flush()

    row = AutonomyGateResult(
        organization_id=auth.organization_id,
        target_level=int(target.level),
        eligible=evaluation.eligible,
        criteria=[result.as_dict() for result in evaluation.results],
        summary=evaluation.summary,
        snapshot_id=snapshot.id,
    )
    session.add(row)
    session.flush()
    return evaluation, row
