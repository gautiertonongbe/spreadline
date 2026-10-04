"""The Amazon Reviews 2023 dataset importer.

The dataset is free, real and three years old, and that last fact is the whole
risk. An importer that stamped these prices with today's date would tell the
statistics engine it has current data: every median would be poisoned, and the
anomaly detector would compare a live price against a 2023 baseline while
believing both were current. Most of what is asserted here is about honesty of
provenance rather than about parsing.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.domains.catalog.dataset_import import (
    DEFAULT_CRAWL_DATE,
    NO_ASIN,
    NO_PRICE,
    NO_TITLE,
    import_file,
    parse_category,
    parse_price,
    parse_weight_lb,
    prepare,
    read_rows,
    scan_file,
    to_listing,
)
from app.models.catalog import MarketplaceListing, Product
from app.models.enums import Availability, Condition, IdentifierType, Marketplace
from app.models.observations import PriceObservation

OBSERVED_AT = DEFAULT_CRAWL_DATE


def item(**overrides: object) -> dict:
    """A dataset row in the shape the real files use."""
    row = {
        "main_category": "All Electronics",
        "title": "Sony WH-1000XM5 Wireless Noise Cancelling Headphones",
        "average_rating": 4.6,
        "rating_number": 2841,
        "features": ["Industry leading noise cancellation"],
        "description": ["Two processors control eight microphones."],
        "price": 348.0,
        "store": "Sony",
        "categories": ["Electronics", "Headphones", "Over-Ear Headphones"],
        "details": {
            "Brand": "Sony",
            "Manufacturer": "Sony Electronics",
            "Item model number": "WH-1000XM5",
            "Item Weight": "8.8 ounces",
            "Color": "Black",
        },
        "parent_asin": "B09XS7JWHH",
        "bought_together": None,
    }
    row.update(overrides)
    return row


def write_dataset(path: Path, rows: list[dict], *, compress: bool = True) -> Path:
    payload = "\n".join(json.dumps(row) for row in rows) + "\n"
    if compress:
        path.write_bytes(gzip.compress(payload.encode()))
    else:
        path.write_text(payload)
    return path


class TestParsePrice:
    """The field is documented as a float and is not one in practice."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (12.99, Decimal("12.99")),
            (7, Decimal("7")),
            ("$12.99", Decimal("12.99")),
            ("from $9.99", Decimal("9.99")),
            ("$1,299.00", Decimal("1299.00")),
        ],
    )
    def test_reads_the_shapes_the_files_actually_contain(self, raw, expected):
        assert parse_price(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "None", 0, 0.0, -5])
    def test_an_unreadable_price_is_absent_not_zero(self, raw):
        """A product with no price is not a product priced at zero.

        Defaulting here would put free inventory into the catalogue and every
        spread computed against it would be fiction.
        """
        assert parse_price(raw) is None


class TestParseWeight:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1.5 pounds", Decimal("1.50")),
            ("2 lbs", Decimal("2.00")),
            ("8 ounces", Decimal("0.50")),
            ("500 g", Decimal("1.10")),
            ("1.2 kg", Decimal("2.65")),
        ],
    )
    def test_converts_every_unit_the_field_uses(self, raw, expected):
        assert parse_weight_lb({"Item Weight": raw}) == expected

    def test_an_implausible_weight_is_refused(self):
        """A wrong weight silently changes every fulfilment fee.

        Dimension strings parse into absurd numbers often enough that a
        plausibility guard is cheaper than the fee error it prevents.
        """
        assert parse_weight_lb({"Item Weight": "9000 pounds"}) is None
        assert parse_weight_lb({"Item Weight": "0.0001 ounces"}) is None

    def test_missing_or_malformed_details_yield_nothing(self):
        assert parse_weight_lb({}) is None
        assert parse_weight_lb({"Item Weight": "one pound"}) is None
        assert parse_weight_lb("8.8 ounces") is None
        assert parse_weight_lb(None) is None


class TestParseCategory:
    def test_prefers_the_leaf_because_fee_tables_key_on_it(self):
        assert parse_category(item()) == "Over-Ear Headphones"

    def test_falls_back_to_the_main_category(self):
        assert parse_category(item(categories=[])) == "All Electronics"

    def test_reports_absence_rather_than_guessing(self):
        assert parse_category(item(categories=[], main_category=None)) is None


