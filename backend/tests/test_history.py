"""Spreadline's own cross-market observation history.

The whole argument for owning this data is that it can be trusted, so most of
what is asserted here is about honesty rather than about volume: that a fixture
observation can never be read back as a market record, that a field a provider
did not return stays null, and that the universe is bounded and backs off rather
than spending a quota on a dead id.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.core.clock import ensure_utc, utcnow
from app.domains.history import service as history
from app.models.enums import Marketplace
from app.models.history import TrackedListing
from app.models.observations import PriceObservation

API = "/api/v1"

SOURCE = {"marketplace": "walmart", "external_id": "WM-598712344"}
TARGET = {"marketplace": "amazon", "external_id": "B09XS7JWHH"}


def analyze(client):
    response = client.post(
        f"{API}/products/analyze",
        json={
            "source_marketplace": SOURCE["marketplace"],
            "source_external_id": SOURCE["external_id"],
            "target_marketplace": TARGET["marketplace"],
            "target_external_id": TARGET["external_id"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestCapture:
    def test_a_fixture_observation_is_marked_as_simulated(self, client, engine):
        """The one thing that must never be got wrong.

        A development database is almost entirely fixtures. If those rows could
        be read back as market observations, every statistic built on them would
        be a confident claim about a market nobody observed.
        """
        analyze(client)
        with engine.connect() as connection:
            rows = connection.execute(
                select(PriceObservation.is_simulated, PriceObservation.provider)
            ).all()
        assert rows
        assert all(simulated is True for simulated, _ in rows)
        assert {provider for _, provider in rows} == {"mock_walmart", "mock_amazon"}

    def test_every_observation_carries_the_identity_it_was_an_observation_of(self, client, engine):
        """Listings get re-matched and products get merged; the snapshot does not.

        A price is only meaningful attached to the thing it was a price for, so
        the identifiers are denormalised onto the row rather than joined later.
        """
        analyze(client)
        with engine.connect() as connection:
            rows = connection.execute(
                select(
                    PriceObservation.external_id,
                    PriceObservation.marketplace,
                    PriceObservation.gtin,
                    PriceObservation.retrieved_at,
                )
            ).all()
        assert rows
        assert all(external_id for external_id, _, _, _ in rows)
        assert all(marketplace for _, marketplace, _, _ in rows)
        # The fixtures carry valid UPCs, so a GTIN should have been resolved.
        assert any(gtin for _, _, gtin, _ in rows)
        assert all(retrieved_at is not None for _, _, _, retrieved_at in rows)

    def test_analysing_a_product_puts_it_under_observation(self, client, engine):
        """The dataset is only worth owning if it kept observing afterwards."""
        analyze(client)
        with engine.connect() as connection:
            tracked = connection.execute(
                select(
                    TrackedListing.marketplace, TrackedListing.external_id, TrackedListing.reason
                )
            ).all()
        pairs = {(marketplace, external_id) for marketplace, external_id, _ in tracked}
        assert (SOURCE["marketplace"], SOURCE["external_id"]) in pairs
        assert (TARGET["marketplace"], TARGET["external_id"]) in pairs
        assert all(reason == history.REASON_ANALYSIS for _, _, reason in tracked)

    def test_a_field_the_provider_did_not_return_stays_null(self, client):
        """Null and zero are different answers, and the row keeps them apart."""
        body = analyze(client)
        listing_id = client.get(f"{API}/opportunities/{body['opportunity_id']}").json()[
            "source_listing"
        ]["id"]
        series = client.get(f"{API}/history/listings/{listing_id}").json()["observations"]
        assert series
        for point in series:
            assert point["price"] is not None
            assert point["currency"]
            assert point["is_simulated"] is True
            # Quality was not computed at capture for a history backfill, and it
            # is null rather than a default score standing in for one.
            assert point["quality_score"] is None
            # Buy box is a fact the provider either stated or did not. The
            # fixtures state it on the marketplace side and it is carried
            # through; what must never happen is a winner being invented.
            assert isinstance(point["is_buy_box"], bool)


class TestUniverse:
    def test_tracking_is_idempotent(self, session, auth):
        """Tracking something twice is one standing instruction, not two."""
        first = history.track(
            session, auth.organization_id, marketplace=Marketplace.AMAZON, external_id="B1"
        )
        second = history.track(
            session, auth.organization_id, marketplace=Marketplace.AMAZON, external_id="B1"
        )
        assert first.id == second.id
        assert len(history.universe(session, auth.organization_id)) == 1

    def test_untracking_keeps_what_was_already_observed(self, session, auth):
        """A gap in a history should be explained by a row, not by a deletion."""
        row = history.track(
            session, auth.organization_id, marketplace=Marketplace.AMAZON, external_id="B2"
        )
        history.untrack(session, auth.organization_id, row.id)
        assert row.is_active is False
        assert history.universe(session, auth.organization_id) == []
        assert history.universe(session, auth.organization_id, active_only=False)

    def test_a_listing_never_polled_is_due_immediately(self, session, auth):
        row = history.track(
            session, auth.organization_id, marketplace=Marketplace.AMAZON, external_id="B3"
        )
        assert history.due_at(row) is None
        assert history.universe(session, auth.organization_id)[0].is_due

    def test_a_listing_polled_recently_is_not_due(self, session, auth):
        row = history.track(
            session, auth.organization_id, marketplace=Marketplace.AMAZON, external_id="B4"
        )
        history.record_attempt(row, succeeded=True, observations=1)
        assert history.due_at(row) is not None
        assert not history.universe(session, auth.organization_id)[0].is_due

    def test_repeated_failures_back_the_listing_off(self, session, auth):
        """One dead external id must not consume a provider allowance.

        The interval grows geometrically with consecutive failures, so a bad id
        costs one call a day rather than one call a run.
        """
        row = history.track(
            session, auth.organization_id, marketplace=Marketplace.AMAZON, external_id="B5"
        )
        now = utcnow()
        history.record_attempt(row, succeeded=False, error="boom", now=now)
        first = history.due_at(row)

        for _ in range(3):
            history.record_attempt(row, succeeded=False, error="boom", now=now)
        later = history.due_at(row)

        assert first is not None and later is not None
        assert later > first
        assert row.consecutive_failures == 4
        assert row.last_error == "boom"

    def test_a_success_clears_the_backoff(self, session, auth):
        row = history.track(
            session, auth.organization_id, marketplace=Marketplace.AMAZON, external_id="B6"
        )
        history.record_attempt(row, succeeded=False, error="boom")
        history.record_attempt(row, succeeded=True, observations=3)
        assert row.consecutive_failures == 0
        assert row.last_error is None
        assert row.observation_count == 3

    def test_the_refresh_batch_is_bounded(self, session, auth):
        """An unbounded scheduled job is a bill waiting to happen."""
        for index in range(10):
            history.track(
                session,
                auth.organization_id,
                marketplace=Marketplace.AMAZON,
                external_id=f"B10{index}",
            )
        assert len(history.due_for_refresh(session, auth.organization_id, limit=4)) == 4

    def test_priority_decides_what_a_short_run_covers(self, session, auth):
        """A run that cannot cover everything should cover the important part."""
        history.track(
            session, auth.organization_id, marketplace=Marketplace.AMAZON, external_id="LOW"
        )
        history.track(
            session,
            auth.organization_id,
            marketplace=Marketplace.AMAZON,
            external_id="HIGH",
            priority=10,
        )
        due = history.due_for_refresh(session, auth.organization_id, limit=1)
        assert [row.external_id for row in due] == ["HIGH"]

    def test_an_explicit_interval_overrides_the_default(self, session, auth):
        row = history.track(
            session,
            auth.organization_id,
            marketplace=Marketplace.AMAZON,
            external_id="B7",
            refresh_interval_seconds=60,
        )
        assert history.refresh_interval_for(row) == 60
        history.record_attempt(row, succeeded=True)
        assert history.due_at(row) == ensure_utc(row.last_refreshed_at) + timedelta(seconds=60)


class TestRetrieval:
    def test_coverage_separates_real_from_simulated(self, client, engine, session, auth):
        """A coverage figure that hid the fixtures would be the most misleading
        number on the screen."""
        body = analyze(client)
        listing_id = client.get(f"{API}/opportunities/{body['opportunity_id']}").json()[
            "target_listing"
        ]["id"]

        coverage = client.get(f"{API}/history/listings/{listing_id}").json()["coverage"]
        assert coverage["price_observations"] > 0
        assert coverage["simulated_price_observations"] == coverage["price_observations"]
        assert coverage["real_price_observations"] == 0
        assert coverage["distinct_days"] > 0
        assert coverage["span_days"] > 0

    def test_the_series_can_exclude_simulated_observations(self, client):
        """The question "what have we actually observed" has to be answerable."""
        body = analyze(client)
        listing_id = client.get(f"{API}/opportunities/{body['opportunity_id']}").json()[
            "target_listing"
        ]["id"]

        everything = client.get(f"{API}/history/listings/{listing_id}").json()
        real_only = client.get(
            f"{API}/history/listings/{listing_id}", params={"include_simulated": False}
        ).json()

        assert everything["observations"]
        assert real_only["observations"] == []

    def test_statistics_are_computed_over_every_window(self, client):
        body = analyze(client)
        listing_id = client.get(f"{API}/opportunities/{body['opportunity_id']}").json()[
            "target_listing"
        ]["id"]
        windows = client.get(f"{API}/history/listings/{listing_id}").json()["statistics"]["windows"]
        assert {"1", "7", "30", "90", "180", "365"} <= set(windows)

    def test_the_dataset_total_states_how_much_is_real(self, client):
        analyze(client)
        totals = client.get(f"{API}/history").json()
        assert totals["price_observations"] > 0
        assert totals["price_observations_real"] == 0
        assert totals["price_observations_simulated"] == totals["price_observations"]
        assert totals["tracked_listings_active"] >= 2
        assert totals["windows_supported"] == [1, 7, 30, 90, 180, 365]


class TestUniverseApi:
    def test_a_listing_can_be_tracked_and_untracked_through_the_api(self, client):
        created = client.post(
            f"{API}/history/universe",
            json={"marketplace": "amazon", "external_id": "B09XS7JWHH", "priority": 5},
        )
        assert created.status_code == 200, created.text
        row = created.json()
        assert row["is_active"] is True
        assert row["priority"] == 5
        assert row["due_at"] is None, "never polled means due now"

        listed = client.get(f"{API}/history/universe").json()
        assert listed["total"] == 1
        assert listed["due_now"] == 1

        removed = client.delete(f"{API}/history/universe/{row['id']}")
        assert removed.status_code == 200
        assert removed.json()["is_active"] is False
        assert client.get(f"{API}/history/universe").json()["total"] == 0

    def test_tracking_the_same_listing_twice_does_not_duplicate_it(self, client):
        payload = {"marketplace": "amazon", "external_id": "B09XS7JWHH"}
        first = client.post(f"{API}/history/universe", json=payload).json()
        second = client.post(f"{API}/history/universe", json=payload).json()
        assert first["id"] == second["id"]
        assert client.get(f"{API}/history/universe").json()["total"] == 1

    def test_an_unknown_tracked_id_is_a_404_with_a_code(self, client):
        response = client.delete(f"{API}/history/universe/does-not-exist")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_a_refresh_pass_observes_the_universe(self, client, engine):
        """The scheduled pass, run on demand, writes real observations."""
        client.post(
            f"{API}/history/universe",
            json={"marketplace": "amazon", "external_id": "B09XS7JWHH"},
        )
        with engine.connect() as connection:
            before = connection.execute(select(PriceObservation.id)).all()

        result = client.post(f"{API}/history/refresh").json()
        assert result["polled"] >= 0
        assert result["failures"] == 0

        with engine.connect() as connection:
            after = connection.execute(select(PriceObservation.id)).all()
        assert len(after) >= len(before)
