"""The learning loop and the sell side.

Both exist to improve a decision rather than to make one, and both are held to
the same standard as everything else here: a pattern needs a sample behind it,
and a recommendation states what it was read from.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.core.clock import utcnow
from app.domains.autonomy import policy as policy_module
from app.domains.autonomy import positions, selling
from app.domains.autonomy.selling import SellAction
from app.domains.learning import attribution
from app.models.catalog import Product
from app.models.enums import ExecutionMode
from app.models.portfolio import Outcome

API = "/api/v1"


def product(session, auth, *, title: str, category: str, brand: str) -> Product:
    row = Product(
        organization_id=auth.organization_id, title=title, category=category, brand=brand
    )
    session.add(row)
    session.flush()
    return row


def outcome(
    session,
    auth,
    *,
    product_id: str,
    predicted: str,
    actual: str,
    capital: str = "100",
) -> Outcome:
    row = Outcome(
        organization_id=auth.organization_id,
        product_id=product_id,
        quantity_purchased=1,
        quantity_sold=1,
        capital_deployed=Decimal(capital),
        predicted_total_profit=Decimal(predicted),
        actual_profit=Decimal(actual),
        is_closed=True,
        result="win" if Decimal(actual) > 0 else "loss",
    )
    session.add(row)
    session.flush()
    return row


class TestAttribution:
    def test_nothing_to_attribute_without_closed_positions(self, session, auth):
        report = attribution.attribute(session, auth)
        assert report.observations == 0
        assert report.findings == []
        assert "No closed positions yet" in report.summary

    def test_a_consistent_overestimate_is_found_and_named(self, session, auth):
        """The finding the whole module exists for.

        Six positions in one category, every one of them worse than predicted.
        That is a pattern; the average alone would not say so.
        """
        item = product(session, auth, title="Serum", category="Beauty", brand="Acme")
        for _ in range(6):
            outcome(session, auth, product_id=item.id, predicted="100", actual="70")

        report = attribution.attribute(session, auth)
        found = [f for f in report.findings if f.segment == "Beauty"]
        assert found, report.summary
        finding = found[0]
        assert finding.direction == "overestimated"
        assert finding.sample == 6
        assert finding.agreement == Decimal("1.0000")
        assert "overestimated" in finding.summary
        assert finding.suggestion

    def test_a_small_sample_is_not_a_pattern(self, session, auth):
        """Three sales is a story, not a measurement."""
        item = product(session, auth, title="Thing", category="Toys", brand="Acme")
        for _ in range(3):
            outcome(session, auth, product_id=item.id, predicted="100", actual="40")

        report = attribution.attribute(session, auth)
        assert not [f for f in report.findings if f.segment == "Toys"]
        reasons = [note["reason"] for note in report.insufficient if note["segment"] == "Toys"]
        assert reasons and "needed" in reasons[0]

    def test_errors_that_cancel_out_are_not_a_bias(self, session, auth):
        """A mean of zero across +50% and -50% is noise, not accuracy."""
        item = product(session, auth, title="Gadget", category="Electronics", brand="Acme")
        for value in ("150", "50", "150", "50", "150", "50"):
            outcome(session, auth, product_id=item.id, predicted="100", actual=value)

        report = attribution.attribute(session, auth)
        assert not [f for f in report.findings if f.segment == "Electronics"]

    def test_findings_are_ranked_by_money_not_by_error_size(self, session, auth):
        """A 40% error on one small position matters less than 16% on many large ones."""
        small = product(session, auth, title="Small", category="Small", brand="S")
        large = product(session, auth, title="Large", category="Large", brand="L")
        for _ in range(5):
            outcome(session, auth, product_id=small.id, predicted="10", actual="6", capital="20")
        for _ in range(5):
            outcome(
                session, auth, product_id=large.id, predicted="400", actual="320", capital="900"
            )

        report = attribution.attribute(session, auth)
        categories = [f.segment for f in report.findings if f.dimension == "category"]
        assert categories[0] == "Large", "the bigger money should lead"

    def test_a_zero_prediction_does_not_dominate(self, session, auth):
        """A percentage error against a zero prediction is undefined, not infinite."""
        item = product(session, auth, title="Edge", category="Edge", brand="E")
        outcome(session, auth, product_id=item.id, predicted="0", actual="50")
        for _ in range(5):
            outcome(session, auth, product_id=item.id, predicted="100", actual="80")

        report = attribution.attribute(session, auth)
        finding = next(f for f in report.findings if f.segment == "Edge")
        assert finding.sample == 5, "the undefined one is excluded from the ratio"

    def test_the_report_is_served(self, client):
        body = client.get(f"{API}/autonomy/learning").json()
        assert "findings" in body
        assert body["minimum_sample"] == attribution.MIN_SAMPLE
        assert body["summary"]


class TestSelling:
    def _policy(self, session, auth, **changes):
        if changes:
            policy_module.update_policy(session, auth, changes)
        return policy_module.view(policy_module.active_policy(session, auth))

    def test_a_fresh_position_is_held(self, session, auth):
        row = positions.open_position(
            session,
            auth,
            execution_mode=ExecutionMode.SHADOW,
            quantity=2,
            unit_cost=Decimal("50"),
            expected_unit_profit=Decimal("20"),
        )
        policy = self._policy(session, auth)
        item = positions.view(row)
        result = selling.assess_position(session, item, policy)
        assert result.action is SellAction.HOLD
        assert result.is_urgent is False
        assert result.reasons

    def test_a_position_past_the_age_limit_is_liquidated(self, session, auth):
        row = positions.open_position(
            session,
            auth,
            execution_mode=ExecutionMode.SHADOW,
            quantity=1,
            unit_cost=Decimal("50"),
        )
        row.opened_at = utcnow() - timedelta(days=60)
        session.flush()

        policy = self._policy(session, auth, maximum_inventory_age_days=45)
        result = selling.assess_position(session, positions.view(row), policy)
        assert result.action is SellAction.LIQUIDATE
        assert result.is_urgent
        assert "limit" in result.summary

    def test_an_ageing_position_is_sold_before_the_limit(self, session, auth):
        """Capital turnover starts to matter before the deadline, not at it."""
        row = positions.open_position(
            session,
            auth,
            execution_mode=ExecutionMode.SHADOW,
            quantity=1,
            unit_cost=Decimal("50"),
        )
        row.opened_at = utcnow() - timedelta(days=35)
        session.flush()

        policy = self._policy(session, auth, maximum_inventory_age_days=45)
        result = selling.assess_position(session, positions.view(row), policy)
        assert result.action is SellAction.SELL_NOW

    def test_the_review_orders_by_urgency_then_by_money(self, session, auth):
        small = positions.open_position(
            session,
            auth,
            execution_mode=ExecutionMode.SHADOW,
            quantity=1,
            unit_cost=Decimal("10"),
        )
        small.opened_at = utcnow() - timedelta(days=60)
        big = positions.open_position(
            session,
            auth,
            execution_mode=ExecutionMode.SHADOW,
            quantity=1,
            unit_cost=Decimal("900"),
        )
        big.opened_at = utcnow() - timedelta(days=60)
        calm = positions.open_position(
            session,
            auth,
            execution_mode=ExecutionMode.SHADOW,
            quantity=1,
            unit_cost=Decimal("500"),
        )
        session.flush()

        policy = self._policy(session, auth, maximum_inventory_age_days=45)
        body = selling.review(session, auth, policy)
        order = [entry["position_id"] for entry in body["items"]]
        assert order[0] == big.id, "urgent and largest first"
        assert order[-1] == calm.id, "the one needing nothing comes last"
        assert body["needs_attention"] == 2
        assert Decimal(body["capital_needing_attention"]) == Decimal("910.0000")

    def test_the_review_never_claims_to_have_acted(self, session, auth):
        policy = self._policy(session, auth)
        body = selling.review(session, auth, policy)
        assert "Recommendations only" in body["note"]

    def test_the_review_is_served(self, client):
        body = client.get(f"{API}/autonomy/sell-review").json()
        assert body["total"] == 0
        assert body["by_action"] == {}
        assert "note" in body