class TestToListing:
    def test_maps_a_row_onto_the_provider_contract(self):
        listing = to_listing(item(), observed_at=OBSERVED_AT)
        assert listing is not None
        assert listing.marketplace is Marketplace.AMAZON
        assert listing.external_id == "B09XS7JWHH"
        assert listing.model == "WH-1000XM5"
        assert listing.price == Decimal("348.00")
        assert listing.review_count == 2841
        assert listing.rating == Decimal("4.6")
        assert listing.observed_at == OBSERVED_AT
        assert listing.provider == "amazon_reviews_2023"

    def test_the_asin_travels_as_an_identifier(self):
        listing = to_listing(item(), observed_at=OBSERVED_AT)
        assert listing is not None
        assert [(i.identifier_type, i.value) for i in listing.identifiers] == [
            (IdentifierType.ASIN.value, "B09XS7JWHH")
        ]

    def test_a_recovered_weight_removes_a_fee_assumption(self):
        listing = to_listing(item(), observed_at=OBSERVED_AT)
        assert listing is not None
        assert listing.attributes["weight_lb"] == "0.55"
        assert listing.attributes["dataset"] == "amazon_reviews_2023"

    def test_what_the_dataset_does_not_say_is_left_unknown(self):
        """The file carries no stock, no offers and no rank.

        Every one of those changes a decision, so each is reported as absent
        rather than filled in with a plausible default.
        """
        listing = to_listing(item(), observed_at=OBSERVED_AT)
        assert listing is not None
        assert listing.availability is Availability.UNKNOWN
        assert listing.seller_count is None
        assert listing.offer_count is None
        assert listing.sales_rank is None
        assert listing.condition is Condition.NEW

    @pytest.mark.parametrize("missing", [{"parent_asin": ""}, {"title": "  "}])
    def test_a_row_without_an_identity_is_unusable(self, missing):
        assert to_listing(item(**missing), observed_at=OBSERVED_AT) is None


class TestPrepare:
    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"parent_asin": ""}, NO_ASIN),
            ({"title": ""}, NO_TITLE),
            ({"price": None}, NO_PRICE),
            ({"price": "call for pricing"}, NO_PRICE),
        ],
    )
    def test_names_why_a_row_was_dropped(self, overrides, reason):
        assert prepare(item(**overrides), observed_at=OBSERVED_AT).skipped == reason

    def test_a_complete_row_is_usable(self):
        row = prepare(item(), observed_at=OBSERVED_AT)
        assert row.skipped is None
        assert row.is_usable


class TestReadRows:
    def test_streams_gzip_and_plain_alike(self, tmp_path):
        rows = [item(), item(parent_asin="B000000002")]
        gz = write_dataset(tmp_path / "meta_Test.jsonl.gz", rows)
        plain = write_dataset(tmp_path / "meta_Test.jsonl", rows, compress=False)
        assert len(list(read_rows(gz))) == 2
        assert len(list(read_rows(plain))) == 2

    def test_a_malformed_line_does_not_abort_an_import_hours_in(self, tmp_path):
        path = tmp_path / "meta_Test.jsonl"
        path.write_text(
            "\n".join([json.dumps(item()), "{not json", json.dumps(item(parent_asin="B2"))]) + "\n"
        )
        assert len(list(read_rows(path))) == 2

    def test_limit_stops_the_read(self, tmp_path):
        path = write_dataset(tmp_path / "meta_Test.jsonl.gz", [item()] * 10)
        assert len(list(read_rows(path, limit=3))) == 3


