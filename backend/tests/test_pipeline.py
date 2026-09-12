"""End-to-end analysis pipeline against the fixture catalogue.

Each test names the decision the platform is supposed to reach and why. These are
the tests that would catch a regression in how the engines combine, which unit
tests of each engine in isolation structurally cannot.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.domains.opportunities.analysis import AnalysisOptions, analyze_pair
from app.models.enums import Marketplace, MatchStatus, OpportunityStatus, Recommendation
from app.models.observations import PriceObservation
from app.models.opportunity import Opportunity, OpportunityEvent
from app.services.providers.fixtures import FIXTURES_BY_KEY


async def analyze_fixture(session, auth, registry, key: str, **kwargs):
    """Analyse a fixture, sourcing from whichever side is cheaper."""
    fixture = FIXTURES_BY_KEY[key]
    amazon = fixture.listing(Marketplace.AMAZON)
    walmart = fixture.listing(Marketplace.WALMART)
    assert amazon and walmart, f"fixture {key} needs both listings"

    if walmart.price <= amazon.price:
        source, source_id, target, target_id = (
            Marketplace.WALMART,
            walmart.external_id,
            Marketplace.AMAZON,
            amazon.external_id,
        )
    else:
        source, source_id, target, target_id = (
            Marketplace.AMAZON,
            amazon.external_id,
            Marketplace.WALMART,
            walmart.external_id,
        )
    return await analyze_pair(
        session,
        auth,
        source_marketplace=source,
        source_external_id=source_id,
        target_marketplace=target,
        target_external_id=target_id,
        options=AnalysisOptions(**kwargs),
        registry=registry,
    )


class TestCleanOpportunity:
    async def test_a_genuine_opportunity_reaches_buy(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        assert result.decision.recommendation is Recommendation.BUY
        assert result.context.match.status is MatchStatus.CONFIRMED
        assert result.context.profitability.net_profit > 0
        assert result.score.total >= Decimal("70")

    async def test_the_explanation_is_built_from_real_values(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        roi = result.context.profitability.roi
        assert roi is not None
        assert any(f"{roi:.0%}" in reason for reason in result.decision.reasons)
        assert all(gate.detail for gate in result.decision.gates)

    async def test_every_gate_is_reported_pass_or_fail(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        assert len(result.decision.gates) >= 10
        assert all(isinstance(gate.passed, bool) for gate in result.decision.gates)

    async def test_stress_scenarios_all_run(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        assert result.stress is not None
        keys = {item.scenario.key for item in result.stress.scenarios}
        assert keys == {
            "base",
            "downside",
            "severe_downside",
            "acquisition_shock",
            "competition_shock",
            "demand_shock",
            "combined_downside",
        }

    async def test_downside_scenarios_reduce_profit(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        scenarios = {item.scenario.key: item for item in result.stress.scenarios}
        base = scenarios["base"].net_profit
        assert scenarios["severe_downside"].net_profit < scenarios["downside"].net_profit < base
        assert scenarios["combined_downside"].net_profit < base


class TestTrapsAreRejected:
    @pytest.mark.parametrize(
        "key",
        [
            "airpods-pro-generation-trap",
            "xm5-vs-xm4-trap",
            "switch-oled-accessory-trap",
            "iphone-charger-trap",
            "tide-pods-pack-trap",
            "aa-batteries-pack-trap",
            "coffee-pods-pack-trap",
            "sandisk-capacity-trap",
            "olaplex-size-trap",
            "used-condition-trap",
        ],
    )
    async def test_false_matches_never_reach_buy(self, session, auth, registry, key):
        result = await analyze_fixture(session, auth, registry, key)
        assert result.decision.recommendation is Recommendation.PASS
        assert not result.context.match.is_usable

    async def test_a_rejected_match_scores_zero_despite_a_large_spread(
        self, session, auth, registry
    ):
        """A console against its carrying case shows a 260 dollar 'profit'. If it
        scored on that, it would sort to the top of the opportunity table."""
        result = await analyze_fixture(session, auth, registry, "switch-oled-accessory-trap")
        assert result.context.profitability.net_profit > 100
        assert result.score.total == Decimal("0")
        assert result.score.raw_total is not None and result.score.raw_total > Decimal("50")
        assert result.score.gated_reason

    async def test_rejection_names_the_specific_conflict(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "sandisk-capacity-trap")
        assert "capacity" in result.decision.headline.lower()


class TestEconomicTraps:
    async def test_fees_can_erase_an_attractive_spread(self, session, auth, registry):
        """A 6.46 spread on a 19.88 item is a loss once fulfilment is counted."""
        result = await analyze_fixture(session, auth, registry, "cerave-moisturizing-cream")
        assert result.context.profitability.spread > 0
        assert result.context.profitability.net_profit < 0
        assert result.decision.recommendation is Recommendation.PASS

    async def test_weight_dominates_on_heavy_items(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "kitchenaid-mixer")
        assert result.context.profitability.net_profit < 0

    async def test_negative_spread_is_a_pass(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "bose-qc-ultra")
        assert result.decision.recommendation is Recommendation.PASS


class TestRiskRouting:
    async def test_clearance_priced_source_is_held_for_review(self, session, auth, registry):
        """The economics look excellent. The price is not repeatable."""
        result = await analyze_fixture(session, auth, registry, "crest-whitestrips-anomaly")
        assert result.context.profitability.roi > Decimal("0.5")
        assert result.decision.recommendation is Recommendation.REVIEW
        assert any(
            signal.code in {"source_clearance", "source_price_anomaly"}
            for signal in result.risk.signals
        )

    async def test_elevated_exit_price_is_held_for_review(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "lego-star-wars-spike")
        assert result.decision.recommendation is Recommendation.REVIEW
        assert any(signal.code == "target_price_elevated" for signal in result.risk.signals)

    async def test_weak_demand_blocks_a_profitable_looking_trade(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "obscure-niche-tool-weak-demand")
        assert result.context.profitability.roi > Decimal("0.3")
        assert result.decision.recommendation is Recommendation.REVIEW
        assert any(signal.code == "weak_demand" for signal in result.risk.signals)

    async def test_very_low_inventory_is_flagged(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "vitamix-a3500-low-stock")
        assert any(
            signal.code in {"very_low_source_inventory", "low_source_inventory"}
            for signal in result.risk.signals
        )

    async def test_crowded_listing_raises_competition_risk(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "airtag-4pack-competition")
        assert result.context.competition.seller_count >= 9
        assert any(signal.code == "competition" for signal in result.risk.signals)

    async def test_gated_brand_is_flagged(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "gated-brand-high-risk")
        assert any(signal.code == "brand_category_risk" for signal in result.risk.signals)

    async def test_every_risk_signal_explains_itself(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "volatile-gpu-high-risk")
        assert result.risk.signals
        assert all(signal.message for signal in result.risk.signals)
        assert all(signal.code for signal in result.risk.signals)


class TestMissingData:
    async def test_thin_history_is_not_presented_as_a_trend(self, session, auth, registry):
        """Three observations may be summarised, but never treated as a baseline."""
        result = await analyze_fixture(session, auth, registry, "new-release-thin-history")
        prices = result.context.target_prices
        year = prices.window(365)
        assert year is not None
        assert year.observation_count <= 3
        assert year.sufficient is False
        assert year.reason  # states exactly why it cannot be relied on
        # No long window earns the right to define "normal" for this listing.
        assert prices.reference_window in (None, 1, 7)
        assert result.context.target_anomaly.anomaly_type.value in {"unknown", "none"}

    async def test_absent_demand_is_none_not_zero(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "no-demand-data-product")
        assert result.context.demand.score is None
        assert result.context.demand.confidence.value == "none"
        component = result.score.component("demand")
        assert component is not None and component.has_data is False

    async def test_missing_data_lowers_the_quality_score(self, session, auth, registry):
        complete = await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        sparse = await analyze_fixture(session, auth, registry, "no-demand-data-product")
        assert sparse.context.quality.score < complete.context.quality.score

    async def test_no_counterpart_raises_rather_than_inventing_one(self, session, auth, registry):
        from app.core.errors import NotFoundError

        with pytest.raises(NotFoundError):
            await analyze_pair(
                session,
                auth,
                source_marketplace=Marketplace.AMAZON,
                source_external_id="B00E2RGPFS",
                target_marketplace=Marketplace.WALMART,
                options=AnalysisOptions(),
                registry=registry,
            )


class TestPersistence:
    async def test_analysis_persists_an_opportunity_with_its_evidence(
        self, session, auth, registry
    ):
        result = await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        session.commit()

        opportunity = session.get(Opportunity, result.opportunity_id)
        assert opportunity is not None
        assert opportunity.status == OpportunityStatus.NEW.value
        assert opportunity.score_components["components"]
        assert opportunity.explanation["reasons"]
        assert opportunity.risk_level
        assert len(opportunity.profitability_snapshots) == 7  # base plus six scenarios
        assert opportunity.risk_assessments

    async def test_assumptions_are_frozen_into_the_snapshot(self, session, auth, registry):
        """Today's fee change must not rewrite yesterday's prediction."""
        result = await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        session.commit()
        opportunity = session.get(Opportunity, result.opportunity_id)
        base = next(item for item in opportunity.profitability_snapshots if item.scenario == "base")
        assert base.assumptions["default_referral_rate"]
        assert base.assumptions_version.startswith("amazon-v1:")

    async def test_reanalysis_updates_in_place_and_logs_an_event(self, session, auth, registry):
        first = await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        session.commit()
        second = await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        session.commit()

        assert first.opportunity_id == second.opportunity_id
        count = session.scalar(select(func.count()).select_from(Opportunity))
        assert count == 1
        events = list(
            session.scalars(
                select(OpportunityEvent).where(
                    OpportunityEvent.opportunity_id == first.opportunity_id
                )
            )
        )
        assert [event.event_type for event in events] == ["created", "rescored"]

    async def test_observations_are_not_duplicated_on_reanalysis(self, session, auth, registry):
        await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        session.commit()
        before = session.scalar(select(func.count()).select_from(PriceObservation))
        await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        session.commit()
        after = session.scalar(select(func.count()).select_from(PriceObservation))
        # Only the new current-price snapshot may be added.
        assert after - before <= 2

    async def test_listings_are_linked_to_one_canonical_product(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "sony-wh1000xm5")
        session.commit()
        opportunity = session.get(Opportunity, result.opportunity_id)
        from app.models.catalog import MarketplaceListing

        source = session.get(MarketplaceListing, opportunity.source_listing_id)
        target = session.get(MarketplaceListing, opportunity.target_listing_id)
        assert source.product_id == target.product_id

    async def test_rejected_match_does_not_unify_products(self, session, auth, registry):
        result = await analyze_fixture(session, auth, registry, "sandisk-capacity-trap")
        session.commit()
        from app.models.catalog import MarketplaceListing

        opportunity = session.get(Opportunity, result.opportunity_id)
        source = session.get(MarketplaceListing, opportunity.source_listing_id)
        target = session.get(MarketplaceListing, opportunity.target_listing_id)
        assert source.product_id != target.product_id


class TestBothDirections:
    async def test_both_directions_are_evaluated(self, session, auth, registry):
        from app.domains.opportunities.analysis import analyze_both_directions

        results = await analyze_both_directions(
            session,
            auth,
            amazon_external_id="B09XS7JWHH",
            walmart_external_id="WM-598712344",
            options=AnalysisOptions(run_stress_test=False),
            registry=registry,
        )
        session.commit()
        assert len(results) == 2
        directions = {item.context.direction.value for item in results}
        assert directions == {"amazon_to_walmart", "walmart_to_amazon"}
        # Buying on the dearer side and selling on the cheaper one must not be
        # profitable; the platform should find exactly one workable direction.
        profitable = [r for r in results if r.context.profitability.net_profit > 0]
        assert len(profitable) == 1
