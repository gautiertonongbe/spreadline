"""Whether an opportunity is eligible for autonomous capital at all.

This runs before allocation and is entirely deterministic. Nothing here reasons,
weighs or judges: each rule reads a stored value, compares it to the policy, and
returns a pass or a fail with the numbers on both sides.

The separation matters. Scoring ranks candidates against each other; eligibility
decides whether a candidate may be bought without a human. A candidate can score
94 and be ineligible, and when that happens the answer is REVIEW, never BUY.

Every rule is mandatory. There is no weighting and no majority: one failure ends
it. That is what makes the boundary auditable, and it is why the reason codes are
stable strings rather than prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.money import display_currency, display_score
from app.domains.autonomy.policy import PolicyView
from app.models.enums import RiskLevel
from app.models.opportunity import Opportunity

#: Stable machine-readable causes. Counted in reports and asserted on in tests,
#: so they are part of the contract rather than log text.
NOT_A_BUY = "NOT_A_BUY"
MATCH_CONFIDENCE_TOO_LOW = "MATCH_CONFIDENCE_TOO_LOW"
ROI_BELOW_MINIMUM = "ROI_BELOW_MINIMUM"
PROFIT_BELOW_MINIMUM = "PROFIT_BELOW_MINIMUM"
RISK_ABOVE_MAXIMUM = "RISK_ABOVE_MAXIMUM"
DATA_QUALITY_TOO_LOW = "DATA_QUALITY_TOO_LOW"
UNRESOLVED_BLOCKER = "UNRESOLVED_BLOCKER"
NO_ECONOMICS = "NO_ECONOMICS"
NOT_PURCHASABLE = "NOT_PURCHASABLE"


@dataclass
class Check:
    """One rule, with both sides of the comparison kept."""

    code: str
    label: str
    passed: bool
    required: str
    actual: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "passed": self.passed,
            "required": self.required,
            "actual": self.actual,
            "detail": self.detail,
        }


@dataclass
class EligibilityResult:
    eligible: bool
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.passed]

    @property
    def reason_code(self) -> str | None:
        return self.failures[0].code if self.failures else None

    @property
    def summary(self) -> str:
        """Why this is or is not eligible, with both sides of the comparison.

        Phrased from ``required`` and ``actual`` rather than from the label
        alone. A label names what was checked, and pasting it after "not
        eligible" produced sentences like "not eligible: return meets the
        minimum", which says the opposite of what happened.
        """
        if self.eligible:
            return f"Eligible: all {len(self.checks)} mandatory conditions met."
        first = self.failures[0]
        extra = (
            f", and {len(self.failures) - 1} other condition(s) also failed"
            if len(self.failures) > 1
            else ""
        )
        return (
            f"Not eligible. {first.label} needs {first.required}, "
            f"and this is {first.actual}{extra}."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason_code": self.reason_code,
            "summary": self.summary,
            "checks": [check.as_dict() for check in self.checks],
        }


def _pct(value: Decimal | None) -> str:
    return "not available" if value is None else f"{value:.1%}"


def assess(
    opportunity: Opportunity,
    policy: PolicyView,
    *,
    primary_blocker: str | None = None,
) -> EligibilityResult:
    """Check one stored opportunity against one policy version."""
    checks: list[Check] = []

    checks.append(
        Check(
            code=NOT_A_BUY,
            label="Recommendation",
            passed=opportunity.recommendation == "buy",
            required="buy",
            actual=str(opportunity.recommendation),
            detail=(
                "Only a candidate the decision engine called a buy is eligible for "
                "capital without a human."
            ),
        )
    )

    has_economics = opportunity.net_profit is not None and opportunity.acquisition_cost is not None
    checks.append(
        Check(
            code=NO_ECONOMICS,
            label="Economics",
            passed=has_economics,
            required="profit and cost present",
            actual="present" if has_economics else "missing",
            detail="Without stored economics there is nothing to size a position from.",
        )
    )

    confidence = opportunity.match_confidence
    checks.append(
        Check(
            code=MATCH_CONFIDENCE_TOO_LOW,
            label="Match confidence",
            passed=confidence is not None and confidence >= policy.minimum_match_confidence,
            required=_pct(policy.minimum_match_confidence),
            actual=_pct(confidence),
            detail=(
                "Buying against the wrong listing loses the whole position, not the "
                "margin, so this is the one threshold set highest."
            ),
        )
    )

    roi = opportunity.roi
    checks.append(
        Check(
            code=ROI_BELOW_MINIMUM,
            label="Return",
            passed=roi is not None and roi >= policy.minimum_roi,
            required=_pct(policy.minimum_roi),
            actual=_pct(roi),
            detail="A return below the policy minimum is not what the capital is for.",
        )
    )

    profit = opportunity.net_profit
    checks.append(
        Check(
            code=PROFIT_BELOW_MINIMUM,
            label="Profit per item",
            passed=profit is not None and profit >= policy.minimum_profit,
            required=display_currency(policy.minimum_profit),
            actual=display_currency(profit),
            detail="Per item, after every fee.",
        )
    )

    risk = RiskLevel(opportunity.risk_level)
    checks.append(
        Check(
            code=RISK_ABOVE_MAXIMUM,
            label="Risk level",
            passed=risk.rank <= policy.maximum_risk.rank,
            required=f"at or below {policy.maximum_risk.value}",
            actual=risk.value,
            detail="The overall risk level, driven by whichever category scored worst.",
        )
    )

    quality = opportunity.data_quality_score
    checks.append(
        Check(
            code=DATA_QUALITY_TOO_LOW,
            label="Data quality",
            passed=quality is not None and quality >= policy.minimum_data_quality,
            required=f"{display_score(policy.minimum_data_quality)}/100",
            actual="not available" if quality is None else f"{display_score(quality)}/100",
            detail=(
                "A decision made on thin evidence is a guess with a confident label, "
                "and an autonomous one has nobody to catch it."
            ),
        )
    )

    # A blocker is whatever the decision engine could not clear. It is reported
    # rather than re-derived, so eligibility and the recommendation cannot
    # disagree about what is wrong.
    checks.append(
        Check(
            code=UNRESOLVED_BLOCKER,
            label="Unresolved gates",
            passed=primary_blocker is None,
            required="no unresolved gate",
            actual=primary_blocker or "none",
            detail=primary_blocker or "",
        )
    )

    return EligibilityResult(
        eligible=all(check.passed for check in checks),
        checks=checks,
    )