class TestImport:
    """What reaches the database, and with what provenance."""

    @pytest.fixture
    def dataset(self, tmp_path) -> Path:
        return write_dataset(
            tmp_path / "meta_Electronics.jsonl.gz",
            [
                item(),
                item(parent_asin="B07FDJMC9Q", title="Ninja AF101 Air Fryer", price="$89.99"),
                item(parent_asin="B0NOPRICE", price=None),
                item(parent_asin="", title="No identity here"),
            ],
        )

    def test_prices_are_dated_to_the_crawl_and_never_to_now(self, session, auth, dataset):
        """The single decision the whole module exists to get right.

        A 2023 price written with today's timestamp would be treated as a
        current market observation: it would set the reference median, and the
        anomaly detector would measure live prices against it.
        """
        import_file(session, auth, dataset)

        observations = list(session.scalars(select(PriceObservation)))
        assert observations
        for observation in observations:
            stored = observation.observed_at
            stored = stored if stored.tzinfo else stored.replace(tzinfo=UTC)
            assert stored == DEFAULT_CRAWL_DATE
            assert stored < datetime.now(UTC)

    def test_a_crawled_price_is_a_real_observation_not_a_simulated_one(
        self, session, auth, dataset
    ):
        """Old is not the same as fabricated.

        These are prices Amazon actually showed, from a published crawl. What
        makes them unusable as current data is their age, which the timestamp
        already states and the freshness logic already acts on. Marking them
        simulated would conflate the two and understate the only real data the
        platform has without a provider key.
        """
        import_file(session, auth, dataset)
        rows = list(session.scalars(select(PriceObservation)))
        assert rows
        assert all(row.is_simulated is False for row in rows)
        assert all(row.source == "dataset" for row in rows)

    def test_every_row_is_labelled_as_dataset_provenance(self, session, auth, dataset):
        """Distinguishable from a live poll in the database and in any audit."""
        import_file(session, auth, dataset)
        observations = list(session.scalars(select(PriceObservation)))
        assert {observation.source for observation in observations} == {"dataset"}
        assert {observation.provider for observation in observations} == {"amazon_reviews_2023"}

    def test_a_custom_crawl_date_is_honoured(self, session, auth, dataset):
        crawl = datetime(2022, 3, 1, tzinfo=UTC)
        import_file(session, auth, dataset, crawl_date=crawl)
        for observation in session.scalars(select(PriceObservation)):
            stored = observation.observed_at
            stored = stored if stored.tzinfo else stored.replace(tzinfo=UTC)
            assert stored == crawl

    def test_rows_without_a_price_or_an_identity_are_skipped_not_defaulted(
        self, session, auth, dataset
    ):
        stats = import_file(session, auth, dataset)
        assert stats.read == 4
        assert stats.imported == 2
        assert stats.skipped_no_price == 1
        assert stats.skipped_no_asin == 1
        assert stats.price_observations == 2

        listings = list(session.scalars(select(MarketplaceListing)))
        assert {listing.external_id for listing in listings} == {"B09XS7JWHH", "B07FDJMC9Q"}
        assert session.scalars(select(Product)).all()

    def test_weights_are_counted_so_the_fee_assumption_is_visible(self, session, auth, dataset):
        stats = import_file(session, auth, dataset)
        assert stats.with_weight == stats.imported

    def test_a_dry_run_reports_exactly_what_an_import_would_write(self, session, auth, dataset):
        """The preview and the import share one mapping, so they cannot drift.

        A category file takes a long time; a preview that disagreed with the
        import would be worse than no preview at all.
        """
        scanned = scan_file(dataset)
        imported = import_file(session, auth, dataset)

        assert scanned.usable == imported.imported
        assert scanned.read == imported.read
        assert scanned.with_weight == imported.with_weight
        assert scanned.skipped_no_price == imported.skipped_no_price
        assert scanned.skipped_no_asin == imported.skipped_no_asin

    def test_a_dry_run_writes_nothing(self, session, auth, dataset):
        scan_file(dataset)
        assert session.scalars(select(MarketplaceListing)).all() == []
        assert session.scalars(select(PriceObservation)).all() == []

    def test_reimporting_the_same_file_does_not_duplicate_history(self, session, auth, dataset):
        """Observations dedupe by calendar day for non-live sources.

        Running the importer twice is a normal thing to do, and it must not
        double the observation count behind every median.
        """
        first = import_file(session, auth, dataset)
        before = len(list(session.scalars(select(PriceObservation))))
        second = import_file(session, auth, dataset)
        after = len(list(session.scalars(select(PriceObservation))))

        assert first.price_observations == before
        assert second.price_observations == 0
        assert after == before

    def test_limit_bounds_the_read(self, session, auth, dataset):
        stats = import_file(session, auth, dataset, limit=1)
        assert stats.read == 1
        assert stats.imported == 1
