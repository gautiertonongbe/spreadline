"""Provider registry.

One place that decides which providers exist, wraps each in the reliability
policy, and answers "who can tell me about this marketplace". Domains ask the
registry; they never construct a provider.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.models.enums import Marketplace
from app.services.providers.amazon import AmazonProvider
from app.services.providers.base import MarketplaceProvider, ProviderCapability, ProviderInfo
from app.services.providers.bestbuy import BestBuyProvider
from app.services.providers.ebay import EbayProvider
from app.services.providers.mock import MockProvider
from app.services.providers.planned import PlannedProvider
from app.services.providers.platforms import ALL as PLATFORMS
from app.services.providers.platforms import PlatformStatus
from app.services.providers.reliability import CallRecord, ReliableProvider
from app.services.providers.walmart import WalmartProvider

logger = get_logger(__name__)

#: slug -> factory. Adding a provider means adding one line here and one adapter.
PROVIDER_FACTORIES: dict[str, Callable[[], MarketplaceProvider]] = {
    "mock": lambda: MockProvider(Marketplace.AMAZON, slug="mock"),
    "mock_amazon": lambda: MockProvider(Marketplace.AMAZON),
    "mock_walmart": lambda: MockProvider(Marketplace.WALMART),
    "amazon": AmazonProvider,
    "walmart": WalmartProvider,
    # Live and free: a developer key is enough, no seller account required.
    "bestbuy": BestBuyProvider,
    "ebay": EbayProvider,
}

#: Platforms described in the catalogue but not implemented. They register so
#: they are visible with their status, and refuse every call.
PROVIDER_FACTORIES.update(
    {
        definition.slug: (lambda d=definition: PlannedProvider(d))
        for definition in PLATFORMS
        if definition.status is PlatformStatus.PLANNED
    }
)

#: Everything the catalogue knows about, for an operator who wants the whole
#: picture rather than only what is switched on.
ALL_PLATFORM_SLUGS = tuple(definition.slug for definition in PLATFORMS)

#: "mock" is shorthand for the pair of fixture providers, since one marketplace
#: on its own cannot produce a cross-market opportunity.
_ALIASES = {
    "mock": ("mock_amazon", "mock_walmart"),
    # The free pairing: a retailer to buy from and a marketplace to sell on,
    # both reachable with a developer key and no seller account.
    "free": ("bestbuy", "ebay"),
    # Everything in the catalogue, connected or not, so the provider screen can
    # show the full picture.
    "all": ALL_PLATFORM_SLUGS,
}


class ProviderRegistry:
    def __init__(self, *, on_call: Callable[[CallRecord], None] | None = None):
        self._providers: dict[str, ReliableProvider] = {}
        self._on_call = on_call

    def register(self, provider: MarketplaceProvider) -> ReliableProvider:
        wrapped = (
            provider
            if isinstance(provider, ReliableProvider)
            else ReliableProvider(provider, on_call=self._on_call)
        )
        self._providers[wrapped.slug] = wrapped
        return wrapped

    def get(self, slug: str) -> ReliableProvider:
        try:
            return self._providers[slug]
        except KeyError as exc:
            raise ProviderError(f"Provider '{slug}' is not registered.", provider=slug) from exc

    def all(self) -> list[ReliableProvider]:
        return list(self._providers.values())

    def for_marketplace(
        self, marketplace: Marketplace | str, *, capability: ProviderCapability | None = None
    ) -> ReliableProvider:
        """The best provider for a marketplace.

        Live, configured providers are preferred over fixtures: once credentials
        exist, the platform uses them without a code change. A fixture provider
        is only selected when nothing live can answer.
        """
        target = Marketplace(marketplace)
        candidates = [
            provider
            for provider in self._providers.values()
            if provider.marketplace is target
            and provider.is_configured
            and (capability is None or provider.supports(capability))
        ]
        if not candidates:
            raise ProviderError(
                f"No configured provider for {target.value}"
                + (f" supporting '{capability.value}'." if capability else "."),
                marketplace=target.value,
            )
        candidates.sort(key=lambda provider: (not provider.is_live, provider.slug))
        return candidates[0]

    def info(self) -> list[ProviderInfo]:
        return [provider.info() for provider in self._providers.values()]

    async def close(self) -> None:
        for provider in self._providers.values():
            await provider.close()


def build_registry(
    slugs: list[str] | None = None, *, on_call: Callable[[CallRecord], None] | None = None
) -> ProviderRegistry:
    registry = ProviderRegistry(on_call=on_call)
    requested = slugs if slugs is not None else settings.enabled_providers
    expanded: list[str] = []
    for slug in requested:
        expanded.extend(_ALIASES.get(slug, (slug,)))

    for slug in dict.fromkeys(expanded):
        factory = PROVIDER_FACTORIES.get(slug)
        if factory is None:
            logger.warning(
                "unknown provider in ENABLED_PROVIDERS", extra={"context": {"slug": slug}}
            )
            continue
        provider = factory()
        registry.register(provider)
        if not provider.is_configured:
            # Registered but unusable: it shows up in /providers with the reason,
            # rather than vanishing and leaving the operator guessing.
            logger.info(
                "provider registered but not configured",
                extra={"context": {"slug": slug, "note": provider.configuration_note}},
            )
    return registry


_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    """Process-wide registry. Replaceable in tests via ``set_registry``."""
    global _registry
    if _registry is None:
        _registry = build_registry()
    return _registry


def set_registry(registry: ProviderRegistry | None) -> None:
    global _registry
    _registry = registry
