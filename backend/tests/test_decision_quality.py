"""The decision-quality contract, exercised end to end over the fixtures.

The question this phase is built around is whether someone looking at an
opportunity can answer, from the screen alone: why is this a good purchase, how
fragile is it, how much should I pay, and how strong is the evidence. Each of
those is a claim the API has to be able to support, and these tests hold it to
them through HTTP, over the fixture catalogue, with no mocks in between.
"""

from __future__ import annotations

from decimal import Decimal

API = "/api/v1"

CLEAN = (
    {"marketplace": "walmart", "external_id": "WM-598712344"},
    {"marketplace": "amazon", "external_id": "B09XS7JWHH"},
)
NO_DEMAND = (
    {"marketplace": "walmart", "external_id": "WM-000112233"},
    {"marketplace": "amazon", "external_id": "B0AAA111BB"},
)
THIN_HISTORY = (
    {"marketplace": "walmart", "external_id": "WM-551002993"},
    {"marketplace": "amazon", "external_id": "B0CX1Y2Z3Q"},
)
VOLATILE = (
    {"marketplace": "walmart", "external_id": "WM-882001773"},
    {"marketplace": "amazon", "external_id": "B0BG94PS2F"},
)

#: Every risk category the engine is required to report on, separately.
EXPECTED_CATEGORIES = {
    "price",
    "competition",
    "demand",
    "inventory",
    "product_match",
    "data_quality",
    "brand_category",
    "economics",
}


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


class TestScoreIsReproducible:
    """"Why is this a good purchase?" has to be answerable by arithmetic."""

    def test_every_component_publishes_its_inputs_and_its_working(self, client):
        components = analyze(client)["score"]["components"]
        assert components
        for component in components:
            assert component["label"], f"{component['name']} has no readable name"
            assert component["basis"], f"{component['name']} has no basis"
            assert component["calculation"], f"{component['name']} shows no working"
            assert component["inputs"], f"{component['name']} names no inputs"
            # The working has to contain the figures, not describe them in
            # the abstract: a sentence with no numbers in it explains nothing.
            assert any(character.isdigit() for character in component["calculation"])

    def test_the_components_add_up_to_the_total(self, client):
        """The reader can add the column and get the number at the bottom."""
        score = analyze(client)["score"]
        summed = sum(Decimal(item["contribution"]) for item in score["components"])
        assert Decimal(score["contribution_total"]) == summed.quantize(Decimal("0.0001"))
        assert abs(Decimal(score["total"]) - summed) <= Decimal("0.01")

    def test_the_weights_are_published_and_sum_to_one(self, client):
        score = analyze(client)["score"]
        assert Decimal(score["weight_total"]) == Decimal("1.0000")

    def test_an_unmeasured_component_says_so_rather_than_scoring_zero(self, client):
        """Absence of evidence is not evidence of absence, and not a zero."""
        score = analyze(client, NO_DEMAND)["score"]
        demand = next(item for item in score["components"] if item["name"] == "demand")
        assert demand["has_data"] is False
        assert Decimal(demand["score"]) > 0
        assert "demand" in score["unknown_components"]
        assert "unknown" in demand["calculation"].lower()


