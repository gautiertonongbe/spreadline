"""Analysing a pair somebody typed in by hand.

The path that needs no credential, no key and nobody's approval. A person reads
two public pages and types what they saw, which is not scraping and cannot be:
nothing fetches. These tests hold two things. That a hand-entered price is
treated as a real observation, because nothing about it is invented. And that it
is judged by exactly the standard an API-fed pair is, including being refused
when the evidence is thin.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.domains.opportunities import manual
from app.models.enums import Marketplace
from app.models.observations import PriceObservation

API = "/api/v1"


def side(**changes):
    base = dict(
        marketplace=Marketplace.WALMART,
        external_id="WM-123456",
        title="Ninja AF101 Air Fryer 4 Qt, Grey",
        price=Decimal("56.00"),
        brand="Ninja",
        category="home & kitchen",
        identifiers={"upc": "622356561235"},
    )
    base.update(changes)
    return manual.ManualSide(**base)


def exit_side(**changes):
    base = dict(
        marketplace=Marketplace.AMAZON,
        external_id="B07FDJMC9Q",
        price=Decimal("89.99"),
        sales_rank=412,
        seller_count=6,
        review_count=52000,
    )
    base.update(changes)
    return side(**base)


def payload(source_over=None, target_over=None):
    source = {
        "marketplace": "walmart",
        "external_id": "WM-123456",
        "title": "Ninja AF101 Air Fryer 4 Qt, Grey",
        "price": "56.00",
        "brand": "Ninja",
        "category": "home & kitchen",
        "identifiers": {"upc": "622356561235"},
    }
    target = {
        "marketplace": "amazon",
        "external_id": "B07FDJMC9Q",
        "title": "Ninja AF101 Air Fryer 4 Qt, Grey",
        "price": "89.99",
        "brand": "Ninja",
        "category": "home & kitchen",
        "identifiers": {"upc": "622356561235"},
        "sales_rank": 412,
        "seller_count": 6,
    }
    source.update(source_over or {})
    target.update(target_over or {})
    return {"source": source, "target": target}


class TestItCountsAsReal:
    def test_a_hand_entered_price_is_not_simulated(self, session, auth):
        """A person read a real page. Nothing about it is invented.

        This is the opposite of the sandbox decision, and deliberately so: a
        sandbox calls real infrastructure and makes the number up, and this makes
        nothing up at all.
        """
        manual.record_side(session, auth, side())
        session.flush()

        row = session.scalars(select(PriceObservation)).first()
        assert row is not None
        assert row.is_simulated is False
        assert row.provider == manual.MANUAL_PROVIDER
        assert row.source == manual.SOURCE_MANUAL

    def test_it_is_attributed_to_a_human_rather_than_an_endpoint(self, session, auth):
        manual.record_side(session, auth, side())
        session.flush()
        row = session.scalars(select(PriceObservation)).first()
        assert row.provider == "manual", "the history has to say which prices a person typed"

    def test_the_quality_score_is_high_but_not_perfect(self, session, auth):
        """People mistype, and read the subscription price by mistake."""
        assert Decimal("70") < manual.MANUAL_QUALITY < Decimal("100")


class TestWhatItRefusesToInvent:
    def test_no_rank_means_no_demand_point_rather_than_a_zero(self, session, auth):
        from app.models.observations import DemandObservation

        manual.record_side(session, auth, side(sales_rank=None, review_count=None))
        session.flush()
        assert session.scalars(select(DemandObservation)).all() == []

    def test_no_seller_count_means_no_competition_point(self, session, auth):
        from app.models.observations import CompetitionObservation

        manual.record_side(session, auth, side(seller_count=None, offer_count=None))
        session.flush()
        assert session.scalars(select(CompetitionObservation)).all() == []

    def test_a_rank_that_is_supplied_is_recorded(self, session, auth):
        from app.models.observations import DemandObservation

        manual.record_side(session, auth, side(sales_rank=412))
        session.flush()
        rows = session.scalars(select(DemandObservation)).all()
        assert len(rows) == 1
        assert rows[0].sales_rank == 412


class TestGapsAreStatedBeforeTheVerdict:
    def test_missing_identifiers_are_called_out(self, session, auth):
        """The fix costs ten seconds; the refusal costs a repeat."""
        result = manual.record_pair(
            session,
            auth,
            source=side(identifiers={}),
            target=exit_side(identifiers={}),
        )
        assert result["shared_identifiers"] == []
        assert any("caps a title-only match at 60%" in note for note in result["warnings"])

    def test_an_identifier_on_both_sides_clears_the_warning(self, session, auth):
        result = manual.record_pair(session, auth, source=side(), target=exit_side())
        assert result["shared_identifiers"] == ["upc"]
        assert not any("title-only" in note for note in result["warnings"])

    def test_an_identifier_on_only_one_side_does_not_count(self, session, auth):
        result = manual.record_pair(
            session, auth, source=side(), target=exit_side(identifiers={})
        )
        assert result["shared_identifiers"] == []

    def test_a_missing_category_says_it_moves_the_fee(self, session, auth):
        result = manual.record_pair(
            session, auth, source=side(), target=exit_side(category=None)
        )
        assert any("referral fee" in note for note in result["warnings"])

    def test_missing_demand_and_competition_are_both_named(self, session, auth):
        result = manual.record_pair(
            session,
            auth,
            source=side(),
            target=exit_side(sales_rank=None, review_count=None, seller_count=None),
        )
        assert any("demand" in note for note in result["warnings"])
        assert any("competition" in note for note in result["warnings"])


class TestValidation:
    def test_the_same_marketplace_on_both_sides_is_refused(self, session, auth):
        with pytest.raises(ValidationError) as caught:
            manual.record_pair(
                session, auth, source=side(), target=side(external_id="WM-999")
            )
        assert "round trip" in caught.value.detail

    def test_a_price_of_zero_is_refused_as_an_absence_not_a_price(self, session, auth):
        with pytest.raises(ValidationError):
            manual.record_pair(
                session, auth, source=side(price=Decimal("0")), target=exit_side()
            )

    def test_a_missing_identifier_is_refused_with_the_reason(self, session, auth):
        with pytest.raises(ValidationError) as caught:
            manual.record_pair(session, auth, source=side(external_id="  "), target=exit_side())
        assert "ASIN" in caught.value.detail


class TestApi:
    def test_a_hand_entered_pair_runs_the_whole_engine(self, client):
        response = client.post(f"{API}/products/analyze/manual", json=payload())
        assert response.status_code == 200, response.text
        body = response.json()

        # The same output an API-fed analysis produces, from the same code.
        assert body["economics"]["net_profit"]
        assert body["decision"]["recommendation"] in {"buy", "review", "pass"}
        assert body["entry"]["method"] == "manual"
        assert body["entry"]["shared_identifiers"] == ["upc"]

    def test_no_provider_is_called(self, client):
        """The point of the whole path. Nothing fetches."""
        response = client.post(f"{API}/products/analyze/manual", json=payload())
        assert response.status_code == 200
        notes = " ".join(response.json().get("notes") or [])
        assert "no provider calls" in notes

    def test_the_profit_is_the_real_arithmetic(self, client):
        body = client.post(f"{API}/products/analyze/manual", json=payload()).json()
        economics = body["economics"]
        # Sold at 89.99, bought at 56.00, and the fees are the engine's own.
        assert Decimal(economics["sale_price"]) == Decimal("89.9900")
        assert Decimal(economics["acquisition_cost"]) == Decimal("56.0000")
        assert Decimal(economics["total_fees"]) > 0

    def test_the_same_marketplace_twice_is_a_422(self, client):
        response = client.post(
            f"{API}/products/analyze/manual",
            json=payload(target_over={"marketplace": "walmart"}),
        )
        assert response.status_code == 422

    def test_the_gaps_are_returned_with_the_verdict(self, client):
        response = client.post(
            f"{API}/products/analyze/manual",
            json=payload(
                source_over={"identifiers": {}}, target_over={"identifiers": {}}
            ),
        )
        assert response.status_code == 200
        assert any("title-only" in note for note in response.json()["entry"]["gaps"])

    def test_entering_the_same_pair_twice_builds_a_history(self, client):
        """Two visits to the same page is two observations, which is the point.

        Type it in again next week and the platform has a price series nobody
        needed a credential to collect.
        """
        client.post(f"{API}/products/analyze/manual", json=payload())
        second = client.post(
            f"{API}/products/analyze/manual",
            json=payload(target_over={"price": "94.50"}),
        )
        assert second.status_code == 200

        history = client.get(f"{API}/history").json()
        assert history["price_observations"] >= 4
        assert history["price_observations_real"] >= 4, "a person typed them, so they are real"


class TestItIsNotCalledFixtureData:
    """A price somebody read with their own eyes is not fixture data.

    The interface labels an analysis as fixture or live from whether the
    listing's provider is live. "manual" is not a provider and the registry
    cannot answer for it, so the generic fallback called a hand-verified price
    invented, which is a worse lie than the one that rule exists to prevent.
    """

    def test_both_sides_are_marked_as_real_data(self, client):
        body = client.post(f"{API}/products/analyze/manual", json=payload()).json()
        summary = body["summary"]
        assert summary["source"]["is_live_data"] is True
        assert summary["target"]["is_live_data"] is True
        assert summary["is_live_data"] is True

    def test_nothing_it_writes_is_counted_as_simulated(self, client):
        client.post(f"{API}/products/analyze/manual", json=payload())
        history = client.get(f"{API}/history").json()
        assert history["price_observations_real"] >= 2
        assert history["price_observations_simulated"] == 0

    def test_the_history_counts_it_as_a_real_observation(self, client):
        client.post(f"{API}/products/analyze/manual", json=payload())
        history = client.get(f"{API}/history").json()
        assert history["price_observations_real"] >= 2, "a person looked at the page"
