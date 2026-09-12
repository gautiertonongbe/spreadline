"""Capital allocation and stress testing."""

from __future__ import annotations

from decimal import Decimal

from app.domains.portfolio.capital import (
    AllocationCandidate,
    CapitalConstraints,
    allocate_capital,
)
from app.models.enums import RiskLevel


def candidate(
    identifier: str,
    *,
    cost: str = "20",
    profit: str = "8",
    roi: str = "0.40",
    risk: RiskLevel = RiskLevel.LOW,
    score: str = "80",
    units: int | None = 100,
    brand: str | None = None,
    category: str | None = None,
    recommendation: str = "buy",
) -> AllocationCandidate:
    return AllocationCandidate(
        opportunity_id=identifier,
        title=f"Product {identifier}",
        unit_cost=Decimal(cost),
        unit_profit=Decimal(profit),
        roi=Decimal(roi),
        risk_level=risk,
        score=Decimal(score),
        units_available=units,
        brand=brand,
        category=category,
        recommendation=recommendation,
    )


def constraints(**overrides) -> CapitalConstraints:
    defaults = {"available_capital": Decimal("10000")}
    defaults.update(overrides)
    return CapitalConstraints(**defaults)


class TestAllocation:
    def test_allocates_within_available_capital(self):
        plan = allocate_capital([candidate("a"), candidate("b")], constraints())
        assert plan.allocated_capital <= Decimal("10000")
        assert plan.allocated_capital + plan.unallocated_capital == Decimal("10000")

    def test_ranks_by_risk_adjusted_profit_per_dollar(self):
        """A high-risk dollar of profit is worth less than a low-risk one."""
        low = candidate("low", cost="20", profit="8", risk=RiskLevel.LOW)
        high = candidate("high", cost="20", profit="9", risk=RiskLevel.HIGH)
        plan = allocate_capital(
            [high, low], constraints(max_risk_level=RiskLevel.HIGH, max_positions=2)
        )
        assert plan.allocations[0].candidate.opportunity_id == "low"

    def test_position_size_cap_binds(self):
        plan = allocate_capital(
            [candidate("a", units=10_000)],
            constraints(max_position_pct=Decimal("0.10")),
        )
        assert plan.allocations[0].capital <= Decimal("1000")
        assert plan.allocations[0].limited_by == "maximum position size"

    def test_supply_caps_the_position(self):
        plan = allocate_capital([candidate("a", units=3)], constraints())
        assert plan.allocations[0].units == 3
        assert "units available" in plan.allocations[0].limited_by

    def test_unknown_supply_is_not_treated_as_infinite(self):
        plan = allocate_capital(
            [candidate("a", units=None)],
            constraints(default_units_when_unknown=5),
        )
        assert plan.allocations[0].units == 5
        assert "unknown supply" in plan.allocations[0].limited_by

    def test_risk_ceiling_excludes_with_a_reason(self):
        plan = allocate_capital(
            [candidate("a", risk=RiskLevel.HIGH)],
            constraints(max_risk_level=RiskLevel.MEDIUM),
        )
        assert not plan.allocations
        assert "exceeds" in plan.excluded[0].reason

    def test_min_roi_excludes_with_a_reason(self):
        plan = allocate_capital([candidate("a", roi="0.05")], constraints(min_roi=Decimal("0.20")))
        assert "below the 20% minimum" in plan.excluded[0].reason

    def test_undefined_roi_is_excluded_not_assumed(self):
        item = candidate("a")
        item.roi = None
        plan = allocate_capital([item], constraints())
        assert not plan.allocations
        assert "undefined" in plan.excluded[0].reason

    def test_only_buy_recommendations_by_default(self):
        plan = allocate_capital([candidate("a", recommendation="review")], constraints())
        assert not plan.allocations
        assert "REVIEW" in plan.excluded[0].reason

    def test_review_can_be_opted_in(self):
        plan = allocate_capital(
            [candidate("a", recommendation="review")],
            constraints(allowed_recommendations=("buy", "review")),
        )
        assert plan.allocations

    def test_brand_concentration_is_capped(self):
        items = [candidate(f"a{i}", brand="Acme", units=10_000, cost="100") for i in range(5)]
        plan = allocate_capital(
            items, constraints(max_brand_pct=Decimal("0.30"), max_position_pct=Decimal("1.0"))
        )
        acme_total = sum(item.capital for item in plan.allocations)
        assert acme_total <= Decimal("3000")

    def test_category_concentration_is_capped(self):
        items = [
            candidate(f"a{i}", category="Toys", brand=f"B{i}", units=10_000, cost="100")
            for i in range(5)
        ]
        plan = allocate_capital(
            items,
            constraints(max_category_pct=Decimal("0.40"), max_position_pct=Decimal("1.0")),
        )
        assert sum(item.capital for item in plan.allocations) <= Decimal("4000")

    def test_position_count_limit(self):
        items = [candidate(f"a{i}", units=1, cost="60") for i in range(10)]
        plan = allocate_capital(items, constraints(max_positions=3))
        assert len(plan.allocations) == 3
        assert any("Position limit" in item.reason for item in plan.excluded)

    def test_tiny_positions_are_rejected(self):
        plan = allocate_capital(
            [candidate("a", cost="5", units=2)], constraints(min_position_size=Decimal("50"))
        )
        assert not plan.allocations
        assert "below the" in plan.excluded[0].reason

    def test_expected_profit_and_roi_are_reported(self):
        plan = allocate_capital([candidate("a", units=10, cost="100", profit="40")], constraints())
        assert plan.expected_profit == Decimal("400.0000")
        assert plan.expected_roi == Decimal("0.4")

    def test_empty_pool_says_so(self):
        plan = allocate_capital([], constraints())
        assert plan.allocated_capital == Decimal("0")
        assert "No candidate" in plan.notes[0]

    def test_allocation_is_deterministic(self):
        items = [candidate(f"a{i}", units=5) for i in range(6)]
        first = allocate_capital(items, constraints()).as_dict()
        second = allocate_capital(items, constraints()).as_dict()
        assert first == second

    def test_every_exclusion_carries_a_reason(self):
        items = [
            candidate("a", risk=RiskLevel.CRITICAL),
            candidate("b", roi="0.01"),
            candidate("c", score="10"),
            candidate("d", profit="-1"),
        ]
        plan = allocate_capital(items, constraints())
        assert len(plan.excluded) == 4
        assert all(item.reason for item in plan.excluded)