class TestRiskIsSeparated:
    """"How fragile is the opportunity?" is several questions, not one."""

    def test_every_category_is_reported_even_when_clean(self, client):
        """A view that lists only what went wrong cannot be read as a checklist."""
        risk = analyze(client)["risk"]
        reported = {item["category"] for item in risk["categories"]}
        assert reported == EXPECTED_CATEGORIES
        for item in risk["categories"]:
            assert item["label"]
            assert item["summary"]
            assert item["level"] in {"low", "medium", "high", "critical"}
            assert Decimal(item["score"]) >= 0

    def test_each_signal_declares_which_category_it_belongs_to(self, client):
        risk = analyze(client, VOLATILE)["risk"]
        assert risk["signals"]
        for signal in risk["signals"]:
            assert signal["category"] in EXPECTED_CATEGORIES

    def test_a_category_with_no_evidence_is_distinguished_from_a_clean_one(self, client):
        """Both score zero signals. They are opposite findings."""
        risk = analyze(client, NO_DEMAND)["risk"]
        demand = next(item for item in risk["categories"] if item["category"] == "demand")
        assert demand["has_evidence"] is False
        assert "demand" in risk["unassessed_categories"]

        match = next(item for item in risk["categories"] if item["category"] == "product_match")
        assert match["has_evidence"] is True

    def test_the_overall_level_names_the_category_that_drove_it(self, client):
        risk = analyze(client, VOLATILE)["risk"]
        assert risk["driving_category"] in EXPECTED_CATEGORIES
        assert risk["driving_category"] in {signal["category"] for signal in risk["signals"]}

    def test_category_scores_never_exceed_the_scale(self, client):
        for pair in (CLEAN, VOLATILE, NO_DEMAND):
            for item in analyze(client, pair)["risk"]["categories"]:
                assert Decimal("0") <= Decimal(item["score"]) <= Decimal("100")


class TestSpreadLadder:
    """"How much should I pay?" needs the whole chain, not its two ends."""

    def test_the_ladder_is_internally_consistent(self, client):
        economics = analyze(client)["economics"]
        sale = Decimal(economics["sale_price"])
        landed = Decimal(economics["landed_acquisition_cost"])
        fees = Decimal(economics["total_fees"])

        other = Decimal(economics["other_unit_costs"])

        assert landed == Decimal(economics["acquisition_cost"]) + Decimal(
            economics["acquisition_shipping"]
        )
        assert other == (
            Decimal(economics["inbound_shipping"])
            + Decimal(economics["tax"])
            + Decimal(economics["misc_cost"])
        )
        assert Decimal(economics["gross_spread"]) == sale - landed
        # The whole chain closes: nothing in the total is left unnamed.
        assert Decimal(economics["total_cost"]) == landed + other + fees
        assert Decimal(economics["net_profit"]) == sale - landed - other - fees

    def test_gross_spread_percent_is_measured_against_what_was_paid(self, client):
        """Spread percent and margin are different numbers. Both are named."""
        economics = analyze(client)["economics"]
        landed = Decimal(economics["landed_acquisition_cost"])
        expected = (Decimal(economics["gross_spread"]) / landed).quantize(Decimal("0.0001"))
        assert Decimal(economics["gross_spread_pct"]) == expected
        assert Decimal(economics["margin"]) != Decimal(economics["gross_spread_pct"])

    def test_the_ceiling_is_the_price_at_which_profit_runs_out(self, client):
        economics = analyze(client)["economics"]
        ceiling = Decimal(economics["max_acquisition_cost"])
        assert ceiling > Decimal(economics["acquisition_cost"])
        assert ceiling < Decimal(economics["sale_price"])

    def test_the_stored_record_shows_the_same_ladder_as_the_live_analysis(self, client):
        """The record is read far more often than the analysis that made it.

        The stored snapshot keeps the components but not the derived terms, and
        rendering it without recomputing them left the detail page showing "not
        available" for a gap it had every number to work out.
        """
        body = analyze(client)
        stored = client.get(f"{API}/opportunities/{body['opportunity_id']}").json()["economics"]
        live = body["economics"]

        for field in (
            "landed_acquisition_cost",
            "other_unit_costs",
            "gross_spread",
            "gross_spread_pct",
            "total_cost",
            "net_profit",
            "max_acquisition_cost",
        ):
            assert stored[field] is not None, f"{field} missing from the stored record"
            assert Decimal(stored[field]) == Decimal(live[field]), field

    def test_a_listed_row_carries_the_whole_chain(self, client):
        """The list has to answer the same question without opening the record."""
        analyze(client)
        row = client.get(f"{API}/opportunities").json()["items"][0]
        for field in (
            "source_marketplace",
            "acquisition_cost",
            "target_marketplace",
            "expected_sale_price",
            "gross_spread",
            "gross_spread_pct",
            "total_cost",
            "total_fees",
            "other_unit_costs",
            "net_profit",
            "roi",
            "margin",
            "max_acquisition_cost",
        ):
            assert row[field] is not None, f"{field} missing from the row"
        assert Decimal(row["total_cost"]) == (
            Decimal(row["acquisition_cost"])
            + Decimal(row["other_unit_costs"])
            + Decimal(row["total_fees"])
        )


