"""Progressive autonomy and autonomous capital management.

The principle these tests exist to hold: autonomy is earned, not assumed, and
every limit is enforced by code rather than by intention. Most of what is
asserted here is a refusal - that the system does not spend past its limit, does
not promote itself, does not average away a risk objection, and does not resume
after a breaker on its own.

Correctness first, risk control second, measurement third, autonomy fourth.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationError
from app.domains.autonomy import breakers, eligibility, engine, gate, positions, scorecard
from app.domains.autonomy import policy as policy_module
from app.models.enums import (
    AgentStage,
    AgentVerdict,
    AutonomyLevel,
    ExecutionMode,
    PositionStatus,
)

API = "/api/v1"

CLEAN = (
    {"marketplace": "walmart", "external_id": "WM-598712344"},
    {"marketplace": "amazon", "external_id": "B09XS7JWHH"},
)


def analyze(client, pair=CLEAN):
    source, target = pair
    response = client.post(
        f"{API}/products/analyze",
        json={
            "source_marketplace": source["marketplace"],
            "source_external_id": source["external_id"],
            "target_marketplace": target["marketplace"],
            "target_external_id": target["external_id"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def enable(client, *, level=3, capital="100", mode="shadow", acknowledge=True):
    response = client.post(
        f"{API}/autonomy/enable",
        json={
            "level": level,
            "capital_limit": capital,
            "execution_mode": mode,
            "acknowledge_not_eligible": acknowledge,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestPolicyDefaults:
    def test_an_unconfigured_organisation_has_authorised_no_autonomy(self, client):
        """The safe state is the default, not something to be switched on."""
        policy = client.get(f"{API}/autonomy/policy").json()["policy"]
        assert policy["autonomy_level"] == int(AutonomyLevel.HUMAN_APPROVAL)
        assert Decimal(policy["capital_limit"]) == 0
        assert policy["require_human_approval"] is True
        assert policy["execution_mode"] == "observe"
        assert policy["may_deploy_capital"] is False

    def test_capital_deployment_needs_every_switch_to_agree(self, session, auth):
        """Four independent conditions, so one of them being wrong is not enough."""
        policy_module.update_policy(
            session,
            auth,
            {
                "autonomy_level": int(AutonomyLevel.CONTROLLED),
                "capital_limit": Decimal("100"),
                "execution_mode": "shadow",
                "require_human_approval": False,
            },
        )
        view = policy_module.view(policy_module.active_policy(session, auth))
        assert view.may_deploy_capital is True

        policy_module.set_emergency_stop(session, auth, active=True, reason="test")
        view = policy_module.view(policy_module.active_policy(session, auth))
        assert view.may_deploy_capital is False


class TestPolicyVersioning:
    def test_a_change_writes_a_new_version_and_keeps_the_old_one(self, client):
        """A decision made under v1.0 has to stay readable under v1.0."""
        first = client.get(f"{API}/autonomy/policy").json()["policy"]
        client.put(f"{API}/autonomy/policy", json={"minimum_roi": "0.30"})
        body = client.get(f"{API}/autonomy/policy").json()

        assert body["policy"]["version"] != first["version"]
        assert Decimal(body["policy"]["minimum_roi"]) == Decimal("0.30")
        versions = [row["version"] for row in body["history"]]
        assert first["version"] in versions, "the superseded version is still on the record"
        assert len([row for row in body["history"] if row["is_active"]]) == 1

    def test_a_policy_edit_cannot_clear_an_emergency_stop(self, client):
        """Otherwise a routine edit becomes an accidental resume."""
        client.post(
            f"{API}/autonomy/emergency-stop",
            json={"active": True, "reason": "provider outage"},
        )
        client.put(f"{API}/autonomy/policy", json={"minimum_roi": "0.25"})
        policy = client.get(f"{API}/autonomy/policy").json()["policy"]
        assert policy["emergency_stop_active"] is True
        assert policy["emergency_stop_reason"] == "provider outage"

    def test_autonomy_without_a_limit_is_refused(self, session, auth):
        """A level that deploys capital with no ceiling is not a policy."""
        with pytest.raises(ValidationError):
            policy_module.update_policy(
                session,
                auth,
                {"autonomy_level": int(AutonomyLevel.CONTROLLED), "capital_limit": Decimal("0")},
            )

    def test_an_unknown_field_is_rejected_rather_than_ignored(self, session, auth):
        with pytest.raises(ValidationError):
            policy_module.update_policy(session, auth, {"max_yolo": 5})


class TestEligibility:
    def test_a_clean_candidate_passes_every_mandatory_check(self, client, session, auth):
        body = analyze(client)
        from app.domains.opportunities import service as opportunities

        opportunity = opportunities.get_opportunity(session, auth, body["opportunity_id"])
        policy = policy_module.view(policy_module.active_policy(session, auth))
        result = eligibility.assess(opportunity, policy)
        assert result.eligible, result.summary
        assert all(check.required and check.actual for check in result.checks)

    def test_a_match_below_the_threshold_is_not_eligible(self, client, session, auth):
        """The one threshold set highest: the wrong item loses the position."""
        body = analyze(client)
        from app.domains.opportunities import service as opportunities

        opportunity = opportunities.get_opportunity(session, auth, body["opportunity_id"])
        policy_module.update_policy(session, auth, {"minimum_match_confidence": Decimal("0.999")})
        policy = policy_module.view(policy_module.active_policy(session, auth))
        result = eligibility.assess(opportunity, policy)
        assert not result.eligible
        assert result.reason_code == eligibility.MATCH_CONFIDENCE_TOO_LOW

    def test_an_unresolved_blocker_is_not_eligible(self, client, session, auth):
        body = analyze(client)
        from app.domains.opportunities import service as opportunities

        opportunity = opportunities.get_opportunity(session, auth, body["opportunity_id"])
        policy = policy_module.view(policy_module.active_policy(session, auth))
        result = eligibility.assess(
            opportunity, policy, primary_blocker="Risk no higher than medium"
        )
        assert not result.eligible
        assert result.reason_code == eligibility.UNRESOLVED_BLOCKER


class TestCapitalLimits:
    def test_an_agent_cannot_exceed_its_authorised_capital(self, client):
        """The case that matters most, stated as plainly as possible.

        Authorised $100, a unit that costs more than that: the answer is a
        refusal with a named cause, not a smaller purchase it was not asked to
        make and not a larger one it was not allowed to.
        """
        body = analyze(client)
        enable(client, capital="100")

        result = client.post(
            f"{API}/autonomy/decisions/run",
            json={"opportunity_id": body["opportunity_id"]},
        ).json()["decision"]

        assert result["outcome"] == "blocked"
        assert result["reason_code"] in {
            engine.CAPITAL_LIMIT_EXCEEDED,
            engine.POSITION_LIMIT_EXCEEDED,
        }
        assert Decimal(result["capital"]) == 0

    def test_a_request_cannot_raise_the_policy_ceiling(self, client):
        """The policy is the ceiling; a request can only ask for less."""
        body = analyze(client)
        enable(client, capital="100")

        result = client.post(
            f"{API}/autonomy/decisions/run",
            json={"opportunity_id": body["opportunity_id"], "available_capital": "100000"},
        ).json()["decision"]

        assert Decimal(result["capital"]) <= Decimal("100")

    def test_capital_already_deployed_reduces_what_is_available(self, client, session, auth):
        body = analyze(client)
        enable(client, capital="5000")

        first = client.post(
            f"{API}/autonomy/decisions/run",
            json={"opportunity_id": body["opportunity_id"]},
        ).json()
        assert first["decision"]["outcome"] == "authorized", first["decision"]["reason"]
        deployed = positions.deployed_capital(session, auth, execution_mode=ExecutionMode.SHADOW)
        assert deployed > 0

        overview = client.get(f"{API}/autonomy").json()
        assert Decimal(overview["capital"]["deployed"]) == deployed
        assert Decimal(overview["capital"]["available"]) == Decimal("5000") - deployed

    def test_the_position_share_limit_binds(self, client):
        """A single position cannot take more of the capital than the policy allows."""
        body = analyze(client)
        enable(client, capital="5000")
        client.put(f"{API}/autonomy/policy", json={"max_position_pct": "0.05"})

        result = client.post(
            f"{API}/autonomy/decisions/run",
            json={"opportunity_id": body["opportunity_id"]},
        ).json()["decision"]

        if result["outcome"] == "authorized":
            assert Decimal(result["capital"]) <= Decimal("5000") * Decimal("0.05")
        else:
            assert result["reason_code"] == engine.POSITION_LIMIT_EXCEEDED


class TestGovernanceGates:
    def test_human_approval_blocks_deployment(self, client):
        body = analyze(client)
        enable(client, capital="5000")
        client.put(f"{API}/autonomy/policy", json={"require_human_approval": True})

        result = client.post(
            f"{API}/autonomy/decisions/run",
            json={"opportunity_id": body["opportunity_id"]},
        ).json()["decision"]
        assert result["outcome"] == "blocked"
        assert result["reason_code"] == engine.HUMAN_APPROVAL_REQUIRED

    def test_a_level_that_does_not_deploy_capital_blocks_deployment(self, client):
        body = analyze(client)
        result = client.post(
            f"{API}/autonomy/decisions/run",
            json={"opportunity_id": body["opportunity_id"]},
        ).json()["decision"]
        assert result["outcome"] == "blocked"
        assert result["reason_code"] in {
            engine.AUTONOMY_LEVEL_TOO_LOW,
            engine.HUMAN_APPROVAL_REQUIRED,
        }

    def test_the_emergency_stop_halts_new_capital_immediately(self, client):
        """It stops spending. It does not unwind, and it does not stop analysis."""
        body = analyze(client)
        enable(client, capital="5000")
        client.post(
            f"{API}/autonomy/emergency-stop",
            json={"active": True, "reason": "pricing anomaly"},
        )

        result = client.post(
            f"{API}/autonomy/decisions/run",
            json={"opportunity_id": body["opportunity_id"]},
        ).json()["decision"]
        assert result["outcome"] == "blocked"
        assert result["reason_code"] == engine.EMERGENCY_STOP_ACTIVE

        # Analysis still works while stopped.
        assert client.get(f"{API}/opportunities").status_code == 200

    def test_the_stop_can_be_cleared_and_deployment_resumes(self, client):
        body = analyze(client)
        enable(client, capital="5000")
        client.post(f"{API}/autonomy/emergency-stop", json={"active": True, "reason": "test"})
        client.post(f"{API}/autonomy/emergency-stop", json={"active": False, "reason": "cleared"})

        result = client.post(
            f"{API}/autonomy/decisions/run",
            json={"opportunity_id": body["opportunity_id"]},
        ).json()["decision"]
        assert result["outcome"] == "authorized", result["reason"]

    def test_disabling_autonomy_returns_to_human_approval(self, client):
        enable(client, capital="500", level=4)
        policy = client.post(f"{API}/autonomy/disable").json()
        assert policy["autonomy_level"] == int(AutonomyLevel.HUMAN_APPROVAL)
        assert Decimal(policy["capital_limit"]) == 0
        assert policy["require_human_approval"] is True


class TestCircuitBreakers:
    def test_breakers_exist_with_defaults_on_first_read(self, client):
        body = client.get(f"{API}/autonomy/circuit-breakers").json()
        codes = {item["code"] for item in body["items"]}
        assert {
            breakers.DAILY_LOSS,
            breakers.PORTFOLIO_DRAWDOWN,
            breakers.FAILED_DECISIONS,
            breakers.DAILY_DEPLOYMENT,
        } <= codes
        assert body["any_tripped"] is False

    def test_a_tripped_breaker_blocks_deployment(self, client, session, auth):
        body = analyze(client)
        enable(client, capital="5000")

        rows = breakers.ensure(session, auth)
        target = next(row for row in rows if row.code == breakers.DAILY_LOSS)
        target.state = "tripped"
        target.tripped_reason = "test trip"
        session.commit()

        result = client.post(
            f"{API}/autonomy/decisions/run",
            json={"opportunity_id": body["opportunity_id"]},
        ).json()["decision"]
        assert result["outcome"] == "blocked"
        assert result["reason_code"] == engine.CIRCUIT_BREAKER_TRIPPED

    def test_a_breaker_does_not_reset_itself(self, client, session, auth):
        """A breaker that clears on its own hides the thing it caught."""
        rows = breakers.ensure(session, auth)
        target = next(row for row in rows if row.code == breakers.DAILY_LOSS)
        target.state = "tripped"
        session.commit()

        breakers.evaluate(session, auth)
        session.commit()
        assert target.state == "tripped"

        reset = client.post(f"{API}/autonomy/circuit-breakers/{target.id}/reset").json()
        assert reset["state"] == "ok"


class TestShadowMode:
    def test_a_shadow_position_commits_no_real_money(self, client):
        """Tracked the same way, measured the same way, bought with nothing."""
        body = analyze(client)
        enable(client, capital="5000", mode="shadow")

        result = client.post(
            f"{API}/autonomy/decisions/run",
            json={"opportunity_id": body["opportunity_id"]},
        ).json()
        assert result["decision"]["outcome"] == "authorized"
        position = result["position"]
        assert position is not None
        assert position["execution_mode"] == "shadow"
        assert position["purchase_id"] is None, "a shadow position has no purchase behind it"
        assert Decimal(position["capital_invested"]) > 0

    def test_shadow_and_live_are_measured_separately(self, client, session, auth):
        body = analyze(client)
        enable(client, capital="5000", mode="shadow")
        client.post(
            f"{API}/autonomy/decisions/run", json={"opportunity_id": body["opportunity_id"]}
        )

        shadow = scorecard.build(session, auth, execution_mode=ExecutionMode.SHADOW)
        live = scorecard.build(session, auth, execution_mode=ExecutionMode.LIVE)
        assert shadow.decisions_total >= 1
        assert live.decisions_total == 0


class TestDecisionRecord:
    def test_an_authorised_decision_preserves_what_it_knew(self, client):
        """ "Why did Spreadline buy this" has to be answerable from the record."""
        body = analyze(client)
        enable(client, capital="5000")
        run = client.post(
            f"{API}/autonomy/decisions/run", json={"opportunity_id": body["opportunity_id"]}
        ).json()

        detail = client.get(f"{API}/autonomy/decisions/{run['decision_id']}").json()
        evidence = detail["evidence"]
        assert evidence["opportunity"]["net_profit"]
        assert evidence["economics"]["assumptions_version"]
        assert evidence["policy"]["version"] == detail["policy_version"]
        assert evidence["eligibility"]["checks"]
        assert detail["max_unit_price"], "the ceiling is part of the authorisation"
        assert detail["stage_verdicts"]

    def test_every_stage_is_recorded_with_its_verdict(self, client):
        body = analyze(client)
        enable(client, capital="5000")
        run = client.post(
            f"{API}/autonomy/decisions/run", json={"opportunity_id": body["opportunity_id"]}
        ).json()

        detail = client.get(f"{API}/autonomy/decisions/{run['decision_id']}").json()
        stages = {item["stage"] for item in detail["runs"]}
        assert {
            AgentStage.SCOUT.value,
            AgentStage.UNDERWRITING.value,
            AgentStage.RISK.value,
            AgentStage.ELIGIBILITY.value,
            AgentStage.CAPITAL.value,
            AgentStage.DECISION.value,
        } <= stages
        assert all(item["verdict"] in {v.value for v in AgentVerdict} for item in detail["runs"])

    def test_a_refusal_is_recorded_as_carefully_as_an_authorisation(self, client):
        """The refusals are the evidence the limits work."""
        body = analyze(client)
        result = client.post(
            f"{API}/autonomy/decisions/run", json={"opportunity_id": body["opportunity_id"]}
        ).json()
        assert result["decision"]["outcome"] == "blocked"

        listed = client.get(f"{API}/autonomy/decisions", params={"outcome": "blocked"}).json()
        assert listed["total"] >= 1
        assert listed["items"][0]["reason_code"]

        detail = client.get(f"{API}/autonomy/decisions/{result['decision_id']}").json()
        assert detail["evidence"]["policy"]

    def test_the_policy_version_is_frozen_onto_the_decision(self, client):
        """Later policy changes must not rewrite an earlier decision."""
        body = analyze(client)
        enable(client, capital="5000")
        run = client.post(
            f"{API}/autonomy/decisions/run", json={"opportunity_id": body["opportunity_id"]}
        ).json()
        recorded = client.get(f"{API}/autonomy/decisions/{run['decision_id']}").json()

        client.put(f"{API}/autonomy/policy", json={"minimum_roi": "0.99"})
        after = client.get(f"{API}/autonomy/decisions/{run['decision_id']}").json()

        assert after["policy_version"] == recorded["policy_version"]
        assert (
            after["evidence"]["policy"]["minimum_roi"]
            == recorded["evidence"]["policy"]["minimum_roi"]
        )


class TestDisagreement:
    """Stages ask different questions, so they can genuinely differ.

    Eligibility checks the aggregate risk level against the policy ceiling. The
    risk stage looks at the breakdown underneath it. Two stages checking the
    same threshold could never disagree, and a disagreement path that cannot
    fire is not a safeguard.
    """

    def _ready(self, client, session, auth):
        body = analyze(client)
        from app.domains.opportunities import service as opportunities

        opportunity = opportunities.get_opportunity(session, auth, body["opportunity_id"])
        policy_module.update_policy(
            session,
            auth,
            {
                "autonomy_level": int(AutonomyLevel.CONTROLLED),
                "capital_limit": Decimal("5000"),
                "execution_mode": "shadow",
                "require_human_approval": False,
            },
        )
        return opportunity

    def _assessment(self, session, opportunity):
        from sqlalchemy import select

        from app.models.opportunity import RiskAssessment

        return session.scalar(
            select(RiskAssessment)
            .where(RiskAssessment.opportunity_id == opportunity.id)
            .order_by(RiskAssessment.created_at.desc())
        )

    def test_a_high_severity_signal_escalates_even_within_the_risk_ceiling(
        self, client, session, auth
    ):
        """Averaging a severe signal into an acceptable level is how it gets waved through."""
        opportunity = self._ready(client, session, auth)
        assessment = self._assessment(session, opportunity)
        assert assessment is not None
        assessment.signals = [
            *(assessment.signals or []),
            {
                "code": "source_clearance",
                "category": "price",
                "severity": "high",
                "message": "The source price looks like clearance.",
                "blocking": False,
            },
        ]
        session.flush()

        result, _row = engine.decide(session, auth, opportunity)
        assert result.outcome == engine.ESCALATED
        assert result.reason_code == engine.AGENTS_DISAGREE
        assert "disagree" in result.reason.lower()

    def test_an_unassessable_risk_category_escalates(self, client, session, auth):
        """ "No evidence" and "no risk" produce the same level and are opposites."""
        opportunity = self._ready(client, session, auth)
        assessment = self._assessment(session, opportunity)
        assert assessment is not None
        assessment.categories = [
            {"category": "demand", "label": "Demand", "has_evidence": False, "score": "0"}
        ]
        session.flush()

        result, _row = engine.decide(session, auth, opportunity)
        assert result.outcome == engine.ESCALATED
        assert result.reason_code == engine.AGENTS_DISAGREE

    def test_agreement_authorises(self, client, session, auth):
        """The control: with nothing objecting, the same setup goes through."""
        opportunity = self._ready(client, session, auth)
        result, _row = engine.decide(session, auth, opportunity)
        assert result.outcome == engine.AUTHORIZED, result.reason

    def test_an_escalation_is_recorded_with_the_verdicts_that_differed(self, client, session, auth):
        opportunity = self._ready(client, session, auth)
        assessment = self._assessment(session, opportunity)
        assessment.signals = [
            {
                "code": "weak_demand",
                "category": "demand",
                "severity": "high",
                "message": "Demand is weak.",
                "blocking": False,
            }
        ]
        session.flush()

        _result, row = engine.decide(session, auth, opportunity)
        assert row is not None
        assert row.outcome == engine.ESCALATED
        assert row.stage_verdicts["risk"] == AgentVerdict.REVIEW.value
        assert row.stage_verdicts["eligibility"] == AgentVerdict.PROCEED.value


class TestAutonomyGate:
    def test_a_fresh_system_is_not_eligible_for_anything(self, client):
        body = client.get(f"{API}/autonomy/eligibility").json()
        assert body["eligible"] is False
        assert body["criteria"]
        assert any(not item["met"] for item in body["criteria"])

    def test_a_ratio_with_no_sample_behind_it_does_not_count(self, session, auth):
        """80% precision over four decisions is not evidence of anything."""
        card = scorecard.build(session, auth)
        requirements = gate.requirements_for(AutonomyLevel.LIMITED)
        assert requirements is not None
        evaluation = gate.evaluate(card, requirements)
        assert not evaluation.eligible
        sampled = [
            result
            for result in evaluation.results
            if result.criterion.requires_sample and not result.met
        ]
        assert sampled, "sample-backed criteria should be the ones failing"

    def test_the_gate_reports_but_never_promotes(self, client):
        """Eligibility is information. Authorisation is a person's action."""
        before = client.get(f"{API}/autonomy/policy").json()["policy"]
        client.get(f"{API}/autonomy/eligibility")
        client.get(f"{API}/autonomy/ladder")
        after = client.get(f"{API}/autonomy/policy").json()["policy"]
        assert after["autonomy_level"] == before["autonomy_level"]
        assert after["capital_limit"] == before["capital_limit"]

    def test_enabling_without_eligibility_requires_acknowledgement(self, client):
        response = client.post(
            f"{API}/autonomy/enable",
            json={"level": 3, "execution_mode": "shadow", "acknowledge_not_eligible": False},
        )
        assert response.status_code == 422
        assert "acknowledge_not_eligible" in response.json()["error"]["message"]


