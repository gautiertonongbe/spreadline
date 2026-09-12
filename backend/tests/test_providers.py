"""Provider layer: interface discipline, reliability policy and payload mapping."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal

import pytest

from app.core.errors import (
    ProviderCapabilityError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderUnavailableError,
)
from app.models.enums import Availability, Condition, Marketplace
from app.services.providers import amazon, walmart
from app.services.providers.base import (
    MarketplaceProvider,
    ProviderCapability,
    RawSearchResult,
)
from app.services.providers.mock import MockProvider
from app.services.providers.registry import build_registry
from app.services.providers.reliability import (
    CircuitBreaker,
    RateLimiter,
    ReliableProvider,
)


class TestMockProvider:
    async def test_search_finds_by_title(self):
        provider = MockProvider(Marketplace.AMAZON)
        result = await provider.search_products("sony headphones")
        assert result.listings
        assert any("Sony" in item.title for item in result.listings)

    async def test_search_accepts_an_identifier_as_an_exact_lookup(self):
        provider = MockProvider(Marketplace.AMAZON)
        result = await provider.search_products("B09XS7JWHH")
        assert result.listings[0].external_id == "B09XS7JWHH"

    async def test_thin_history_is_returned_thin(self):
        """A three-day-old listing must not come back with a year of prices."""
        provider = MockProvider(Marketplace.AMAZON)
        points = await provider.get_history("B0CX1Y2Z3Q", days=365)
        assert len(points) == 3

    async def test_missing_demand_returns_nothing_rather_than_zeros(self):
        provider = MockProvider(Marketplace.AMAZON)
        assert await provider.get_demand("B0AAA111BB") == ()

    async def test_history_is_deterministic_across_instances(self):
        """Fixture reproducibility is what makes a failing test mean something."""
        first = await MockProvider(Marketplace.AMAZON).get_history("B09XS7JWHH", days=90)
        second = await MockProvider(Marketplace.AMAZON).get_history("B09XS7JWHH", days=90)
        assert [p.price for p in first] == [p.price for p in second]

    async def test_current_price_is_the_last_history_point(self):
        provider = MockProvider(Marketplace.AMAZON)
        listing = await provider.get_product("B09XS7JWHH")
        points = await provider.get_history("B09XS7JWHH", days=30)
        assert points[-1].price == listing.price

    async def test_anomaly_fixture_history_sits_above_the_shocked_price(self):
        provider = MockProvider(Marketplace.WALMART)
        points = await provider.get_history("WM-440129983", days=200)
        current = points[-1].price
        earlier = sorted(p.price for p in points[:-1])
        median = earlier[len(earlier) // 2]
        assert current < median * Decimal("0.7")

    async def test_unknown_listing_raises_not_found(self):
        from app.core.errors import NotFoundError

        with pytest.raises(NotFoundError):
            await MockProvider(Marketplace.AMAZON).get_product("DOES-NOT-EXIST")

    async def test_mock_declares_itself_as_not_live(self):
        provider = MockProvider(Marketplace.AMAZON)
        assert provider.is_live is False
        assert "Fixture data" in (provider.configuration_note or "")


class TestCapabilityDiscipline:
    async def test_unsupported_capability_raises_rather_than_returning_empty(self):
        """The distinction between 'no data' and 'cannot answer' must survive."""

        class Limited(MarketplaceProvider):
            slug = "limited"
            marketplace = Marketplace.AMAZON
            display_name = "Limited"
            capabilities = frozenset({ProviderCapability.PRODUCT})

        with pytest.raises(ProviderCapabilityError):
            await Limited().get_history("X")

    async def test_unconfigured_provider_refuses_rather_than_faking(self):
        provider = amazon.AmazonProvider()
        assert provider.is_configured is False
        with pytest.raises(ProviderNotConfiguredError):
            await provider.get_product("B09XS7JWHH")

    def test_unconfigured_provider_explains_what_is_missing(self):
        note = walmart.WalmartProvider().configuration_note or ""
        assert "WALMART_PROVIDER_CREDENTIALS" in note
        assert "mapper are implemented" in note


class TestRateLimiter:
    async def test_burst_is_allowed_then_throttled(self):
        limiter = RateLimiter(rate_per_second=50, burst=2)
        assert await limiter.acquire() == 0.0
        assert await limiter.acquire() == 0.0
        assert await limiter.acquire() > 0


class TestCircuitBreaker:
    def test_opens_after_the_threshold(self):
        breaker = CircuitBreaker(failure_threshold=3, reset_seconds=60)
        for _ in range(3):
            assert breaker.allows_request()
            breaker.record_failure()
        assert breaker.allows_request() is False

    def test_success_closes_it_again(self):
        breaker = CircuitBreaker(failure_threshold=2, reset_seconds=60)
        breaker.record_failure()
        breaker.record_success()
        assert breaker.consecutive_failures == 0
        assert breaker.allows_request()

    def test_half_open_probe_reopens_on_failure(self):
        import time

        breaker = CircuitBreaker(failure_threshold=1, reset_seconds=60)
        breaker.record_failure()
        assert breaker.allows_request() is False

        # Pretend the reset window has elapsed: one probe is let through.
        breaker.opened_at = time.monotonic() - 61
        assert breaker.allows_request() is True
        assert breaker.state.value == "half_open"

        # The probe fails, so the circuit reopens for another full window.
        breaker.record_failure()
        assert breaker.allows_request() is False


class FlakyProvider(MarketplaceProvider):
    slug = "flaky"
    marketplace = Marketplace.AMAZON
    display_name = "Flaky"
    capabilities = frozenset({ProviderCapability.SEARCH})

    def __init__(self, failures: int):
        self.remaining = failures
        self.calls = 0

    async def search_products(self, query: str, *, limit: int = 20) -> RawSearchResult:
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise RuntimeError("transport blew up")
        return RawSearchResult(query=query)


class TestReliableProvider:
    async def test_transient_failures_are_retried(self):
        inner = FlakyProvider(failures=2)
        provider = ReliableProvider(inner, max_retries=3, rate_limit_per_second=1000)
        result = await provider.search_products("x")
        assert result.query == "x"
        assert inner.calls == 3
        assert provider.metrics.success_count == 1

    async def test_gives_up_after_max_retries(self):
        inner = FlakyProvider(failures=99)
        provider = ReliableProvider(inner, max_retries=1, rate_limit_per_second=1000)
        with pytest.raises(ProviderError):
            await provider.search_products("x")
        assert inner.calls == 2
        assert provider.metrics.failure_count == 1

    async def test_permanent_errors_are_not_retried(self):
        """Retrying a capability error burns quota to reach the same answer."""

        class Unsupported(MarketplaceProvider):
            slug = "unsupported"
            marketplace = Marketplace.AMAZON
            display_name = "Unsupported"
            capabilities = frozenset({ProviderCapability.SEARCH})
            calls = 0

            async def search_products(self, query: str, *, limit: int = 20):
                type(self).calls += 1
                raise ProviderCapabilityError("nope")

        inner = Unsupported()
        provider = ReliableProvider(inner, max_retries=3, rate_limit_per_second=1000)
        with pytest.raises(ProviderCapabilityError):
            await provider.search_products("x")
        assert Unsupported.calls == 1

    async def test_open_circuit_short_circuits(self):
        inner = FlakyProvider(failures=99)
        provider = ReliableProvider(inner, max_retries=0, rate_limit_per_second=1000)
        provider.circuit.failure_threshold = 1
        provider.circuit.reset_seconds = 60
        with pytest.raises(ProviderError):
            await provider.search_products("x")
        calls_before = inner.calls
        with pytest.raises(ProviderUnavailableError):
            await provider.search_products("x")
        assert inner.calls == calls_before

    async def test_timeout_is_recorded_as_a_failure(self):
        class Slow(MarketplaceProvider):
            slug = "slow"
            marketplace = Marketplace.AMAZON
            display_name = "Slow"
            capabilities = frozenset({ProviderCapability.SEARCH})

            async def search_products(self, query: str, *, limit: int = 20):
                await asyncio.sleep(5)

        provider = ReliableProvider(
            Slow(), timeout_seconds=0.01, max_retries=0, rate_limit_per_second=1000
        )
        with pytest.raises(ProviderError):
            await provider.search_products("x")
        assert provider.metrics.failure_count == 1

    async def test_call_records_are_emitted(self):
        records = []
        provider = ReliableProvider(
            FlakyProvider(failures=0), on_call=records.append, rate_limit_per_second=1000
        )
        await provider.search_products("x")
        assert len(records) == 1
        assert records[0].succeeded
        assert records[0].capability == "search"


class TestRegistry:
    def test_mock_alias_registers_both_marketplaces(self):
        registry = build_registry(["mock"])
        slugs = {provider.slug for provider in registry.all()}
        assert slugs == {"mock_amazon", "mock_walmart"}

    def test_unconfigured_providers_are_registered_but_not_selected(self):
        registry = build_registry(["mock", "amazon"])
        assert registry.get("amazon").is_configured is False
        chosen = registry.for_marketplace(Marketplace.AMAZON)
        assert chosen.slug == "mock_amazon"

    def test_capability_filter_applies(self):
        registry = build_registry(["mock"])
        chosen = registry.for_marketplace(
            Marketplace.WALMART, capability=ProviderCapability.HISTORY
        )
        assert chosen.slug == "mock_walmart"

    def test_no_provider_for_marketplace_raises(self):
        registry = build_registry([])
        with pytest.raises(ProviderError):
            registry.for_marketplace(Marketplace.AMAZON)

    def test_unknown_slug_is_skipped_not_fatal(self):
        registry = build_registry(["mock", "nonexistent-provider"])
        assert {p.slug for p in registry.all()} == {"mock_amazon", "mock_walmart"}


class TestAmazonMapper:
    def test_maps_a_realistic_payload(self):
        listing = amazon.map_listing(
            {
                "asin": "B09XS7JWHH",
                "title": "Sony WH-1000XM5",
                "brand": "Sony",
                "upc": "027242923058",
                "price": {"amount": "328.00", "currency": "USD"},
                "availability": "In Stock",
                "salesRanks": [{"rank": 142, "title": "Electronics"}],
                "review_count": 48210,
                "rating": 4.6,
                "condition": "new",
            }
        )
        assert listing.marketplace is Marketplace.AMAZON
        assert listing.external_id == "B09XS7JWHH"
        assert listing.price == Decimal("328.0000")
        assert listing.availability is Availability.IN_STOCK
        assert listing.sales_rank == 142
        assert any(i.identifier_type == "upc" for i in listing.identifiers)

    def test_missing_price_is_none_not_zero(self):
        listing = amazon.map_listing({"asin": "B0", "title": "x"})
        assert listing.price is None

    def test_money_parsing_handles_strings_and_objects(self):
        assert amazon.parse_money("$1,234.56") == Decimal("1234.5600")
        assert amazon.parse_money({"amount": 10}) == Decimal("10.0000")
        assert amazon.parse_money(None) is None

    def test_timestamp_falls_back_to_now(self):
        assert isinstance(amazon.parse_timestamp("not-a-date"), datetime)

    def test_offer_mapping(self):
        offer = amazon.map_offer(
            {
                "price": "25.00",
                "shipping": "0",
                "SellerId": "A123",
                "IsBuyBoxWinner": True,
                "fulfillmentChannel": "Amazon",
                "observed_at": "2026-01-01T00:00:00Z",
            }
        )
        assert offer.is_buy_box
        assert offer.fulfillment == "fba"
        assert offer.landed_price == Decimal("25.0000")


class TestWalmartMapper:
    def test_maps_a_realistic_payload(self):
        listing = walmart.map_listing(
            {
                "itemId": "598712344",
                "name": "Sony WH1000XM5",
                "brandName": "Sony",
                "salePrice": 248.0,
                "upc": "027242923058",
                "stock": "Available",
                "numberOfSellers": 2,
                "customerRating": "4.5",
            }
        )
        assert listing.marketplace is Marketplace.WALMART
        assert listing.price == Decimal("248.0000")
        assert listing.availability is Availability.IN_STOCK
        assert listing.condition is Condition.NEW

    def test_sale_price_wins_over_list_price(self):
        listing = walmart.map_listing({"itemId": "1", "name": "x", "price": 100, "salePrice": 80})
        assert listing.price == Decimal("80.0000")

    def test_wfs_offer_is_labelled(self):
        offer = walmart.map_offer({"price": 20, "wfsEligible": True, "sellerName": "Walmart.com"})
        assert offer.fulfillment == "wfs"
        assert offer.is_marketplace_seller
