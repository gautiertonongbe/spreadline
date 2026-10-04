"""How long capital stays tied up, and what the allocator does with it.

The measurement exists so that a 20% return in ten days can beat a 30% return in
ninety. Most of these tests are about the boundary of what may be claimed: that
an open position contributes nothing, that a thin segment gets no number of its
own, and that with no history at all the ranking says so rather than inventing a
holding period.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.core.clock import utcnow
from app.domains.autonomy import allocation, positions
from app.domains.learning import velocity
from app.models.enums import ExecutionMode, PositionStatus
from tests.test_allocation import build_candidate, policy_for

API = "/api/v1"


def close_position(
    session,
    auth,
    *,
    days_held,
    capital=Decimal("100"),
    profit=Decimal("20"),
    category="electronics",
    brand="Acme",
    marketplace="amazon",
):
    """A closed position with a known holding period."""
    opened = utcnow() - timedelta(days=days_held)
    row = positions.open_position(
        session,
        auth,
        execution_mode=ExecutionMode.SHADOW,
        quantity=1,
        unit_cost=capital,
        category=category,
        brand=brand,
        target_marketplace=marketplace,
    )
    row.opened_at = opened
    row.closed_at = opened + timedelta(days=days_held)
    row.status = PositionStatus.CLOSED.value
    row.quantity_sold = 1
    row.realized_proceeds = capital + profit
    row.realized_profit = profit
    session.flush()
    return row


class TestWhatMayBeClaimed:
    def test_no_closed_positions_means_no_holding_period(self, session, auth):
        """The whole point. A default invented here would be load-bearing."""
        report = velocity.measure(session, auth)
        assert not report.measured
        assert report.portfolio_median_days is None
        assert report.expected_days(category="electronics", brand="Acme") is None
        assert "not measured yet" in report.summary

    def test_an_open_position_contributes_nothing(self, session, auth):
        """Counting today's age drags the median towards what has not sold."""
        opportunity = build_candidate(session, auth, title="Open", cost=50, profit=10)
        positions.open_position(
            session,
            auth,
            execution_mode=ExecutionMode.SHADOW,
            quantity=1,
            unit_cost=Decimal("50"),
            opportunity_id=opportunity.id,
        )
        session.flush()

        report = velocity.measure(session, auth)
        assert not report.measured
        assert report.sample == 0

    def test_a_thin_segment_gets_no_number_of_its_own(self, session, auth):
        for index in range(velocity.MIN_SAMPLE - 1):
            close_position(session, auth, days_held=10 + index, category="thin")

        report = velocity.measure(session, auth)
        assert report.measured
        assert not [entry for entry in report.segments if entry.segment == "thin"]
        assert any(entry["segment"] == "thin" for entry in report.insufficient)

    def test_a_thin_segment_falls_back_to_the_portfolio_and_says_so(self, session, auth):
        for _ in range(velocity.MIN_SAMPLE):
            close_position(session, auth, days_held=20, category="measured")

        report = velocity.measure(session, auth)
        expected = report.expected_days(category="never-seen", brand=None)
        assert expected is not None
        days, basis = expected
        assert days == report.portfolio_median_days
        assert "across the portfolio" in basis


