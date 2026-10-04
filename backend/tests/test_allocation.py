"""Choosing between opportunities rather than checking them one at a time.

The property every test here defends: a plan is decided across the whole slate.
Sequential decisions give the capital to whichever candidate was looked at
first; these assert that the best one wins, that the limits move as lines are
funded, and that leaving capital uncommitted is a result rather than a failure.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domains.autonomy import allocation, positions
from app.domains.autonomy import policy as policy_module
from app.models.catalog import MarketplaceListing, Product
from app.models.enums import ExecutionMode
from app.models.opportunity import Opportunity

API = "/api/v1"


def build_candidate(
    session,
    auth,
    *,
    title,
    cost,
    profit,
    brand="Acme",
    category="electronics",
    marketplace="amazon",
    risk="low",
    score="80",
):
    """One stored opportunity with economics that clear a default policy.

    Built directly rather than through the pipeline: these tests are about how
    candidates are chosen against each other, and the pipeline produces one set
    of economics rather than the spread of them the ranking exists to order.
    """
    product = Product(
        organization_id=auth.organization_id,
        title=title,
        brand=brand,
        category=category,
    )
    session.add(product)
    session.flush()

    listings = {}
    for role, place in (("source", "walmart"), ("target", marketplace)):
        listing = MarketplaceListing(
            organization_id=auth.organization_id,
            product_id=product.id,
            marketplace=place,
            external_id=f"{role}-{product.id[:8]}",
            title=title,
            current_price=Decimal(str(cost)),
        )
        session.add(listing)
        session.flush()
        listings[role] = listing

    cost_value = Decimal(str(cost))
    profit_value = Decimal(str(profit))
    opportunity = Opportunity(
        organization_id=auth.organization_id,
        product_id=product.id,
        source_listing_id=listings["source"].id,
        target_listing_id=listings["target"].id,
        direction=f"walmart_to_{marketplace}",
        source_marketplace="walmart",
        target_marketplace=marketplace,
        recommendation="buy",
        status="new",
        risk_level=risk,
        score=Decimal(str(score)),
        match_confidence=Decimal("0.99"),
        data_quality_score=Decimal("90"),
        acquisition_cost=cost_value,
        expected_sale_price=cost_value + profit_value,
        net_profit=profit_value,
        roi=(profit_value / cost_value).quantize(Decimal("0.0001")),
    )
    session.add(opportunity)
    session.flush()
    return opportunity


def policy_for(session, auth, **changes):
    base = {
        "autonomy_level": 3,
        "capital_limit": Decimal("1000"),
        "execution_mode": "shadow",
        "require_human_approval": False,
        "minimum_roi": Decimal("0.10"),
        "minimum_profit": Decimal("1"),
        "max_position_pct": Decimal("0.25"),
        "maximum_brand_exposure": Decimal("1"),
        "maximum_category_exposure": Decimal("1"),
        "maximum_marketplace_exposure": Decimal("1"),
    }
    base.update(changes)
    policy_module.update_policy(session, auth, base)
    return policy_module.view(policy_module.active_policy(session, auth))


class TestRanking:
    def test_the_best_return_is_funded_first_regardless_of_insertion_order(
        self, session, auth
    ):
        """The whole point. Sequential decisions fund whatever came first.

        The position cap is what leaves room for a second line at all: without
        one, concentrating the whole budget in the best return is the correct
        answer, and the policy is the only thing that says otherwise.
        """
        build_candidate(session, auth, title="Thin", cost=10, profit=2, brand="A")
        build_candidate(session, auth, title="Fat", cost=10, profit=8, brand="B")
        build_candidate(session, auth, title="Middling", cost=10, profit=5, brand="C")
        # Cap of 25% of a $100 limit is $25, so two units of anything at $10.
        policy = policy_for(session, auth, capital_limit=Decimal("100"))

        plan = allocation.plan(session, auth, policy, budget=Decimal("30"))
        assert [line.title for line in plan.lines] == ["Fat", "Middling"]
        assert plan.lines[0].rank == 1
        assert plan.lines[0].return_per_dollar > plan.lines[1].return_per_dollar
        assert plan.lines[0].quantity == 2, "the position cap bound before the budget"

    def test_a_tie_on_return_breaks_on_score_then_risk(self, session, auth):
        build_candidate(session, auth, title="Lower score", cost=10, profit=3, score="50")
        build_candidate(session, auth, title="Higher score", cost=10, profit=3, score="90")
        policy = policy_for(session, auth, capital_limit=Decimal("100"))

        plan = allocation.plan(session, auth, policy, budget=Decimal("100"))
        assert [line.title for line in plan.lines][:2] == ["Higher score", "Lower score"]

    def test_the_same_data_produces_the_same_plan(self, session, auth):
        """A plan that reorders between two reads cannot be reviewed."""
        for index in range(4):
            build_candidate(session, auth, title=f"Item {index}", cost=10, profit=3)
        policy = policy_for(session, auth)

        first = allocation.plan(session, auth, policy, budget=Decimal("25"))
        second = allocation.plan(session, auth, policy, budget=Decimal("25"))
        assert [line.opportunity_id for line in first.lines] == [
            line.opportunity_id for line in second.lines
        ]


class TestJointLimits:
    def test_two_lines_cannot_each_be_within_the_brand_limit_and_together_over_it(
        self, session, auth
    ):
        """The defect the per-opportunity path cannot see.

        Checked one at a time against the database, both positions pass: neither
        exists yet. The limit only works if the plan counts what it has already
        funded.
        """
        build_candidate(session, auth, title="First", cost=40, profit=20, brand="Acme")
        build_candidate(session, auth, title="Second", cost=40, profit=10, brand="Acme")
        policy = policy_for(
            session,
            auth,
            capital_limit=Decimal("200"),
            maximum_brand_exposure=Decimal("0.25"),  # $50 of $200
        )

        plan = allocation.plan(session, auth, policy)
        brand_capital = sum(
            (line.capital for line in plan.lines if line.brand == "Acme"), Decimal("0")
        )
        assert brand_capital <= Decimal("50")
        assert len(plan.lines) == 1, "the second line would have breached the brand limit"
        refused = [item for item in plan.excluded if item.title == "Second"]
        assert refused and refused[0].reason_code == allocation.BRAND_EXPOSURE_EXCEEDED

    def test_the_budget_falls_as_lines_are_funded(self, session, auth):
        build_candidate(session, auth, title="A", cost=30, profit=15, brand="A")
        build_candidate(session, auth, title="B", cost=30, profit=12, brand="B")
        build_candidate(session, auth, title="C", cost=30, profit=9, brand="C")
        policy = policy_for(session, auth, max_position_size=Decimal("30"))

        plan = allocation.plan(session, auth, policy, budget=Decimal("70"))
        assert plan.capital_allocated <= Decimal("70")
        assert plan.capital_allocated + plan.unallocated == plan.budget

    def test_capital_already_deployed_shrinks_the_budget(self, session, auth):
        opportunity = build_candidate(session, auth, title="Held", cost=20, profit=10)
        policy = policy_for(session, auth, capital_limit=Decimal("100"))
        positions.open_position(
            session,
            auth,
            opportunity_id=opportunity.id,
            product_id=opportunity.product_id,
            brand="Acme",
            quantity=2,
            unit_cost=Decimal("20"),
            execution_mode=ExecutionMode.SHADOW,
        )
        session.flush()

        plan = allocation.plan(session, auth, policy)
        assert plan.already_deployed == Decimal("40.0000")
        assert plan.budget == Decimal("60.0000")

    def test_an_opportunity_already_held_is_not_bought_again(self, session, auth):
        opportunity = build_candidate(session, auth, title="Held", cost=20, profit=10)
        policy = policy_for(session, auth)
        positions.open_position(
            session,
            auth,
            opportunity_id=opportunity.id,
            product_id=opportunity.product_id,
            brand="Acme",
            quantity=1,
            unit_cost=Decimal("20"),
            execution_mode=ExecutionMode.SHADOW,
        )
        session.flush()

        plan = allocation.plan(session, auth, policy)
        assert [line.title for line in plan.lines] == []
        assert any(item.reason_code == allocation.ALREADY_HELD for item in plan.excluded)

    def test_the_marketplace_limit_is_enforced(self, session, auth):
        """A policy field that changes nothing is worse than no field."""
        build_candidate(
            session, auth, title="One", cost=40, profit=20, brand="A", marketplace="amazon"
        )
        build_candidate(
            session, auth, title="Two", cost=40, profit=10, brand="B", marketplace="amazon"
        )
        policy = policy_for(
            session,
            auth,
            capital_limit=Decimal("200"),
            maximum_marketplace_exposure=Decimal("0.25"),
        )

        plan = allocation.plan(session, auth, policy)
        assert len(plan.lines) == 1
        assert any(
            item.reason_code == allocation.MARKETPLACE_EXPOSURE_EXCEEDED
            for item in plan.excluded
        )


class TestDoingNothing:
    def test_capital_is_never_forced_out_of_the_door(self, session, auth):
        """Leaving money uncommitted is a decision, and it is reported as one."""
        build_candidate(session, auth, title="Only one", cost=10, profit=5)
        policy = policy_for(session, auth, capital_limit=Decimal("1000"))

        plan = allocation.plan(session, auth, policy, budget=Decimal("500"))
        assert plan.unallocated > 0
        assert "left uncommitted" in plan.summary

    def test_an_empty_slate_says_why_rather_than_returning_nothing(self, session, auth):
        policy = policy_for(session, auth)
        plan = allocation.plan(session, auth, policy, budget=Decimal("100"))
        assert plan.lines == []
        assert "no candidate" in plan.summary.lower()

    def test_ineligible_candidates_are_counted_by_cause(self, session, auth):
        build_candidate(session, auth, title="Too thin", cost=100, profit=1)
        policy = policy_for(session, auth, minimum_roi=Decimal("0.50"))

        plan = allocation.plan(session, auth, policy)
        assert plan.lines == []
        assert plan.ineligible, "a refusal before ranking is still reported"
        assert sum(plan.ineligible.values()) >= 1


class TestMarginalCapital:
    def test_the_next_unfunded_candidate_is_named_with_what_it_would_cost(
        self, session, auth
    ):
        """The number a person needs before authorising more capital."""
        build_candidate(session, auth, title="Funded", cost=40, profit=20, brand="A")
        build_candidate(session, auth, title="Missed", cost=40, profit=12, brand="B")
        policy = policy_for(session, auth, capital_limit=Decimal("1000"))

        plan = allocation.plan(session, auth, policy, budget=Decimal("50"))
        nxt = plan.next_unfunded
        assert nxt is not None and nxt.title == "Missed"
        assert nxt.capital_needed == Decimal("30.0000"), "40 needed against 10 left"

    def test_a_candidate_stopped_by_a_rule_is_not_reported_as_needing_money(
        self, session, auth
    ):
        """More capital would not buy it, and saying so argues for the wrong fix."""
        build_candidate(session, auth, title="First", cost=40, profit=20, brand="Acme")
        build_candidate(session, auth, title="Second", cost=40, profit=10, brand="Acme")
        policy = policy_for(
            session,
            auth,
            capital_limit=Decimal("1000"),
            maximum_brand_exposure=Decimal("0.05"),  # $50
        )

        plan = allocation.plan(session, auth, policy, budget=Decimal("900"))
        assert plan.next_unfunded is None
        blocked = [item for item in plan.excluded if item.title == "Second"]
        assert blocked and blocked[0].capital_needed is None


class TestConcentration:
    def test_the_plan_reports_what_the_portfolio_would_hold(self, session, auth):
        build_candidate(session, auth, title="A", cost=30, profit=15, brand="Acme")
        policy = policy_for(
            session, auth, capital_limit=Decimal("300"), maximum_brand_exposure=Decimal("0.5")
        )

        plan = allocation.plan(session, auth, policy, budget=Decimal("60"))
        brands = [entry for entry in plan.concentration if entry.dimension == "brand"]
        assert brands
        entry = brands[0]
        assert entry.key == "Acme"
        assert entry.total == plan.capital_allocated
        assert entry.share_of_cap is not None


class TestCommit:
    def test_a_plan_is_proposed_before_anything_is_opened(self, session, auth):
        build_candidate(session, auth, title="A", cost=20, profit=10)
        policy = policy_for(session, auth)

        allocation.plan(session, auth, policy, budget=Decimal("100"))
        assert positions.deployed_capital(session, auth) == Decimal("0.0000")

    def test_committing_opens_positions_through_the_decision_engine(self, session, auth):
        build_candidate(session, auth, title="A", cost=20, profit=10, brand="A")
        build_candidate(session, auth, title="B", cost=20, profit=8, brand="B")
        policy = policy_for(session, auth, capital_limit=Decimal("100"))

        plan = allocation.plan(session, auth, policy, budget=Decimal("60"))
        assert plan.may_commit
        result = allocation.commit(session, auth, plan)

        assert result.authorized, result.summary
        assert result.capital_committed > 0
        deployed = positions.deployed_capital(
            session, auth, execution_mode=ExecutionMode.SHADOW
        )
        assert deployed == result.capital_committed

    def test_a_committed_line_never_exceeds_what_the_plan_proposed(self, session, auth):
        build_candidate(session, auth, title="A", cost=20, profit=10)
        policy = policy_for(session, auth, capital_limit=Decimal("500"))

        plan = allocation.plan(session, auth, policy, budget=Decimal("60"))
        result = allocation.commit(session, auth, plan)
        for outcome in result.outcomes:
            assert Decimal(outcome["capital"]) <= Decimal(outcome["planned_capital"])

    def test_a_plan_that_cannot_be_committed_is_still_produced(self, session, auth):
        """"Here is what it would do" is the evidence for raising a level."""
        build_candidate(session, auth, title="A", cost=20, profit=10)
        policy = policy_for(session, auth, autonomy_level=1, require_human_approval=True)

        plan = allocation.plan(session, auth, policy, budget=Decimal("100"))
        assert plan.lines, "the plan is still worth seeing"
        assert not plan.may_commit
        assert plan.blocked_reason

        with pytest.raises(ValueError):
            allocation.commit(session, auth, plan)

    def test_the_emergency_stop_blocks_a_commit(self, session, auth):
        build_candidate(session, auth, title="A", cost=20, profit=10)
        policy = policy_for(session, auth)
        policy_module.set_emergency_stop(session, auth, active=True, reason="testing")
        policy = policy_module.view(policy_module.active_policy(session, auth))

        plan = allocation.plan(session, auth, policy, budget=Decimal("100"))
        assert not plan.may_commit
        assert "stopped" in (plan.blocked_reason or "")


class TestApi:
    def _enable(self, client):
        response = client.post(
            f"{API}/autonomy/enable",
            json={
                "level": 3,
                "capital_limit": "200",
                "execution_mode": "shadow",
                "acknowledge_not_eligible": True,
            },
        )
        assert response.status_code == 200, response.text

    def test_the_plan_endpoint_ranks_and_explains(self, client, session, auth):
        build_candidate(session, auth, title="A", cost=20, profit=10, brand="A")
        session.commit()
        self._enable(client)

        body = client.get(f"{API}/autonomy/allocation").json()
        assert "lines" in body
        assert body["ranking"]
        assert body["note"]
        assert body["capital"]["budget"]

    def test_reading_a_plan_opens_nothing(self, client, session, auth):
        build_candidate(session, auth, title="A", cost=20, profit=10)
        session.commit()
        self._enable(client)

        client.get(f"{API}/autonomy/allocation")
        positions_body = client.get(f"{API}/autonomy/positions").json()
        assert positions_body["total"] == 0

    def test_a_commit_refuses_a_plan_that_has_changed(self, client, session, auth):
        build_candidate(session, auth, title="A", cost=20, profit=10)
        session.commit()
        self._enable(client)

        response = client.post(
            f"{API}/autonomy/allocation/commit", json={"expect_capital": "999999"}
        )
        assert response.status_code == 422
        assert "changed" in response.text

    def test_a_commit_opens_positions_and_records_the_event(self, client, session, auth):
        build_candidate(session, auth, title="A", cost=20, profit=10)
        session.commit()
        self._enable(client)

        body = client.post(f"{API}/autonomy/allocation/commit", json={}).json()
        assert body["authorized"] >= 1, body["summary"]

        events = client.get(f"{API}/autonomy/events").json()["items"]
        assert any(event["type"] == "plan_committed" for event in events)