class TestPositions:
    def test_a_position_is_capital_not_a_stock_row(self, client):
        body = analyze(client)
        enable(client, capital="5000")
        client.post(
            f"{API}/autonomy/decisions/run", json={"opportunity_id": body["opportunity_id"]}
        )

        listed = client.get(f"{API}/autonomy/positions").json()
        assert listed["total"] == 1
        item = listed["items"][0]
        for key in (
            "capital_invested",
            "expected_profit",
            "expected_roi",
            "unrealized_value",
            "days_held",
            "risk_level",
        ):
            assert key in item
        assert Decimal(listed["portfolio"]["capital_deployed"]) > 0

    def test_selling_closes_the_position_and_realises_the_profit(self, session, auth):
        row = positions.open_position(
            session,
            auth,
            execution_mode=ExecutionMode.SHADOW,
            quantity=2,
            unit_cost=Decimal("50"),
            expected_unit_profit=Decimal("20"),
        )
        assert row.capital_invested == Decimal("100.0000")

        positions.record_sale(session, auth, row.id, quantity=2, unit_proceeds=Decimal("70"))
        assert row.status == PositionStatus.CLOSED.value
        assert row.realized_proceeds == Decimal("140.0000")
        assert row.realized_profit == Decimal("40.0000")
        assert row.closed_at is not None

    def test_a_partial_sale_leaves_the_position_open(self, session, auth):
        row = positions.open_position(
            session,
            auth,
            execution_mode=ExecutionMode.SHADOW,
            quantity=4,
            unit_cost=Decimal("25"),
        )
        positions.record_sale(session, auth, row.id, quantity=1, unit_proceeds=Decimal("40"))
        assert row.status == PositionStatus.OPEN.value
        assert row.quantity_sold == 1


class TestAuditLog:
    def test_every_autonomous_action_is_auditable(self, client):
        body = analyze(client)
        enable(client, capital="5000")
        client.post(
            f"{API}/autonomy/decisions/run", json={"opportunity_id": body["opportunity_id"]}
        )
        client.post(f"{API}/autonomy/emergency-stop", json={"active": True, "reason": "drill"})

        events = client.get(f"{API}/autonomy/events").json()["items"]
        types = [event["type"] for event in events]
        assert "emergency_stop" in types
        assert "decision_authorized" in types or "decision_blocked" in types
        assert "policy_changed" in types
        assert all(event["created_at"] for event in events)