class TestMeasurement:
    def test_the_median_is_the_median(self, session, auth):
        for days in (5, 10, 40):
            close_position(session, auth, days_held=days, category="mixed")
        report = velocity.measure(session, auth)
        assert report.portfolio_median_days == 10

    def test_a_segment_with_enough_history_gets_its_own_figure(self, session, auth):
        for _ in range(velocity.MIN_SAMPLE):
            close_position(session, auth, days_held=7, category="fast")
        for _ in range(velocity.MIN_SAMPLE):
            close_position(session, auth, days_held=90, category="slow")

        report = velocity.measure(session, auth)
        by_segment = {
            entry.segment: entry for entry in report.segments if entry.dimension == "category"
        }
        assert by_segment["fast"].median_days == 7
        assert by_segment["slow"].median_days == 90

        fast = report.expected_days(category="fast", brand=None)
        slow = report.expected_days(category="slow", brand=None)
        assert fast is not None and slow is not None
        assert fast[0] < slow[0]

    def test_a_same_day_sale_is_counted_as_one_day(self, session, auth):
        """Zero days would make the annualised return infinite."""
        close_position(session, auth, days_held=0)
        report = velocity.measure(session, auth)
        assert report.observations[0].days_held == velocity.MINIMUM_DAYS
        assert report.observations[0].annualized_roi is not None

    def test_the_slowest_segment_is_reported_first(self, session, auth):
        for _ in range(velocity.MIN_SAMPLE):
            close_position(session, auth, days_held=5, category="fast")
        for _ in range(velocity.MIN_SAMPLE):
            close_position(session, auth, days_held=80, category="slow")

        report = velocity.measure(session, auth)
        categories = [e for e in report.segments if e.dimension == "category"]
        assert categories[0].segment == "slow", "the capital sink is what needs seeing"


class TestRanking:
    def test_without_history_the_plan_ranks_on_return_and_says_so(self, session, auth):
        build_candidate(session, auth, title="A", cost=20, profit=10)
        policy = policy_for(session, auth, capital_limit=Decimal("100"))

        plan = allocation.plan(session, auth, policy)
        assert not plan.ranked_on_time
        assert "no measured holding period" in plan.ranking_explanation
        assert plan.lines[0].expected_days is None
        assert plan.lines[0].annualized_return is None

    def test_a_faster_segment_outranks_a_higher_return_that_sits(self, session, auth):
        """The reason the measurement exists.

        Ranked on return alone the slow candidate wins on 40% against 20%. Ranked
        on return per year of capital tied up, the fast one is worth more than
        twice as much, and the ordering flips.
        """
        for _ in range(velocity.MIN_SAMPLE):
            close_position(session, auth, days_held=10, category="fast")
        for _ in range(velocity.MIN_SAMPLE):
            close_position(session, auth, days_held=180, category="slow")

        build_candidate(
            session,
            auth,
            title="Slow but fat",
            cost=50,
            profit=20,
            category="slow",
            brand="SlowBrand",
        )
        build_candidate(
            session,
            auth,
            title="Fast but thin",
            cost=50,
            profit=10,
            category="fast",
            brand="FastBrand",
        )
        policy = policy_for(session, auth, capital_limit=Decimal("1000"))

        plan = allocation.plan(session, auth, policy, budget=Decimal("200"))
        assert plan.ranked_on_time
        assert [line.title for line in plan.lines][0] == "Fast but thin"

        fast = plan.lines[0]
        assert fast.expected_days == 10
        assert fast.annualized_return is not None
        assert fast.annualized_return > Decimal("1")
        assert "category 'fast'" in (fast.velocity_basis or "")

    def test_a_line_says_where_its_holding_period_came_from(self, session, auth):
        for _ in range(velocity.MIN_SAMPLE):
            close_position(session, auth, days_held=30, category="known")
        # A brand and a category the closed positions have never seen, so
        # neither segment can answer and the portfolio figure has to.
        build_candidate(
            session,
            auth,
            title="Unknown kind",
            cost=20,
            profit=8,
            category="new",
            brand="NeverSeen",
        )
        policy = policy_for(session, auth, capital_limit=Decimal("500"))

        plan = allocation.plan(session, auth, policy)
        assert plan.lines
        assert "across the portfolio" in (plan.lines[0].velocity_basis or "")


class TestApi:
    def test_the_endpoint_reports_not_measured_rather_than_zero(self, client):
        body = client.get(f"{API}/autonomy/velocity").json()
        assert body["measured"] is False
        assert body["portfolio_median_days"] is None
        assert "not measured yet" in body["summary"]

    def test_the_plan_carries_the_evidence_for_its_ranking(self, client):
        body = client.get(f"{API}/autonomy/allocation").json()
        assert "ranked_on_time" in body
        assert body["velocity"]["minimum_sample"] == velocity.MIN_SAMPLE
        assert body["ranking"]
