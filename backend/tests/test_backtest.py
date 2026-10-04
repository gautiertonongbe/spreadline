"""Replaying a policy against recorded history.

A backtest is either honest or worthless, and the difference is one property:
whether it can see the future. Most of what is asserted here is about that, and
about the result never presenting an assumption as a measurement.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.core.clock import ensure_utc, utcnow
from app.domains.autonomy import policy as policy_module
from app.domains.backtest import engine as backtest
from app.domains.opportunities.analysis import load_price_points
from app.models.catalog import MarketplaceListing, Product
from app.models.observations import PriceObservation
from app.models.opportunity import Opportunity

API = "/api/v1"
NOW = utcnow()


def build_pair(session, auth, *, source_prices, target_prices, days_apart=1, risk="low"):
    """One matched pair with a price series on each side."""
    product = Product(organization_id=auth.organization_id, title="Test Widget", brand="Acme")
    session.add(product)
    session.flush()

    listings = {}
    for role, marketplace, prices in (
        ("source", "walmart", source_prices),
        ("target", "amazon", target_prices),
    ):
        listing = MarketplaceListing(
            organization_id=auth.organization_id,
            product_id=product.id,
            marketplace=marketplace,
            external_id=f"{role}-1",
            title="Test Widget",
            current_price=Decimal(str(prices[-1])),
        )
        session.add(listing)
        session.flush()
        listings[role] = listing
        for index, price in enumerate(prices):
            observed = NOW - timedelta(days=(len(prices) - 1 - index) * days_apart)
            session.add(
                PriceObservation(
                    organization_id=auth.organization_id,
                    product_id=product.id,
                    listing_id=listing.id,
                    marketplace=marketplace,
                    price=Decimal(str(price)),
                    shipping=Decimal("0"),
                    landed_price=Decimal(str(price)),
                    observed_at=observed,
                    provider="mock_test",
                    source="poll",
                    is_simulated=True,
                )
            )

    opportunity = Opportunity(
        organization_id=auth.organization_id,
        product_id=product.id,
        source_listing_id=listings["source"].id,
        target_listing_id=listings["target"].id,
        direction="walmart_to_amazon",
        source_marketplace="walmart",
        target_marketplace="amazon",
        recommendation="buy",
        risk_level=risk,
        match_confidence=Decimal("0.99"),
        acquisition_cost=Decimal(str(source_prices[-1])),
        expected_sale_price=Decimal(str(target_prices[-1])),
    )
    session.add(opportunity)
    session.flush()
    return opportunity, listings


def policy_for(session, auth, **changes):
    base = {
        "autonomy_level": 3,
        "capital_limit": Decimal("1000"),
        "execution_mode": "shadow",
        "require_human_approval": False,
        "minimum_roi": Decimal("0.10"),
        "minimum_profit": Decimal("1"),
    }
    base.update(changes)
    policy_module.update_policy(session, auth, base)
    return policy_module.view(policy_module.active_policy(session, auth))


class TestNoLookahead:
    def test_a_loader_truncated_to_a_past_instant_cannot_see_later_prices(
        self, session, auth
    ):
        """The lever the whole feature's honesty rests on.

        If this filter is wrong, every figure a backtest produces is a memory of
        the answer rather than a measurement of a decision.
        """
        _opportunity, listings = build_pair(
            session, auth, source_prices=[10, 11, 12, 13, 14], target_prices=[20] * 5
        )
        listing_id = listings["source"].id

        everything = load_price_points(session, listing_id)
        assert len(everything) == 5

        cutoff = NOW - timedelta(days=2, hours=1)
        truncated = load_price_points(session, listing_id, as_of=cutoff)
        assert len(truncated) == 2, "only observations at or before the cutoff"
        assert all(ensure_utc(point.observed_at) <= cutoff for point in truncated)
        assert max(point.price for point in truncated) < max(
            point.price for point in everything
        ), "the later, higher prices must not be visible"

    def test_an_exit_is_only_found_after_the_entry(self, session, auth):
        """A position cannot be closed by a price that predates buying it."""
        series = backtest._Series(
            points=[
                (NOW - timedelta(days=10), Decimal("99")),
                (NOW - timedelta(days=3), Decimal("50")),
            ],
            simulated=0,
        )
        # The high price is before the entry, so it must not be usable as an exit.
        found = series.first_at_or_above(
            Decimal("90"), after=NOW - timedelta(days=5), until=NOW
        )
        assert found is None

    def test_a_price_is_carried_forward_never_interpolated(self, session, auth):
        """Inventing a value between two observations would fabricate a market."""
        series = backtest._Series(
            points=[
                (NOW - timedelta(days=10), Decimal("100")),
                (NOW - timedelta(days=2), Decimal("200")),
            ],
            simulated=0,
        )
        assert series.price_at(NOW - timedelta(days=5)) == Decimal("100")
        assert series.price_at(NOW - timedelta(days=11)) is None
        assert series.price_at(NOW) == Decimal("200")


class TestReplay:
    def test_a_profitable_spread_is_bought_and_closed(self, session, auth):
        build_pair(
            session,
            auth,
            source_prices=[20] * 40,
            target_prices=[60] * 40,
        )
        policy = policy_for(session, auth)
        result = backtest.run(session, auth, policy, days=30)

        assert result.trades, result.summary
        trade = result.trades[0]
        assert trade.entry_price == Decimal("20.0000")
        assert trade.quantity > 0
        assert trade.capital <= result.starting_capital

    def test_thin_history_is_skipped_rather_than_guessed(self, session, auth):
        """Below the coverage floor a replay would be inventing a market."""
        build_pair(session, auth, source_prices=[20, 21, 22], target_prices=[60, 61, 62])
        policy = policy_for(session, auth)
        result = backtest.run(session, auth, policy, days=30)

        assert result.trades == []
        assert result.skipped
        assert "distinct day" in result.skipped[0]["reason"]

    def test_an_unprofitable_spread_is_refused_with_a_cause(self, session, auth):
        build_pair(session, auth, source_prices=[60] * 40, target_prices=[62] * 40)
        policy = policy_for(session, auth)
        result = backtest.run(session, auth, policy, days=30)

        assert result.trades == []
        assert result.rejections
        codes = {rejection.reason_code for rejection in result.rejections}
        assert codes & {"NOT_PROFITABLE", "PROFIT_BELOW_MINIMUM", "ROI_BELOW_MINIMUM"}

    def test_capital_is_never_exceeded(self, session, auth):
        """The limit binds in a replay exactly as it does live."""
        build_pair(session, auth, source_prices=[20] * 40, target_prices=[60] * 40)
        policy = policy_for(session, auth, capital_limit=Decimal("100"))
        result = backtest.run(session, auth, policy, days=30, starting_capital=Decimal("100"))

        for trade in result.trades:
            assert trade.capital <= Decimal("100")
        assert sum(trade.capital for trade in result.trades) <= Decimal("100")

    def test_a_position_is_liquidated_at_the_age_limit(self, session, auth):
        """A monotonic decline never revisits the entry price, so age ends it.

        The series has to fall without recovering: any later observation at or
        above the entry-time exit price would close the position on plan, which
        is a different branch.
        """
        build_pair(
            session,
            auth,
            source_prices=[20] * 60,
            target_prices=[90 - index for index in range(60)],
        )
        policy = policy_for(session, auth, maximum_inventory_age_days=10)
        result = backtest.run(session, auth, policy, days=55)

        closed = result.closed
        assert closed, result.summary
        assert all("age limit" in trade.exit_reason for trade in closed)
        trade = closed[0]
        assert trade.exit_price is not None
        assert trade.exit_price < trade.expected_sale_price, "it sold into a falling market"

    def test_exit_fees_are_recomputed_at_the_exit_price(self, session, auth):
        """A position that sells below plan does not pay the fee it would have.

        The referral fee is a share of the sale, so carrying the entry-time fee
        forward would charge a fee on revenue that never arrived and overstate
        every loss.
        """
        build_pair(
            session,
            auth,
            source_prices=[20] * 60,
            target_prices=[90 - index for index in range(60)],
        )
        policy = policy_for(session, auth, maximum_inventory_age_days=5)
        result = backtest.run(session, auth, policy, days=50)

        closed = [trade for trade in result.closed if trade.exit_price is not None]
        assert closed
        trade = closed[0]
        assert trade.realized_profit is not None
        assert trade.exit_price < trade.expected_sale_price

        # Priced at the exit price by the real engine, so the profit equals the
        # engine's answer rather than the entry figure adjusted by the drop.
        from decimal import Decimal as D

        from app.domains.profitability.engine import (
            ProfitabilityInput,
            calculate_profitability,
        )
        from app.models.enums import Marketplace

        expected = calculate_profitability(
            ProfitabilityInput(
                sale_price=trade.exit_price,
                acquisition_cost=trade.entry_price,
                target_marketplace=Marketplace.AMAZON,
            )
        )
        assert trade.realized_profit == (expected.net_profit * trade.quantity).quantize(
            D("0.0001")
        )


class TestHonesty:
    def test_the_result_always_carries_its_caveats(self, session, auth):
        build_pair(session, auth, source_prices=[20] * 40, target_prices=[60] * 40)
        policy = policy_for(session, auth)
        body = backtest.run(session, auth, policy, days=30).as_dict()

        caveats = body["caveats"]
        assert caveats
        assert any("would have allowed" in note for note in caveats)
        assert any("Identity is held constant" in note for note in caveats)
        assert any("Risk, demand and competition are held" in note for note in caveats)
        assert body["exit_rule"] == backtest.ExitRule.AT_MARKET

    def test_a_fixture_backed_replay_says_so(self, session, auth):
        """A backtest over fixtures measures the fixtures, and must say it."""
        build_pair(session, auth, source_prices=[20] * 40, target_prices=[60] * 40)
        policy = policy_for(session, auth)
        result = backtest.run(session, auth, policy, days=30)

        assert result.simulated_share == Decimal("1.0000")
        assert any("fixture data" in note for note in result.as_dict()["caveats"])

    def test_a_small_sample_is_flagged_rather_than_summarised_away(self, session, auth):
        build_pair(session, auth, source_prices=[20] * 40, target_prices=[60] * 40)
        policy = policy_for(session, auth)
        body = backtest.run(session, auth, policy, days=30).as_dict()
        assert any("too few to draw a conclusion" in note for note in body["caveats"])

    def test_refusals_are_grouped_rather_than_repeated_per_day(self, session, auth):
        """The same product refused for the same reason every day is one fact.

        A day-by-day list is capped by the API, so a caller counting it would be
        counting a truncation. The count has to come from the server.
        """
        build_pair(
            session,
            auth,
            source_prices=[400] * 40,  # far above any position cap
            target_prices=[900] * 40,
        )
        policy = policy_for(session, auth)
        result = backtest.run(session, auth, policy, days=30)

        assert result.rejections, "the candidate should have been refused"
        groups = result.refusals
        assert len(groups) < len(result.rejections)
        group = groups[0]
        assert group.days == sum(
            1
            for rejection in result.rejections
            if (rejection.opportunity_id, rejection.reason_code)
            == (group.opportunity_id, group.reason_code)
        )
        assert group.first_at <= group.last_at
        assert group.title and group.title != group.opportunity_id

    def test_an_empty_window_reports_why(self, session, auth):
        policy = policy_for(session, auth)
        result = backtest.run(session, auth, policy, days=30)
        assert result.trades == []
        assert "bought nothing" in result.summary


class TestApi:
    def test_the_endpoint_replays_and_returns_caveats(self, client):
        body = client.post(f"{API}/autonomy/backtest", json={"days": 30}).json()
        assert "results" in body
        assert body["caveats"]
        assert body["window"]["days"] == 30
        assert body["exit_rule"]

    def test_the_window_is_bounded(self, client):
        assert client.post(f"{API}/autonomy/backtest", json={"days": 5000}).status_code == 422
