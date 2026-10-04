"""Identity: normalisation, variation detection and matching.

These are the tests that protect the most expensive class of mistake the platform
can make, so they are written as claims about behaviour rather than as coverage.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.clock import utcnow
from app.domains.identity.matcher import MatchCandidate, match_listings
from app.domains.identity.normalization import (
    gtin_check_digit,
    is_valid_gtin,
    normalize_brand,
    normalize_gtin,
    normalize_identifier,
    normalize_mpn,
)
from app.domains.identity.similarity import title_similarity
from app.domains.identity.variation import (
    ComparisonOutcome,
    VariationDimension,
    build_profile,
    compare_profiles,
)
from app.models.enums import MatchMethod, MatchStatus


class TestGtinNormalization:
    def test_upc_becomes_gtin14(self):
        assert normalize_gtin("027242923058") == "00027242923058"

    def test_leading_zero_forms_are_equal(self):
        """The most common reason two marketplaces look like different products."""
        assert normalize_gtin("027242923058") == normalize_gtin("0027242923058")

    def test_separators_are_ignored(self):
        assert normalize_gtin("0-27242-92305-8") == normalize_gtin("027242923058")

    def test_bad_check_digit_is_rejected(self):
        assert normalize_gtin("027242923059") is None

    def test_wrong_length_is_rejected(self):
        assert normalize_gtin("12345") is None

    def test_check_digit_matches_known_value(self):
        assert gtin_check_digit("02724292305") == 8
        assert is_valid_gtin("027242923058")

    def test_invalid_identifier_reports_why(self):
        result = normalize_identifier("upc", "027242923059")
        assert result.is_valid is False
        assert "check digit" in (result.note or "")

    def test_mpn_ignores_separators_and_case(self):
        assert normalize_mpn("910-006556") == normalize_mpn("910006556") == "910006556"

    def test_brand_drops_corporate_suffix(self):
        assert normalize_brand("Sony Corporation") == normalize_brand("Sony")


class TestVariationExtraction:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Tide PODS 112 count", 112),
            ("Energizer MAX AA Batteries, 24 Pack", 24),
            ("Paper Towels, Pack of 6", 6),
            ("Socks Set of 3", 3),
            ("Sony WH-1000XM5 Headphones", None),
        ],
    )
    def test_pack_count(self, title, expected):
        assert build_profile(title).pack_count == expected

    def test_model_survives_hyphenation(self):
        assert build_profile("Sony WH-1000XM5 Headphones").model == "wh1000xm5"
        assert build_profile("Sony WH1000XM5 Headphones").model == "wh1000xm5"

    def test_year_is_not_a_model(self):
        assert build_profile("Calendar 2026 Wall Planner").model is None

    def test_capacity_and_size_are_distinguished(self):
        profile = build_profile("SanDisk Ultra 128GB microSD Card")
        assert profile.capacity is not None
        assert profile.capacity.dimension == "capacity"
        assert profile.size is None

    def test_structured_attribute_beats_title(self):
        profile = build_profile("Widget 2 Pack", {"pack_count": 6})
        assert profile.pack_count == 6

    def test_accessory_phrases_are_detected(self):
        assert build_profile("Hard Shell Case for Sony WH-1000XM5").is_accessory
        assert build_profile("Sony WH-1000XM5 Carrying Case").is_accessory
        assert not build_profile("Sony WH-1000XM5 Headphones").is_accessory

    def test_case_of_twelve_is_a_pack_not_an_accessory(self):
        """'Case of 12' is a pack size. Treating it as an accessory would reject
        every legitimate multipack in grocery."""
        profile = build_profile("Sparkling Water, Case of 12")
        assert profile.pack_count == 12
        assert profile.is_accessory is False


class TestVariationComparison:
    def test_equivalent_volumes_in_different_units_agree(self):
        left = build_profile("Detergent 1 L")
        right = build_profile("Detergent 1000 ml")
        result = compare_profiles(left, right)
        size = next(c for c in result.comparisons if c.dimension is VariationDimension.SIZE)
        assert size.outcome is ComparisonOutcome.AGREE

    def test_unstated_multipack_is_a_blocking_conflict(self):
        result = compare_profiles(
            build_profile("Detergent 92 fl oz, 6 Pack"), build_profile("Detergent 92 fl oz")
        )
        assert result.blocking_conflicts
        assert result.blocking_conflicts[0].dimension is VariationDimension.PACK_COUNT

    def test_both_silent_on_pack_count_is_unknown_not_agreement(self):
        result = compare_profiles(build_profile("Widget"), build_profile("Widget"))
        pack = next(c for c in result.comparisons if c.dimension is VariationDimension.PACK_COUNT)
        assert pack.outcome is ComparisonOutcome.UNKNOWN

    def test_model_containment_is_agreement(self):
        left = build_profile("Sony WH-1000XM5")
        right = build_profile("Sony WH1000XM5B")
        result = compare_profiles(left, right)
        model = next(c for c in result.comparisons if c.dimension is VariationDimension.MODEL)
        assert model.outcome is ComparisonOutcome.AGREE


def candidate(title: str, **kwargs) -> MatchCandidate:
    kwargs.setdefault("marketplace", "amazon")
    return MatchCandidate(listing_id=None, title=title, **kwargs)


class TestMatching:
    def test_exact_gtin_reaches_high_confidence(self):
        result = match_listings(
            candidate("Sony WH-1000XM5 Headphones", identifiers={"upc": ["027242923058"]}),
            candidate("Sony WH1000XM5 Headphones", identifiers={"upc": ["0027242923058"]}),
        )
        assert result.method is MatchMethod.GTIN
        assert result.status is MatchStatus.CONFIRMED
        assert result.confidence >= Decimal("0.95")

    def test_conflicting_gtins_are_a_rejection_not_weak_evidence(self):
        result = match_listings(
            candidate("Keurig K-Classic Black", identifiers={"upc": ["611247373637"]}),
            candidate("Keurig K-Classic Black", identifiers={"upc": ["611247373644"]}),
        )
        assert result.status is MatchStatus.REJECTED
        assert "GTIN" in (result.rejection_reason or "")

    def test_title_similarity_alone_cannot_confirm(self):
        """Near-identical titles for different models is the canonical trap."""
        result = match_listings(
            candidate("Sony WH-1000XM5 Wireless Headphones Black"),
            candidate("Sony WH-1000XM4 Wireless Headphones Black"),
        )
        assert result.status is MatchStatus.REJECTED
        assert (
            title_similarity(
                "Sony WH-1000XM5 Wireless Headphones Black",
                "Sony WH-1000XM4 Wireless Headphones Black",
            )
            > 0.85
        )

    def test_title_only_match_is_capped_below_high_confidence(self):
        result = match_listings(
            candidate("Blue Ceramic Coffee Mug 12 oz Handmade"),
            candidate("Handmade Blue Ceramic Coffee Mug 12 oz"),
        )
        assert result.method is MatchMethod.TITLE_SIMILARITY
        assert result.confidence <= Decimal("0.60")
        assert result.status is not MatchStatus.CONFIRMED

    def test_pack_mismatch_blocks_a_gtin_backed_match(self):
        """Even an identifier match cannot survive a pack-count conflict."""
        result = match_listings(
            candidate("Tide PODS 112 count", identifiers={"upc": ["037000858065"]}),
            candidate("Tide PODS 42 count", identifiers={"upc": ["037000858065"]}),
        )
        assert result.status is MatchStatus.REJECTED

    def test_accessory_is_rejected_against_the_product(self):
        result = match_listings(
            candidate("Sony WH-1000XM5 Headphones", brand="Sony"),
            candidate("Carrying Case for Sony WH-1000XM5 Headphones", brand="Sony"),
        )
        assert result.status is MatchStatus.REJECTED

    def test_capacity_conflict_is_rejected(self):
        result = match_listings(
            candidate("SanDisk Ultra 256GB microSD", brand="SanDisk"),
            candidate("SanDisk Ultra 128GB microSD", brand="SanDisk"),
        )
        assert result.status is MatchStatus.REJECTED

    def test_colour_difference_is_ambiguous_not_rejected(self):
        """A colourway is a soft signal: not proven the same, not proven different."""
        result = match_listings(
            candidate("Stanley Quencher 40 oz Rose Quartz", brand="Stanley"),
            candidate("Stanley Quencher 40 oz Charcoal", brand="Stanley"),
        )
        assert result.status is MatchStatus.AMBIGUOUS
        assert not result.is_usable

    def test_mpn_match_is_probable(self):
        result = match_listings(
            candidate(
                "Logitech MX Master 3S", brand="Logitech", identifiers={"mpn": ["910-006556"]}
            ),
            candidate(
                "Logitech MX Master 3S Wireless",
                brand="Logitech",
                identifiers={"mpn": ["910006556"]},
            ),
        )
        assert result.method is MatchMethod.MPN
        assert result.is_usable

    def test_identifier_match_is_not_penalised_for_silent_titles(self):
        """A GTIN already distinguishes variants; unstated pack or size in the
        title must not drag a valid identifier match below the usable band."""
        result = match_listings(
            candidate("Widget", identifiers={"upc": ["027242923058"]}),
            candidate("Widget", identifiers={"upc": ["027242923058"]}),
        )
        assert result.confidence >= Decimal("0.95")

    def test_no_evidence_is_rejected(self):
        result = match_listings(
            candidate("Random Product A"), candidate("Totally Different Item B")
        )
        assert result.status is MatchStatus.REJECTED
        assert result.confidence == Decimal("0")


class TestMergingDoesNotTakeInnocentRows:
    """Merging two products must move their children, not delete them.

    ``Product.listings`` and ``Product.identifiers`` are delete-orphan
    relationships loaded eagerly, and the merge repoints them with a Core-level
    bulk UPDATE that the loaded collections know nothing about. Without expiring
    the absorbed product first, ``session.delete`` cascades through a stale
    collection and takes rows that were moved off it moments earlier.

    It only bites when a child was written earlier in the same transaction, so
    it stayed invisible until something created a listing and merged it before
    committing. These tests make that the ordinary case.
    """

    def _pair(self, session, auth, suffix):
        """Two products, each with a listing written in this transaction."""
        from app.models.catalog import MarketplaceListing, Product

        listings = []
        for index, marketplace in enumerate(("walmart", "amazon")):
            product = Product(
                organization_id=auth.organization_id, title=f"Product {index} {suffix}"
            )
            session.add(product)
            session.flush()
            listing = MarketplaceListing(
                organization_id=auth.organization_id,
                product_id=product.id,
                marketplace=marketplace,
                external_id=f"{marketplace}-{suffix}",
                title="Ninja AF101 Air Fryer 4 Qt, Grey",
                current_price=Decimal("10"),
            )
            session.add(listing)
            session.flush()
            # Touch the collection so it is loaded, which is the precondition
            # for the bug: a stale collection is what the delete cascades through.
            assert product.listings
            listings.append(listing)
        return listings

    def test_a_listing_written_in_this_transaction_survives_a_merge(self, session, auth):
        from app.domains.identity.service import unify_products
        from app.models.catalog import MarketplaceListing

        source, target = self._pair(session, auth, "merge1")
        unify_products(session, source, target)
        session.flush()

        for listing in (source, target):
            kept = session.get(MarketplaceListing, listing.id)
            assert kept is not None, "the merge deleted a listing it was supposed to move"
        assert (
            session.get(MarketplaceListing, source.id).product_id
            == session.get(MarketplaceListing, target.id).product_id
        ), "both sides should end on one canonical product"

    def test_observations_are_moved_rather_than_orphaned(self, session, auth):
        from app.domains.identity.service import unify_products
        from app.models.observations import PriceObservation

        source, target = self._pair(session, auth, "merge2")
        observation = PriceObservation(
            organization_id=auth.organization_id,
            product_id=source.product_id,
            listing_id=source.id,
            marketplace="walmart",
            price=Decimal("10"),
            shipping=Decimal("0"),
            landed_price=Decimal("10"),
            observed_at=utcnow(),
            provider="manual",
            source="manual",
            is_simulated=False,
        )
        session.add(observation)
        session.flush()

        survivor = unify_products(session, source, target)
        session.flush()

        kept = session.get(PriceObservation, observation.id)
        assert kept is not None, "history must never be orphaned by a merge"
        assert kept.product_id == survivor.id