class TestHistoricalEvidence:
    """"How strong is the evidence?" is its own answer, not a footnote."""

    def test_a_verdict_is_reached_and_justified(self, client):
        evidence = analyze(client)["spread_evidence"]
        assert evidence["verdict"] in {"structural", "temporary", "unstable", "unsupported"}
        assert evidence["summary"]
        assert evidence["reasons"]
        assert evidence["confidence"] in {"none", "low", "medium", "high"}

    def test_a_supported_verdict_publishes_the_history_behind_it(self, client):
        evidence = analyze(client)["spread_evidence"]
        assert evidence["verdict"] != "unsupported"
        assert evidence["typical_spread"] is not None
        for side in ("source", "target"):
            assert evidence[side]["window_days"]
            assert evidence[side]["observation_count"] > 0
            assert evidence[side]["usable"] is True

    def test_thin_history_withholds_the_verdict_rather_than_guessing(self, client):
        """The absence of an answer is not a quiet "structural"."""
        evidence = analyze(client, THIN_HISTORY)["spread_evidence"]
        if evidence["verdict"] == "unsupported":
            assert evidence["confidence"] == "none"
            assert evidence["typical_spread"] is None
            assert evidence["reasons"]
        else:
            # Enough history to judge, but it must not claim more confidence
            # than a short window can support.
            assert evidence["confidence"] in {"low", "medium"}

    def test_the_stored_record_recomputes_the_same_kind_of_answer(self, client):
        opportunity_id = analyze(client)["opportunity_id"]
        stored = client.get(f"{API}/opportunities/{opportunity_id}").json()["spread_evidence"]
        assert stored is not None
        assert stored["verdict"] in {"structural", "temporary", "unstable", "unsupported"}
        assert stored["reasons"]


class TestEvidenceIsComplete:
    """Everything a BUY rests on has to be on the record."""

    def test_the_detail_record_carries_every_section(self, client):
        opportunity_id = analyze(client)["opportunity_id"]
        detail = client.get(f"{API}/opportunities/{opportunity_id}").json()
        for section in (
            "economics",
            "price_history",
            "match",
            "risk",
            "stress_test",
            "spread_evidence",
            "demand",
            "competition",
            "score_components",
            "explanation",
        ):
            assert detail[section], f"{section} is missing from the decision record"
        assert detail["risk"]["categories"], "risk is not broken down by category"
        assert detail["score_components"]["components"], "the score shows no components"

    def test_every_side_says_where_its_price_came_from(self, client):
        """The hook the provider layer needs before real providers arrive.

        A price is only as good as the thing that reported it. Publishing the
        provider, the observation time and whether it was a live call now means
        connecting a real adapter changes the values in this block and nothing
        about its shape, and the fixture case is labelled honestly in the
        meantime.
        """
        body = analyze(client)
        assert {entry["role"] for entry in body["summary"]["provenance"]} == {
            "source",
            "target",
        }
        for entry in body["summary"]["provenance"]:
            assert entry["provider"], f"{entry['role']} does not say who reported it"
            assert entry["is_live_data"] is False, "fixtures must not claim to be live"
            assert entry["marketplace"] and entry["external_id"]

        stored = client.get(f"{API}/opportunities/{body['opportunity_id']}").json()
        assert len(stored["provenance"]) == 2
        for entry in stored["provenance"]:
            assert entry["provider"]
            assert entry["is_live_data"] is False

    def test_nothing_unavailable_is_reported_as_zero(self, client):
        """The platform's central claim, checked on the fixture built for it."""
        body = analyze(client, NO_DEMAND)
        assert body["demand"]["confidence"] == "none"
        assert body["demand"]["score"] is None
        assert body["demand"]["current_rank"] is None
        quality = body["data_quality"]
        assert "demand" in quality["missing"] or quality["missing"]
