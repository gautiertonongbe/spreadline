"""The provider used for a platform that is described but not implemented.

A planned platform still registers, so it is visible in the provider list with
its status and the credential it is waiting for. What it will not do is answer.
Every capability call raises ``ProviderNotConfiguredError`` carrying the setup
instructions.

That refusal is the point. A stub that returned an empty list would be
indistinguishable from a real provider reporting genuinely empty results, and the
difference matters everywhere in this platform: no offers means no competition
data, which lowers confidence and changes a decision. Silence must be loud.
"""

from __future__ import annotations

from app.core.errors import ProviderNotConfiguredError
from app.models.enums import Marketplace
from app.services.providers.base import MarketplaceProvider
from app.services.providers.platforms import PlatformDefinition, PlatformStatus


class PlannedProvider(MarketplaceProvider):
    """Registers a platform without implementing it."""

    is_live = False
    kind = "planned"

    def __init__(self, definition: PlatformDefinition):
        self.definition = definition
        self.slug = definition.slug
        self.display_name = definition.display_name
        self.marketplace = definition.marketplace or Marketplace.MOCK
        self.capabilities = frozenset(definition.capabilities)

    @property
    def is_configured(self) -> bool:
        return False

    @property
    def configuration_note(self) -> str | None:
        definition = self.definition
        parts = [
            f"{definition.status.value.replace('_', ' ').title()}.",
            definition.notes,
            f"Credentials: {', '.join(definition.credentials)}.",
            f"Sign up: {definition.signup_url}.",
            f"Cost: {definition.cost}.",
        ]
        if definition.requires_seller_account:
            parts.append("Requires an active seller or retailer account.")
        parts.extend(definition.caveats)
        return " ".join(part for part in parts if part)

    def _refuse(self) -> ProviderNotConfiguredError:
        return ProviderNotConfiguredError(
            f"{self.display_name} is not connected. {self.configuration_note}",
            provider=self.slug,
        )

    async def search_products(self, query: str, *, limit: int = 20):
        raise self._refuse()

    async def get_product(self, external_id: str):
        raise self._refuse()

    async def get_product_by_identifier(self, identifier_type: str, value: str):
        raise self._refuse()

    async def get_price(self, external_id: str):
        raise self._refuse()

    async def get_offers(self, external_id: str):
        raise self._refuse()

    async def get_inventory(self, external_id: str):
        raise self._refuse()

    async def get_history(self, external_id: str, *, days: int = 90):
        raise self._refuse()

    async def get_demand(self, external_id: str, *, days: int = 90):
        raise self._refuse()

    async def get_competition(self, external_id: str, *, days: int = 30):
        raise self._refuse()

    async def health_check(self) -> bool:
        return False


def planned_providers() -> list[PlannedProvider]:
    """One provider per platform that is described but not yet implemented."""
    from app.services.providers.platforms import ALL

    return [
        PlannedProvider(definition)
        for definition in ALL
        if definition.status is PlatformStatus.PLANNED
    ]
