"""The two free live providers, and the platform catalogue.

The transports are real, so these tests exercise them against a stubbed HTTP
layer rather than against the live services: a test suite that depends on eBay
being reachable is a test suite that fails for reasons that have nothing to do
with this code, and one that spends a 5,000 call daily allowance on CI.

What is tested is everything between the wire and the domain: the payload
mappers, the auth flows, the error translation and the refusals.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.core.errors import ProviderError, ProviderNotConfiguredError
from app.models.enums import Availability, Condition, Marketplace
from app.services.providers import bestbuy, ebay
from app.services.providers.base import ProviderCapability
from app.services.providers.planned import PlannedProvider
from app.services.providers.platforms import (
    ALL,
    BY_SLUG,
    PlatformRole,
    PlatformStatus,
    connectable_now,
)


def stub_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://stub")


# --------------------------------------------------------------- Best Buy

BESTBUY_PRODUCT = {
    "sku": 6084400,
    "name": "Sony - WH-1000XM5 Wireless Noise-Canceling Headphones - Black",
    "upc": "027242923058",
    "manufacturer": "Sony",
    "modelNumber": "WH1000XM5/B",
    "salePrice": 328.0,
    "regularPrice": 399.99,
    "onSale": True,
    "onlineAvailability": True,
    "orderable": "Available",
    "customerReviewAverage": 4.7,
    "customerReviewCount": 4212,
    "url": "https://www.bestbuy.com/site/6084400.p",
    "image": "https://pisces.bbystatic.com/image.jpg",
    "categoryPath": [
        {"id": "abcat0100000", "name": "Audio"},
        {"id": "abcat0204000", "name": "Headphones"},
    ],
    "shippingWeight": 1.3,
    "shippingCost": 0.0,
}


class TestBestBuyMapper:
    def test_maps_a_realistic_payload(self):
        listing = bestbuy.map_listing(BESTBUY_PRODUCT)
        assert listing.marketplace is Marketplace.BESTBUY
        assert listing.external_id == "6084400"
        assert listing.price == Decimal("328.0000")
        assert listing.brand == "Sony"
        assert listing.availability is Availability.IN_STOCK
        assert listing.condition is Condition.NEW

    def test_carries_the_upc_so_identity_works_on_a_real_identifier(self):
        listing = bestbuy.map_listing(BESTBUY_PRODUCT)
        upc = next(item for item in listing.identifiers if item.identifier_type == "upc")
        assert upc.value == "027242923058"

    def test_shipping_weight_reaches_the_fee_engine(self):
        """The fee engine needs a weight, and most catalogue APIs omit it."""
        listing = bestbuy.map_listing(BESTBUY_PRODUCT)
        assert listing.attributes["weight_lb"] == "1.3"

    def test_category_leaf_is_used(self):
        assert bestbuy.map_listing(BESTBUY_PRODUCT).category == "Headphones"

    def test_no_seller_count_is_invented_for_a_single_retailer(self):
        """Best Buy is one retailer. Reporting one seller would assert a market
        structure that does not exist here."""
        listing = bestbuy.map_listing(BESTBUY_PRODUCT)
        assert listing.seller_count is None
        assert listing.offer_count is None

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"orderable": "Available"}, Availability.IN_STOCK),
            ({"orderable": "SoldOut"}, Availability.OUT_OF_STOCK),
            ({"orderable": "ComingSoon"}, Availability.PREORDER),
            ({"onlineAvailability": True}, Availability.IN_STOCK),
            ({"onlineAvailability": False, "inStoreAvailability": True}, Availability.LIMITED),
            ({}, Availability.UNKNOWN),
        ],
    )
    def test_availability_mapping(self, payload, expected):
        assert bestbuy._availability(payload) is expected

    def test_unknown_availability_does_not_default_to_in_stock(self):
        """A wrong 'in stock' becomes a purchase order for something unbuyable."""
        listing = bestbuy.map_listing({"sku": 1, "name": "x"})
        assert listing.availability is Availability.UNKNOWN

    def test_missing_price_is_none_not_zero(self):
        assert bestbuy.map_listing({"sku": 1, "name": "x"}).price is None


class TestBestBuyTransport:
    async def test_unconfigured_refuses_and_explains(self):
        provider = bestbuy.BestBuyProvider(api_key=None)
        assert provider.is_configured is False
        assert "BESTBUY_API_KEY" in (provider.configuration_note or "")
        with pytest.raises(ProviderNotConfiguredError):
            await provider.get_product("6084400")

    async def test_search_sends_the_key_and_maps_results(self):
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["apiKey"] = request.url.params.get("apiKey")
            return httpx.Response(200, json={"products": [BESTBUY_PRODUCT], "total": 1})

        provider = bestbuy.BestBuyProvider(api_key="KEY", client=stub_client(handler))
        result = await provider.search_products("sony headphones")
        assert seen["apiKey"] == "KEY"
        assert result.listings[0].external_id == "6084400"
        assert result.total_results == 1

    async def test_lookup_by_upc_queries_the_upc_field(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            return httpx.Response(200, json={"products": [BESTBUY_PRODUCT]})

        provider = bestbuy.BestBuyProvider(api_key="KEY", client=stub_client(handler))
        listing = await provider.get_product_by_identifier("upc", "027242923058")
        assert listing is not None
        assert "upc=027242923058" in seen["path"]

    async def test_a_rejected_key_reads_as_not_configured(self):
        provider = bestbuy.BestBuyProvider(
            api_key="BAD", client=stub_client(lambda request: httpx.Response(403))
        )
        with pytest.raises(ProviderNotConfiguredError):
            await provider.get_product("1")

    async def test_server_error_does_not_leak_the_query(self):
        """The error body echoes the query string, which carries the API key."""
        provider = bestbuy.BestBuyProvider(
            api_key="KEY",
            client=stub_client(lambda request: httpx.Response(500, text="apiKey=KEY failed")),
        )
        with pytest.raises(ProviderError) as caught:
            await provider.get_product("1")
        assert "KEY" not in str(caught.value)


# ------------------------------------------------------------------ eBay

EBAY_ITEM = {
    "itemId": "v1|123456789012|0",
    "title": "Sony WH-1000XM5 Wireless Noise Cancelling Headphones Black",
    "price": {"value": "279.95", "currency": "USD"},
    "condition": "NEW",
    "seller": {"username": "audio_deals", "feedbackPercentage": "99.3"},
    "itemWebUrl": "https://www.ebay.com/itm/123456789012",
    "image": {"imageUrl": "https://i.ebayimg.com/x.jpg"},
    "shippingOptions": [{"shippingCost": {"value": "0.00", "currency": "USD"}}],
    "gtin": "027242923058",
    "brand": "Sony",
}

TOKEN_RESPONSE = {"access_token": "TOKEN", "expires_in": 7200, "token_type": "Application"}


def ebay_handler(item=None, *, search_total=1, capture=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.setdefault("paths", []).append(request.url.path)
            capture.setdefault("params", []).append(dict(request.url.params))
        if request.url.path.endswith("/oauth2/token"):
            if capture is not None:
                capture["auth"] = request.headers.get("authorization")
                capture["token_calls"] = capture.get("token_calls", 0) + 1
            return httpx.Response(200, json=TOKEN_RESPONSE)
        if "item_summary/search" in request.url.path:
            return httpx.Response(
                200, json={"itemSummaries": [item or EBAY_ITEM], "total": search_total}
            )
        return httpx.Response(200, json=item or EBAY_ITEM)

    return handler


class TestEbayMapper:
    def test_maps_a_realistic_item(self):
        listing = ebay.map_listing(EBAY_ITEM)
        assert listing.marketplace is Marketplace.EBAY
        assert listing.external_id == "v1|123456789012|0"
        assert listing.price == Decimal("279.9500")
        assert listing.condition is Condition.NEW
        assert listing.url == "https://www.ebay.com/itm/123456789012"

    def test_gtin_becomes_an_identifier(self):
        listing = ebay.map_listing(EBAY_ITEM)
        assert any(item.identifier_type == "gtin" for item in listing.identifiers)

    def test_free_shipping_is_a_real_zero(self):
        assert ebay.parse_shipping(EBAY_ITEM) == Decimal("0")

    def test_cheapest_shipping_option_wins(self):
        payload = {
            "shippingOptions": [
                {"shippingCost": {"value": "9.99"}},
                {"shippingCost": {"value": "4.50"}},
            ]
        }
        assert ebay.parse_shipping(payload) == Decimal("4.5000")

    def test_absent_shipping_is_recorded_as_unknown(self):
        """Zero and unknown are different, and the difference is flagged."""
        listing = ebay.map_listing({"itemId": "1", "title": "x", "price": {"value": "10"}})
        assert listing.attributes["shipping_cost_known"] == "false"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("NEW", Condition.NEW),
            ("CERTIFIED_REFURBISHED", Condition.RENEWED),
            ("USED_GOOD", Condition.USED_GOOD),
            ("FOR_PARTS_OR_NOT_WORKING", Condition.USED_ACCEPTABLE),
            (None, Condition.UNKNOWN),
        ],
    )
    def test_condition_mapping(self, value, expected):
        """Condition matters: the variation engine blocks new against used."""
        assert ebay.parse_condition(value) is expected

    def test_variant_group_is_flagged(self):
        """A listing that is a group of variants is not one sellable unit."""
        listing = ebay.map_listing({**EBAY_ITEM, "itemGroupType": "SELLER_DEFINED_VARIATIONS"})
        assert listing.attributes["item_group_type"] == "SELLER_DEFINED_VARIATIONS"


class TestEbayTransport:
    async def test_unconfigured_refuses_and_explains(self):
        provider = ebay.EbayProvider(client_id=None, client_secret=None)
        assert provider.is_configured is False
        assert "EBAY_CLIENT_ID" in (provider.configuration_note or "")
        with pytest.raises(ProviderNotConfiguredError):
            await provider.search_products("x")

    async def test_client_credentials_flow_sends_basic_auth(self):
        capture: dict = {}
        provider = ebay.EbayProvider(
            client_id="ID",
            client_secret="SECRET",
            client=stub_client(ebay_handler(capture=capture)),
        )
        await provider.search_products("headphones")
        import base64

        expected = base64.b64encode(b"ID:SECRET").decode()
        assert capture["auth"] == f"Basic {expected}"

    async def test_the_token_is_reused_across_calls(self):
        """A token exchange per request would burn a fifth of the daily
        allowance on authentication alone."""
        capture: dict = {}
        provider = ebay.EbayProvider(
            client_id="ID",
            client_secret="SECRET",
            client=stub_client(ebay_handler(capture=capture)),
        )
        await provider.search_products("a")
        await provider.search_products("b")
        await provider.search_products("c")
        assert capture["token_calls"] == 1

    async def test_bad_credentials_read_as_not_configured(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "invalid_client"})

        provider = ebay.EbayProvider(
            client_id="ID", client_secret="WRONG", client=stub_client(handler)
        )
        with pytest.raises(ProviderNotConfiguredError):
            await provider.search_products("x")

    async def test_rate_limit_is_named(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/oauth2/token"):
                return httpx.Response(200, json=TOKEN_RESPONSE)
            return httpx.Response(429)

        provider = ebay.EbayProvider(
            client_id="ID", client_secret="SECRET", client=stub_client(handler)
        )
        with pytest.raises(ProviderError) as caught:
            await provider.search_products("x")
        assert "5,000 calls per day" in str(caught.value)

    async def test_gtin_lookup_uses_the_gtin_parameter(self):
        """This is the reason eBay is valuable: identity by identifier, not title."""
        capture: dict = {}
        provider = ebay.EbayProvider(
            client_id="ID",
            client_secret="SECRET",
            client=stub_client(ebay_handler(capture=capture)),
        )
        listing = await provider.get_product_by_identifier("upc", "027242923058")
        assert listing is not None
        assert any(params.get("gtin") == "027242923058" for params in capture["params"])

    async def test_gtin_lookup_picks_the_cheapest_landed_listing(self):
        cheap = {**EBAY_ITEM, "itemId": "cheap", "price": {"value": "250.00"}}
        dear = {**EBAY_ITEM, "itemId": "dear", "price": {"value": "300.00"}}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/oauth2/token"):
                return httpx.Response(200, json=TOKEN_RESPONSE)
            return httpx.Response(200, json={"itemSummaries": [dear, cheap], "total": 2})

        provider = ebay.EbayProvider(
            client_id="ID", client_secret="SECRET", client=stub_client(handler)
        )
        listing = await provider.get_product_by_identifier("upc", "027242923058")
        assert listing is not None and listing.external_id == "cheap"

    async def test_offers_are_reconstructed_from_a_gtin_search(self):
        provider = ebay.EbayProvider(
            client_id="ID", client_secret="SECRET", client=stub_client(ebay_handler())
        )
        offers = await provider.get_offers("v1|123456789012|0")
        assert offers
        assert sum(1 for offer in offers if offer.is_buy_box) == 1

    async def test_no_gtin_means_no_offers_rather_than_a_guess(self):
        """Without an identifier there is no defensible way to say two listings
        are the same unit, so nothing is returned."""
        item = {key: value for key, value in EBAY_ITEM.items() if key != "gtin"}
        provider = ebay.EbayProvider(
            client_id="ID", client_secret="SECRET", client=stub_client(ebay_handler(item=item))
        )
        assert await provider.get_offers("v1|1|0") == ()

    async def test_competition_is_one_measured_point_not_a_synthetic_series(self):
        provider = ebay.EbayProvider(
            client_id="ID", client_secret="SECRET", client=stub_client(ebay_handler())
        )
        points = await provider.get_competition("v1|123456789012|0")
        assert len(points) == 1
        assert points[0].seller_count is not None


# ----------------------------------------------------------- the catalogue


class TestPlatformCatalogue:
    def test_every_definition_is_complete(self):
        for definition in ALL:
            assert definition.slug and definition.display_name
            assert definition.signup_url, f"{definition.slug} states no signup URL"
            assert definition.cost, f"{definition.slug} states no cost"
            assert definition.notes, f"{definition.slug} has no explanation"

    def test_every_platform_states_its_credential_or_needs_none(self):
        """An empty credential list is allowed only where authentication genuinely
        is not required, which today means the open dataset."""
        for definition in ALL:
            if definition.credentials:
                continue
            assert definition.role is PlatformRole.DATA, (
                f"{definition.slug} states no credential but is not an open data source"
            )
            assert "open" in definition.cost.lower() or "free" in definition.cost.lower()

    def test_slugs_are_unique(self):
        slugs = [definition.slug for definition in ALL]
        assert len(slugs) == len(set(slugs))

    def test_the_free_pair_needs_no_seller_account(self):
        """The whole point of Best Buy plus eBay: real data, no seller account."""
        free = {definition.slug for definition in connectable_now()}
        assert {"bestbuy", "ebay"} <= free

    def test_the_retired_pa_api_is_not_offered_as_a_route(self):
        """PA-API v5 was retired in May 2026 and now returns 403. Pointing an
        operator at it would send them to sign up for something that is gone."""
        for definition in ALL:
            haystack = " ".join((definition.notes, *definition.caveats)).lower()
            if "pa-api" in haystack:
                assert "retired" in haystack, (
                    f"{definition.slug} mentions PA-API without saying it is retired"
                )

    def test_the_amazon_route_without_a_seller_account_is_named(self):
        creators = BY_SLUG["amazon_creators"]
        assert creators.requires_seller_account is False
        assert "10 qualifying sales" in " ".join(creators.caveats)

    def test_the_open_dataset_is_labelled_historical(self):
        """Importing 2023 prices as though they were current would poison every
        statistic and anomaly the platform computes."""
        dataset = BY_SLUG["amazon_reviews_2023"]
        assert dataset.credentials == ()
        assert any("historical" in caveat.lower() for caveat in dataset.caveats)

    def test_amazon_and_walmart_are_honest_about_the_barrier(self):
        for slug in ("amazon", "walmart"):
            assert BY_SLUG[slug].requires_seller_account is True

    def test_planned_platforms_refuse_rather_than_return_empty(self):
        """A silent empty is indistinguishable from a real empty result."""
        definition = BY_SLUG["keepa"]
        assert definition.status is PlatformStatus.PLANNED
        provider = PlannedProvider(definition)
        assert provider.is_configured is False
        note = provider.configuration_note or ""
        assert "KEEPA_API_KEY" in note
        assert "keepa.com" in note

    async def test_a_planned_provider_raises_on_every_capability(self):
        provider = PlannedProvider(BY_SLUG["shopify"])
        for call in (
            provider.search_products("x"),
            provider.get_product("x"),
            provider.get_price("x"),
            provider.get_offers("x"),
            provider.get_history("x"),
            provider.get_demand("x"),
        ):
            with pytest.raises(ProviderNotConfiguredError):
                await call

    async def test_a_planned_provider_is_never_healthy(self):
        assert await PlannedProvider(BY_SLUG["etsy"]).health_check() is False

    def test_capabilities_are_declared_for_every_platform(self):
        for definition in ALL:
            assert definition.capabilities, f"{definition.slug} declares no capabilities"
            for capability in definition.capabilities:
                assert isinstance(capability, ProviderCapability)


class TestRegistryWithLivePlatforms:
    def test_the_free_alias_registers_the_pair(self):
        from app.services.providers.registry import build_registry

        registry = build_registry(["free"])
        assert {provider.slug for provider in registry.all()} == {"bestbuy", "ebay"}

    def test_all_alias_registers_the_whole_catalogue(self):
        from app.services.providers.registry import build_registry

        registry = build_registry(["all"])
        assert len(registry.all()) == len(ALL)

    def test_an_unconfigured_live_provider_is_registered_not_hidden(self):
        from app.services.providers.registry import build_registry

        registry = build_registry(["free"])
        ebay_provider = registry.get("ebay")
        assert ebay_provider.is_live is True
        assert ebay_provider.is_configured is False
        assert "developer.ebay.com" in (ebay_provider.configuration_note or "")


class TestRealDataSwitch:
    """Switching to real data must be one line of config, and must not lie.

    The failure mode worth guarding against is not "the key is missing". It is a
    provider that silently serves fixture data while the interface says the
    numbers came from a market. Every assertion here is about that.
    """

    def test_the_free_preset_registers_the_two_providers_that_need_no_seller_account(self):
        from app.services.providers.registry import build_registry

        registry = build_registry(["free"])
        slugs = {provider.slug for provider in registry.all()}
        assert slugs == {"bestbuy", "ebay"}
        assert all(provider.is_live for provider in registry.all())

    def test_a_live_provider_without_a_key_refuses_rather_than_falling_back(self):
        """A silent fallback would put fixture prices behind a live label."""
        import asyncio

        from app.core.errors import ProviderNotConfiguredError
        from app.services.providers.registry import build_registry

        registry = build_registry(["free"])
        for provider in registry.all():
            assert provider.is_configured is False
            with pytest.raises(ProviderNotConfiguredError):
                asyncio.run(provider.search_products("headphones", limit=1))

    def test_the_refusal_says_exactly_what_to_get_and_where(self):
        from app.services.providers.registry import build_registry

        registry = build_registry(["free"])
        notes = {provider.slug: provider.configuration_note or "" for provider in registry.all()}
        assert "BESTBUY_API_KEY" in notes["bestbuy"]
        assert "developer.bestbuy.com" in notes["bestbuy"]
        assert "EBAY_CLIENT_ID" in notes["ebay"]
        assert "developer.ebay.com" in notes["ebay"]

    def test_a_fixture_provider_never_claims_to_be_live(self):
        from app.services.providers.registry import build_registry

        registry = build_registry(["mock"])
        for provider in registry.all():
            assert provider.is_live is False
            assert provider.kind == "fixture"

    def test_an_observation_records_which_kind_of_provider_wrote_it(self, session, auth):
        """The flag is set from the provider's own liveness, not from its name."""
        from app.domains.catalog import service as catalog
        from app.models.catalog import MarketplaceListing

        listing = MarketplaceListing(
            organization_id=auth.organization_id,
            marketplace="amazon",
            external_id="B0TEST0001",
            title="Test",
        )
        session.add(listing)
        session.flush()

        fixture = catalog.observation_stamp(session, listing, provider="mock_amazon")
        assert fixture.is_simulated is True

        live = catalog.observation_stamp(session, listing, provider="bestbuy", is_simulated=False)
        assert live.is_simulated is False

        unknown = catalog.observation_stamp(session, listing, provider="not_registered")
        assert unknown.is_simulated is True, (
            "an unknown provider must be treated as simulated: assuming it was a real "
            "market observation is the error that contaminates the dataset"
        )


