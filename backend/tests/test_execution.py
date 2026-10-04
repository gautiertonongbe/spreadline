"""The boundary between deciding and buying, and the way back across it.

Spreadline decides; a person buys. These tests hold both halves of that: that
nothing here places an order, and that what the person actually did comes back
and corrects the record. The second half is the one that matters, because a
position left at the figures that were authorised makes every realised number
downstream a measurement of a purchase that never happened.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.errors import ConflictError, ValidationError
from app.domains.autonomy import engine, positions
from app.domains.execution import service as execution
from app.models.autonomy import CapitalPosition
from app.models.enums import PositionStatus
from app.models.portfolio import Purchase
from tests.test_allocation import build_candidate, policy_for

API = "/api/v1"


def authorise(session, auth, *, cost=20, profit=10, capital=Decimal("100")):
    """Run a real decision through to an authorisation and its instruction."""
    opportunity = build_candidate(session, auth, title="Widget", cost=cost, profit=profit)
    policy_for(session, auth, capital_limit=capital, max_position_pct=Decimal("1"))
    result, row, position = engine.authorize(session, auth, opportunity)
    assert result.authorized, result.reason
    pending = execution.outstanding(session, auth)
    assert pending, "an authorised decision must produce an instruction"
    return pending[0], position


class TestTheInstruction:
    def test_an_authorisation_produces_something_a_person_can_act_on(self, session, auth):
        instruction, position = authorise(session, auth)
        assert instruction.status == execution.InstructionStatus.PENDING.value
        assert instruction.executor == "human"
        assert instruction.quantity_authorized > 0
        assert instruction.max_unit_price > 0
        assert instruction.position_id == position.id
        assert "human action" in execution.summary(instruction)

    def test_it_carries_the_ceiling_the_decision_rested_on(self, session, auth):
        instruction, _ = authorise(session, auth)
        assert "no more than" in execution.summary(instruction)

    def test_it_expires_rather_than_waiting_forever(self, session, auth):
        """An authorisation to pay a price is as good as the price behind it."""
        instruction, _ = authorise(session, auth)
        assert instruction.expires_at is not None
        assert not execution.is_expired(instruction)

        instruction.expires_at = utcnow() - timedelta(seconds=1)
        session.flush()
        assert execution.is_expired(instruction)
        assert "needs deciding again" in execution.summary(instruction)

    def test_expiring_releases_the_capital_the_position_was_holding(self, session, auth):
        """Capital the system thinks is working, and is not, distorts every limit."""
        instruction, position = authorise(session, auth)
        instruction.expires_at = utcnow() - timedelta(seconds=1)
        session.flush()

        assert execution.expire_overdue(session, auth) == 1
        session.refresh(position)
        assert position.status == PositionStatus.CANCELLED.value
        assert positions.deployed_capital(session, auth) == Decimal("0.0000")


class TestRecordingWhatHappened:
    def test_the_position_is_corrected_to_what_was_actually_paid(self, session, auth):
        """The reason the module exists."""
        instruction, position = authorise(session, auth, cost=20, capital=Decimal("100"))
        authorised_quantity = instruction.quantity_authorized
        assert authorised_quantity >= 2

        execution.record_execution(
            session,
            auth,
            instruction.id,
            execution.ExecutionInput(quantity=2, unit_price=Decimal("22.50")),
        )
        session.refresh(position)
        assert position.quantity == 2
        assert position.unit_cost == Decimal("22.5000")
        assert position.capital_invested == Decimal("45.0000")

    def test_paying_above_the_ceiling_is_named_rather_than_absorbed(self, session, auth):
        instruction, _ = authorise(session, auth)
        over = instruction.max_unit_price + Decimal("5")

        row = execution.record_execution(
            session,
            auth,
            instruction.id,
            execution.ExecutionInput(quantity=1, unit_price=over),
        )
        assert execution.PAID_ABOVE_CEILING in row.variances
        assert "ceiling" in execution.summary(row)
        # Still recorded: refusing to write down a purchase that happened only
        # makes the books wrong.
        assert row.purchase_id is not None

    def test_buying_fewer_is_partial_and_not_a_failure(self, session, auth):
        instruction, _ = authorise(session, auth, cost=20, capital=Decimal("100"))
        row = execution.record_execution(
            session,
            auth,
            instruction.id,
            execution.ExecutionInput(quantity=1, unit_price=Decimal("20")),
        )
        assert row.status == execution.InstructionStatus.PARTIAL.value
        assert execution.UNDER_QUANTITY in row.variances
        assert "stock runs out" in execution.summary(row)

    def test_buying_more_than_authorised_is_flagged(self, session, auth):
        instruction, _ = authorise(session, auth, cost=20, capital=Decimal("100"))
        row = execution.record_execution(
            session,
            auth,
            instruction.id,
            execution.ExecutionInput(
                quantity=instruction.quantity_authorized + 1, unit_price=Decimal("20")
            ),
        )
        assert execution.OVER_QUANTITY in row.variances

    def test_nothing_bought_is_an_outcome_and_releases_the_capital(self, session, auth):
        instruction, position = authorise(session, auth)
        row = execution.record_execution(
            session,
            auth,
            instruction.id,
            execution.ExecutionInput(quantity=0, notes="Sold out before I got there."),
        )
        assert row.status == execution.InstructionStatus.NOT_EXECUTED.value
        session.refresh(position)
        assert position.status == PositionStatus.CANCELLED.value
        assert "Sold out" in execution.summary(row)

    def test_a_purchase_is_written_into_the_books(self, session, auth):
        instruction, _ = authorise(session, auth)
        execution.record_execution(
            session,
            auth,
            instruction.id,
            execution.ExecutionInput(
                quantity=1,
                unit_price=Decimal("19"),
                shipping_cost=Decimal("3"),
                tax=Decimal("1"),
                order_reference="ORD-1",
            ),
        )
        purchase = session.scalars(select(Purchase)).first()
        assert purchase is not None
        assert purchase.quantity == 1
        assert purchase.total_cost == Decimal("23.0000"), "unit price plus the extras"

    def test_a_price_is_required_when_anything_was_bought(self, session, auth):
        instruction, _ = authorise(session, auth)
        with pytest.raises(ValidationError):
            execution.record_execution(
                session, auth, instruction.id, execution.ExecutionInput(quantity=1)
            )

    def test_one_authorisation_cannot_be_closed_out_twice(self, session, auth):
        """A second purchase is a second decision, not an edit to this one."""
        instruction, _ = authorise(session, auth)
        execution.record_execution(
            session,
            auth,
            instruction.id,
            execution.ExecutionInput(quantity=1, unit_price=Decimal("20")),
        )
        with pytest.raises(ConflictError):
            execution.record_execution(
                session,
                auth,
                instruction.id,
                execution.ExecutionInput(quantity=1, unit_price=Decimal("20")),
            )

    def test_a_late_execution_is_recorded_and_marked_late(self, session, auth):
        instruction, _ = authorise(session, auth)
        instruction.expires_at = utcnow() - timedelta(seconds=1)
        session.flush()

        row = execution.record_execution(
            session,
            auth,
            instruction.id,
            execution.ExecutionInput(quantity=1, unit_price=Decimal("20")),
        )
        assert execution.EXPIRED_WHEN_EXECUTED in row.variances


class TestReporting:
    def test_the_report_says_when_nothing_has_been_checked_against_reality(self, session, auth):
        authorise(session, auth)
        body = execution.report(session, auth)
        assert body["counts"]["pending"] == 1
        assert body["counts"]["closed"] == 0
        assert "no realised figure" in body["summary"]

    def test_fidelity_counts_the_ones_carried_out_as_authorised(self, session, auth):
        instruction, _ = authorise(session, auth, cost=20, capital=Decimal("20"))
        execution.record_execution(
            session,
            auth,
            instruction.id,
            execution.ExecutionInput(
                quantity=instruction.quantity_authorized,
                unit_price=instruction.max_unit_price,
            ),
        )
        body = execution.report(session, auth)
        assert body["counts"]["closed"] == 1
        assert body["counts"]["with_variance"] == 0
        assert "exactly as authorised" in body["summary"]

    def test_the_note_states_the_boundary(self, session, auth):
        body = execution.report(session, auth)
        assert "does not place retail orders" in body["note"]
        assert "no purchasing bot" in body["note"]


class TestApi:
    def _authorised(self, client, session, auth):
        opportunity = build_candidate(session, auth, title="Widget", cost=20, profit=10)
        policy_for(session, auth, capital_limit=Decimal("100"), max_position_pct=Decimal("1"))
        session.commit()
        response = client.post(
            f"{API}/autonomy/decisions/run", json={"opportunity_id": opportunity.id}
        )
        assert response.status_code == 200, response.text
        assert response.json()["decision"]["outcome"] == "authorized"
        return response.json()

    def test_an_authorised_decision_shows_up_as_work_to_do(self, client, session, auth):
        self._authorised(client, session, auth)
        body = client.get(f"{API}/execution").json()
        assert body["counts"]["pending"] == 1
        assert body["outstanding"][0]["quantity_authorized"] >= 1

    def test_recording_the_purchase_closes_it_out(self, client, session, auth):
        self._authorised(client, session, auth)
        instruction = client.get(f"{API}/execution").json()["outstanding"][0]

        response = client.post(
            f"{API}/execution/instructions/{instruction['id']}/record",
            json={"quantity": 1, "unit_price": "21.00", "order_reference": "ORD-9"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] in {"executed", "partial"}
        assert body["purchase_id"]

        assert client.get(f"{API}/execution").json()["counts"]["pending"] == 0

    def test_cancelling_needs_a_reason(self, client, session, auth):
        self._authorised(client, session, auth)
        instruction = client.get(f"{API}/execution").json()["outstanding"][0]
        assert (
            client.post(
                f"{API}/execution/instructions/{instruction['id']}/cancel", json={}
            ).status_code
            == 422
        )

    def test_an_unknown_instruction_is_a_404(self, client):
        response = client.get(f"{API}/execution/instructions/does-not-exist")
        assert response.status_code == 404


class TestBoundary:
    def test_nothing_in_the_module_places_an_order(self):
        """A grep with a reason.

        The constraint is a product decision, and the cheapest way to notice it
        being eroded is to assert that the words that would have to appear never
        do.
        """
        from pathlib import Path

        source = Path(execution.__file__).read_text().lower()
        for forbidden in ("add_to_cart", "checkout(", "place_order", "webdriver", "captcha"):
            assert forbidden not in source, f"{forbidden} has no business in this module"

    def test_an_instruction_names_a_human_executor(self, session, auth):
        instruction, _ = authorise(session, auth)
        assert instruction.executor == "human"


def test_a_cancelled_position_is_kept_rather_than_deleted(session, auth):
    """The decision happened; the record of it stays."""
    instruction, position = authorise(session, auth)
    execution.cancel(session, auth, instruction.id, reason="changed my mind")

    kept = session.get(CapitalPosition, position.id)
    assert kept is not None
    assert kept.status == PositionStatus.CANCELLED.value
    assert "changed my mind" in (kept.notes or "")
