"""API tests.

Exercises the workflow the operator actually performs, through HTTP, from search
to a recorded outcome and a capital plan. These are the tests that would catch a
break in serialisation, route wiring or transaction handling that engine-level
tests cannot see.
"""

from __future__ import annotations

from decimal import Decimal

API = "/api/v1"

#: A clean, profitable fixture pair used across the workflow tests.
SOURCE = {"marketplace": "walmart", "external_id": "WM-598712344"}
TARGET = {"marketplace": "amazon", "external_id": "B09XS7JWHH"}


def analyze(client, source=None, target=None, **extra):
    source = source or SOURCE
    target = target or TARGET
    payload = {
        "source_marketplace": source["marketplace"],
        "source_external_id": source["external_id"],
        "target_marketplace": target["marketplace"],
        "target_external_id": target["external_id"],
        **extra,
    }
    response = client.post(f"{API}/products/analyze", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


class TestSystem:
    def test_health(self, client):
        assert client.get(f"{API}/health").json()["status"] == "ok"

    def test_readiness_checks_the_database(self, client):
        body = client.get(f"{API}/ready").json()
        assert body["database"] == "ok"

    def test_openapi_is_served(self, client):
        assert client.get("/openapi.json").status_code == 200


class TestSearch:
    def test_search_returns_results_flagged_as_fixture_data(self, client):
        body = client.post(
            f"{API}/products/search", json={"query": "sony headphones", "marketplace": "amazon"}
        ).json()
        assert body["items"]
        assert all(item["is_live_data"] is False for item in body["items"])

    def test_search_by_identifier(self, client):
        body = client.post(
            f"{API}/products/search", json={"query": "B09XS7JWHH", "marketplace": "amazon"}
        ).json()
        assert body["items"][0]["external_id"] == "B09XS7JWHH"

    def test_empty_query_is_rejected(self, client):
        response = client.post(f"{API}/products/search", json={"query": ""})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


class TestAnalyze:
    def test_full_analysis_payload(self, client):
        body = analyze(client)
        for key in (
            "summary",
            "match",
            "economics",
            "source_prices",
            "target_prices",
            "source_anomaly",
            "target_anomaly",
            "demand",
            "competition",
            "data_quality",
            "risk",
            "score",
            "decision",
            "stress_test",
        ):
            assert key in body, f"missing section: {key}"

    def test_money_crosses_the_wire_as_strings(self, client):
        """A JSON number becomes a float in every JavaScript client."""
        economics = analyze(client)["economics"]
        for field in ("net_profit", "sale_price", "acquisition_cost", "total_fees"):
            assert isinstance(economics[field], str)

    def test_decision_is_explained(self, client):
        decision = analyze(client)["decision"]
        assert decision["recommendation"] in {"buy", "review", "pass"}
        assert decision["headline"]
        assert decision["gates"]

    def test_score_components_are_visible(self, client):
        score = analyze(client)["score"]
        names = {item["name"] for item in score["components"]}
        assert names == {
            "profit",
            "roi",
            "price_stability",
            "demand",
            "competition",
            "match_confidence",
            "availability",
            "risk",
        }
        assert all(item["basis"] for item in score["components"])

    def test_weights_sum_to_one(self, client):
        components = analyze(client)["score"]["components"]
        total = sum(Decimal(item["weight"]) for item in components)
        assert total == Decimal("1.00")

    def test_same_marketplace_is_rejected(self, client):
        response = client.post(
            f"{API}/products/analyze",
            json={
                "source_marketplace": "amazon",
                "source_external_id": "B09XS7JWHH",
                "target_marketplace": "amazon",
                "target_external_id": "B0863TXGM3",
            },
        )
        assert response.status_code == 422

    def test_unknown_listing_returns_404_with_a_code(self, client):
        response = client.post(
            f"{API}/products/analyze",
            json={
                "source_marketplace": "walmart",
                "source_external_id": "NOPE",
                "target_marketplace": "amazon",
                "target_external_id": "B09XS7JWHH",
            },
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_both_directions(self, client):
        body = client.post(
            f"{API}/products/analyze/both-directions",
            json={"amazon_external_id": "B09XS7JWHH", "walmart_external_id": "WM-598712344"},
        ).json()
        assert len(body["directions"]) == 2
        assert body["best_direction"] in {"amazon_to_walmart", "walmart_to_amazon"}


class TestOpportunities:
    def test_listing_and_filtering(self, client):
        analyze(client)
        analyze(
            client,
            source={"marketplace": "walmart", "external_id": "WM-220884411"},
            target={"marketplace": "amazon", "external_id": "B078JXWDVX"},
        )

        everything = client.get(f"{API}/opportunities").json()
        assert everything["total"] == 2

        buys = client.get(f"{API}/opportunities", params={"recommendation": "buy"}).json()
        assert all(item["recommendation"] == "buy" for item in buys["items"])

        filtered = client.get(f"{API}/opportunities", params={"min_score": 60}).json()
        assert all(Decimal(item["score"]) >= 60 for item in filtered["items"])

    def test_sorting(self, client):
        analyze(client)
        analyze(
            client,
            source={"marketplace": "walmart", "external_id": "WM-118820043"},
            target={"marketplace": "amazon", "external_id": "B07FDJMC9Q"},
        )
        body = client.get(f"{API}/opportunities", params={"sort": "profit"}).json()
        profits = [Decimal(item["net_profit"]) for item in body["items"]]
        assert profits == sorted(profits, reverse=True)

    def test_detail_includes_the_full_decision_view(self, client):
        opportunity_id = analyze(client)["opportunity_id"]
        body = client.get(f"{API}/opportunities/{opportunity_id}").json()
        assert body["product"]
        assert body["source_listing"] and body["target_listing"]
        assert body["match"]["method"]
        assert body["economics"]["line_items"]
        assert body["economics"]["assumptions"]
        assert body["risk"]["signals"] is not None
        assert body["stress_test"]["scenarios"]
        assert body["price_history"]["source"]
        assert body["events"]

    def test_unknown_opportunity_is_404(self, client):
        response = client.get(f"{API}/opportunities/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    def test_summary_counts(self, client):
        analyze(client)
        body = client.get(f"{API}/opportunities/summary").json()
        assert body["total"] == 1


class TestLifecycle:
    def test_decision_transitions_are_recorded(self, client):
        opportunity_id = analyze(client)["opportunity_id"]
        response = client.post(
            f"{API}/opportunities/{opportunity_id}/decision",
            json={"status": "approved", "note": "Checked the source listing by hand."},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "approved"

        events = client.get(f"{API}/opportunities/{opportunity_id}").json()["events"]
        assert any(event["to_status"] == "approved" for event in events)

    def test_illegal_transition_is_refused_with_the_allowed_set(self, client):
        opportunity_id = analyze(client)["opportunity_id"]
        response = client.post(
            f"{API}/opportunities/{opportunity_id}/decision", json={"status": "sold"}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"
        assert "Allowed" in response.json()["error"]["message"]


class TestValidation:
    def test_recording_a_hand_check_computes_deltas(self, client):
        body = analyze(client)
        opportunity_id = body["opportunity_id"]
        source_price = Decimal(body["summary"]["source"]["price"])

        response = client.post(
            f"{API}/opportunities/{opportunity_id}/validate",
            json={
                "actual_source_price": str(source_price + Decimal("5")),
                "match_confirmed": True,
                "would_buy": True,
                "notes": "Price was 5 dollars higher at checkout.",
            },
        )
        assert response.status_code == 200
        assert Decimal(response.json()["source_price_delta"]) == Decimal("5.0000")

    def test_metrics_report_their_denominator(self, client):
        opportunity_id = analyze(client)["opportunity_id"]
        client.post(
            f"{API}/opportunities/{opportunity_id}/validate",
            json={"match_confirmed": True, "would_buy": True},
        )
        body = client.get(f"{API}/opportunities/validation/metrics").json()
        assert body["validated_count"] == 1
        assert body["is_statistically_meaningful"] is False
        assert body["caveat"]
        assert body["match_accuracy"]["checked"] == 1

    def test_unanswered_questions_stay_null(self, client):
        opportunity_id = analyze(client)["opportunity_id"]
        client.post(
            f"{API}/opportunities/{opportunity_id}/validate",
            json={"actual_source_price": "100.00"},
        )
        body = client.get(f"{API}/opportunities/validation/metrics").json()
        assert body["match_accuracy"]["checked"] == 0
        assert body["match_accuracy"]["rate"] is None


class TestPortfolio:
    def test_purchase_sale_and_outcome(self, client):
        body = analyze(client)
        opportunity_id = body["opportunity_id"]
        detail = client.get(f"{API}/opportunities/{opportunity_id}").json()
        product_id = detail["product"]["id"]

        purchase = client.post(
            f"{API}/purchases",
            json={
                "product_id": product_id,
                "opportunity_id": opportunity_id,
                "quantity": 10,
                "unit_price": "219.00",
                "shipping_cost": "15.00",
            },
        )
        assert purchase.status_code == 201
        assert purchase.json()["total_cost"] == "2205.0000"

        sale = client.post(
            f"{API}/sales",
            json={
                "product_id": product_id,
                "opportunity_id": opportunity_id,
                "purchase_id": purchase.json()["id"],
                "marketplace": "amazon",
                "quantity": 10,
                "unit_price": "328.00",
                "marketplace_fees": "262.40",
                "fulfillment_fees": "49.70",
            },
        )
        assert sale.status_code == 201
        assert sale.json()["net_proceeds"] == "2967.9000"

        outcomes = client.get(f"{API}/outcomes").json()
        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert outcome["quantity_purchased"] == 10
        assert outcome["quantity_sold"] == 10
        assert outcome["is_closed"] is True
        assert outcome["result"] == "win"
        assert outcome["predicted_total_profit"] is not None
        assert outcome["profit_variance"] is not None

    def test_purchase_advances_the_lifecycle(self, client):
        body = analyze(client)
        opportunity_id = body["opportunity_id"]
        product_id = client.get(f"{API}/opportunities/{opportunity_id}").json()["product"]["id"]
        client.post(
            f"{API}/purchases",
            json={
                "product_id": product_id,
                "opportunity_id": opportunity_id,
                "quantity": 1,
                "unit_price": "219.00",
            },
        )
        detail = client.get(f"{API}/opportunities/{opportunity_id}").json()
        assert detail["status"] == "purchased"
        assert any(event["type"] == "purchase_recorded" for event in detail["events"])

    def test_partially_sold_position_is_open_not_a_loss(self, client):
        body = analyze(client)
        opportunity_id = body["opportunity_id"]
        product_id = client.get(f"{API}/opportunities/{opportunity_id}").json()["product"]["id"]
        purchase = client.post(
            f"{API}/purchases",
            json={
                "product_id": product_id,
                "opportunity_id": opportunity_id,
                "quantity": 10,
                "unit_price": "219.00",
            },
        ).json()
        client.post(
            f"{API}/sales",
            json={
                "product_id": product_id,
                "opportunity_id": opportunity_id,
                "purchase_id": purchase["id"],
                "marketplace": "amazon",
                "quantity": 3,
                "unit_price": "328.00",
                "marketplace_fees": "78.72",
            },
        )
        outcome = client.get(f"{API}/outcomes").json()[0]
        assert outcome["is_closed"] is False
        # Cost is attributed only to the units that sold.
        assert Decimal(outcome["actual_costs"]) == Decimal("657.0000")

    def test_zero_quantity_is_rejected(self, client):
        response = client.post(
            f"{API}/purchases",
            json={"product_id": "x", "quantity": 0, "unit_price": "1.00"},
        )
        assert response.status_code == 422

    def test_portfolio_summary(self, client):
        body = client.get(f"{API}/portfolio/summary").json()
        assert body["capital_deployed"] == "0.0000"
        assert body["purchase_count"] == 0


class TestCapitalSimulation:
    def test_allocation_respects_capital(self, client):
        analyze(client)
        analyze(
            client,
            source={"marketplace": "walmart", "external_id": "WM-118820043"},
            target={"marketplace": "amazon", "external_id": "B07FDJMC9Q"},
        )

        body = client.post(
            f"{API}/simulate/capital",
            json={"available_capital": "10000", "min_roi": "0.20", "min_score": "50"},
        ).json()
        assert Decimal(body["allocated_capital"]) <= Decimal("10000")
        assert Decimal(body["allocated_capital"]) + Decimal(body["unallocated_capital"]) == Decimal(
            "10000"
        )
        assert body["candidates_considered"] >= 2

    def test_every_exclusion_is_explained(self, client):
        analyze(
            client,
            source={"marketplace": "walmart", "external_id": "WM-908123774"},
            target={"marketplace": "amazon", "external_id": "B083GBLGZR"},
        )
        body = client.post(f"{API}/simulate/capital", json={"available_capital": "5000"}).json()
        assert body["excluded"]
        assert all(item["reason"] for item in body["excluded"])

    def test_plan_can_be_saved_and_listed(self, client):
        analyze(client)
        body = client.post(
            f"{API}/simulate/capital",
            json={"available_capital": "5000", "save": True, "name": "October sourcing"},
        ).json()
        assert body["plan_id"]
        plans = client.get(f"{API}/simulate/capital/plans").json()
        assert plans[0]["name"] == "October sourcing"


class TestBulk:
    def test_mixed_identifier_formats(self, client):
        content = "\n".join(
            [
                "identifier",
                "WM-598712344",
                "https://www.amazon.com/dp/B09XS7JWHH",
                "not-a-real-identifier",
            ]
        )
        body = client.post(f"{API}/bulk/analyze", json={"content": content}).json()
        assert body["total"] == 3
        # A Walmart item id and an Amazon URL both resolve; the third does not
        # match any listing and is reported with its reason rather than dropped.
        assert body["analyzed"] == 2
        assert body["errors"] + body["skipped"] == 1
        assert all(row["message"] for row in body["failures"])

    def test_results_are_ranked(self, client):
        content = "identifier\nWM-598712344\nWM-118820043\nWM-908123774"
        body = client.post(f"{API}/bulk/analyze", json={"content": content}).json()
        scores = [Decimal(row["score"]) for row in body["results"] if "score" in row]
        assert scores == sorted(scores, reverse=True)

    def test_one_bad_row_does_not_lose_the_others(self, client):
        content = "identifier\nNO-SUCH-ITEM-9999\nWM-598712344"
        body = client.post(f"{API}/bulk/analyze", json={"content": content}).json()
        assert body["analyzed"] == 1
        assert body["errors"] + body["skipped"] == 1
        assert all(row["message"] for row in body["failures"])

    def test_csv_export(self, client):
        response = client.post(
            f"{API}/bulk/analyze/export", json={"content": "identifier\nWM-598712344"}
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "recommendation" in response.text

    def test_empty_upload_is_rejected(self, client):
        response = client.post(f"{API}/bulk/analyze", json={"content": "   "})
        assert response.status_code == 422


class TestProvidersAndAnalytics:
    def test_provider_list_reports_configuration(self, client):
        body = client.get(f"{API}/providers").json()
        slugs = {item["slug"] for item in body}
        assert "mock_amazon" in slugs
        assert all(item["is_live"] is False for item in body)

    def test_provider_health_reports_metrics(self, client):
        analyze(client)
        body = client.get(f"{API}/providers/health").json()
        amazon = next(item for item in body if item["slug"] == "mock_amazon")
        assert amazon["request_count"] > 0
        assert amazon["success_rate"] == 1.0
        assert amazon["circuit_state"] == "closed"

    def test_provider_requests_are_logged(self, client):
        analyze(client)
        body = client.get(f"{API}/providers/requests").json()
        assert body
        assert all("capability" in item for item in body)

    def test_analytics_dashboard(self, client):
        analyze(client)
        body = client.get(f"{API}/analytics").json()
        assert body["opportunities"]["total"] == 1
        assert body["portfolio"]["purchase_count"] == 0
        assert body["prediction_accuracy"]["sample_size"] == 0
        assert body["validation"]["validated_count"] == 0

    def test_accuracy_refuses_to_report_without_data(self, client):
        body = client.get(f"{API}/analytics/accuracy").json()
        assert body["is_meaningful"] is False
        assert "cannot be computed" in body["caveat"]


class TestProductRoutes:
    def test_product_detail_and_identifiers(self, client):
        opportunity_id = analyze(client)["opportunity_id"]
        product_id = client.get(f"{API}/opportunities/{opportunity_id}").json()["product"]["id"]
        body = client.get(f"{API}/products/{product_id}").json()
        assert body["product"]["title"]
        assert body["identifiers"]
        assert len(body["listings"]) == 2

    def test_price_history_reports_sufficiency(self, client):
        opportunity_id = analyze(client)["opportunity_id"]
        product_id = client.get(f"{API}/opportunities/{opportunity_id}").json()["product"]["id"]
        body = client.get(f"{API}/products/{product_id}/history").json()
        assert body["listings"]
        statistics = body["listings"][0]["statistics"]
        assert "windows" in statistics
        assert all("sufficient" in window for window in statistics["windows"].values())