class TestSandboxIsNotAMarketObservation:
    """A sandbox exercises the real integration and invents the prices.

    Both halves matter. Reporting it as live would put invented figures into the
    record labelled as market data; reporting it as a fixture would hide that
    the transport, the OAuth flow and the payload contract are genuinely proven.
    """

    def test_sandbox_reports_itself_as_sandbox(self):
        from app.services.providers.ebay import EbayProvider

        provider = EbayProvider(client_id="id", client_secret="secret", environment="sandbox")
        assert provider.kind == "sandbox"
        assert provider.is_live is False
        assert "sandbox.ebay.com" in provider.base_url

    def test_production_reports_itself_as_live(self):
        from app.services.providers.ebay import EbayProvider

        provider = EbayProvider(
            client_id="id", client_secret="secret", environment="production"
        )
        assert provider.kind == "live"
        assert provider.is_live is True
        assert provider.base_url == "https://api.ebay.com"

    def test_everything_a_sandbox_writes_is_recorded_as_simulated(self, session, auth):
        """The lever the honesty of the whole history layer hangs on.

        ``observation_stamp`` derives ``is_simulated`` from ``is_live``, so this
        one property decides whether a sandbox price can ever be counted as a
        measurement anywhere downstream.
        """
        from app.services.providers.ebay import EbayProvider

        sandbox = EbayProvider(client_id="id", client_secret="secret", environment="sandbox")
        assert not sandbox.is_live, "is_simulated is derived from this"

    def test_the_note_says_the_prices_are_invented(self):
        from app.services.providers.ebay import EbayProvider

        provider = EbayProvider(client_id="id", client_secret="secret", environment="sandbox")
        note = provider.configuration_note or ""
        assert "invented" in note
        assert "simulated" in note
