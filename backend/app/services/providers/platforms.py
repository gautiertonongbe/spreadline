"""The platform catalogue.

Every data source Spreadline knows about, whether or not it is implemented yet,
described in one place: what it is, what it could answer, what credential it
needs, and how far along it is.

This exists so that "which platforms can we connect, and what would each take"
is a question the running system answers, rather than something buried in a
roadmap document that drifts from the code. A platform listed here appears in
``/api/v1/platforms`` and in the Providers screen with its status, so the work
remaining to connect it is visible before anyone commits to it.

Three states, and the distinction is deliberate:

``LIVE``
    Implemented, wired to a real endpoint, and usable the moment its credential
    is set.

``ADAPTER_READY``
    Interface, capability declaration and payload mapper are implemented and
    tested, but the transport is not wired. Connecting it is finishing one
    method, not designing an integration.

``PLANNED``
    Described but not implemented. It registers, reports itself as unavailable
    with the reason, and refuses calls. It never returns a plausible empty
    result, because a silent empty is indistinguishable from a real one.

Nothing here fabricates data. A planned platform raises; it does not pretend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.models.enums import Marketplace
from app.services.providers.base import ProviderCapability


class PlatformStatus(StrEnum):
    LIVE = "live"
    ADAPTER_READY = "adapter_ready"
    PLANNED = "planned"


class PlatformRole(StrEnum):
    """What a platform is good for in a sourcing decision."""

    #: Somewhere inventory is bought.
    SOURCE = "source"
    #: Somewhere inventory is sold.
    EXIT = "exit"
    #: Both.
    BOTH = "both"
    #: Not a marketplace: sells data about other marketplaces.
    DATA = "data"


@dataclass(frozen=True)
class PlatformDefinition:
    slug: str
    display_name: str
    role: PlatformRole
    status: PlatformStatus
    #: Which marketplace enum this maps to, when it is a marketplace.
    marketplace: Marketplace | None
    #: What it could answer once connected.
    capabilities: tuple[ProviderCapability, ...]
    #: Environment variables that must be set.
    credentials: tuple[str, ...]
    #: Where to get them.
    signup_url: str
    #: Free, per request, or subscription. Stated plainly so cost is not a
    #: surprise discovered after the integration is written.
    cost: str
    #: Whether a seller account is a prerequisite. This is the single biggest
    #: barrier for most of these and is worth surfacing first.
    requires_seller_account: bool
    notes: str
    docs_url: str = ""
    #: Set when connecting it needs something beyond pasting a credential.
    caveats: tuple[str, ...] = field(default_factory=tuple)


ALL = (
    # ---------------------------------------------------------------- live
    PlatformDefinition(
        slug="bestbuy",
        display_name="Best Buy",
        role=PlatformRole.SOURCE,
        status=PlatformStatus.LIVE,
        marketplace=Marketplace.BESTBUY,
        capabilities=(
            ProviderCapability.SEARCH,
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.INVENTORY,
        ),
        credentials=("BESTBUY_API_KEY",),
        signup_url="https://developer.bestbuy.com",
        cost="Free developer key",
        requires_seller_account=False,
        notes=(
            "Returns UPC and shipping weight, which is unusual and exactly what "
            "the identity and fee engines need. A single retailer, so there is no "
            "seller count or offer set."
        ),
        docs_url="https://bestbuyapis.github.io/api-documentation/",
        caveats=("Commercial use requires a partner agreement with Best Buy.",),
    ),
    PlatformDefinition(
        slug="ebay",
        display_name="eBay",
        role=PlatformRole.BOTH,
        status=PlatformStatus.LIVE,
        marketplace=Marketplace.EBAY,
        capabilities=(
            ProviderCapability.SEARCH,
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.OFFERS,
            ProviderCapability.INVENTORY,
            ProviderCapability.COMPETITION,
        ),
        credentials=("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"),
        signup_url="https://developer.ebay.com",
        cost="Free, 5,000 calls per day",
        requires_seller_account=False,
        notes=(
            "Supports lookup by GTIN, so a product identified at a retailer can be "
            "found on the marketplace by the same identifier. A GTIN search also "
            "returns every concurrent listing, which is a genuine offer set."
        ),
        docs_url="https://developer.ebay.com/api-docs/buy/browse/overview.html",
    ),
    # -------------------------------------------------------- adapter ready
    PlatformDefinition(
        slug="amazon",
        display_name="Amazon",
        role=PlatformRole.BOTH,
        status=PlatformStatus.ADAPTER_READY,
        marketplace=Marketplace.AMAZON,
        capabilities=(
            ProviderCapability.SEARCH,
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.OFFERS,
            ProviderCapability.INVENTORY,
            ProviderCapability.DEMAND,
        ),
        credentials=("AMAZON_PROVIDER_CREDENTIALS",),
        signup_url="https://developer.amazonservices.com",
        cost="Free with a seller account",
        requires_seller_account=True,
        notes=(
            "Payload mappers are implemented and tested. The transport is unwired "
            "because SP-API needs LWA token exchange and request signing, which "
            "cannot be written against credentials that do not exist yet."
        ),
        docs_url="https://developer-docs.amazon.com/sp-api/",
        caveats=(
            "SP-API requires a Professional seller account and developer app registration.",
            "PA-API is the alternative and requires an Associates account with qualifying sales.",
        ),
    ),
    PlatformDefinition(
        slug="walmart",
        display_name="Walmart",
        role=PlatformRole.BOTH,
        status=PlatformStatus.ADAPTER_READY,
        marketplace=Marketplace.WALMART,
        capabilities=(
            ProviderCapability.SEARCH,
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.OFFERS,
            ProviderCapability.INVENTORY,
        ),
        credentials=("WALMART_PROVIDER_CREDENTIALS",),
        signup_url="https://developer.walmart.com",
        cost="Free with a seller account",
        requires_seller_account=True,
        notes=(
            "Payload mappers are implemented and tested. The Marketplace API uses "
            "an OAuth2 client-credentials flow plus a signed header."
        ),
        docs_url="https://developer.walmart.com/home/us-mp/",
    ),
    # ------------------------------------------------------ planned: markets
    PlatformDefinition(
        slug="shopify",
        display_name="Shopify",
        role=PlatformRole.EXIT,
        status=PlatformStatus.PLANNED,
        marketplace=None,
        capabilities=(
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.INVENTORY,
        ),
        credentials=("SHOPIFY_SHOP_DOMAIN", "SHOPIFY_ACCESS_TOKEN"),
        signup_url="https://shopify.dev",
        cost="Free with a Shopify plan",
        requires_seller_account=True,
        notes=(
            "The operator's own store as an exit market. Admin API gives real "
            "inventory and realised sell-through, which is the best possible "
            "outcome data: actual, not inferred."
        ),
        docs_url="https://shopify.dev/docs/api/admin-graphql",
    ),
    PlatformDefinition(
        slug="etsy",
        display_name="Etsy",
        role=PlatformRole.BOTH,
        status=PlatformStatus.PLANNED,
        marketplace=None,
        capabilities=(
            ProviderCapability.SEARCH,
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
        ),
        credentials=("ETSY_API_KEY",),
        signup_url="https://www.etsy.com/developers",
        cost="Free developer key",
        requires_seller_account=False,
        notes=(
            "Open API v3. Relevant for handmade and vintage categories rather "
            "than general retail."
        ),
        docs_url="https://developers.etsy.com/documentation/",
    ),
    PlatformDefinition(
        slug="target",
        display_name="Target",
        role=PlatformRole.SOURCE,
        status=PlatformStatus.PLANNED,
        marketplace=None,
        capabilities=(ProviderCapability.PRODUCT, ProviderCapability.PRICE),
        credentials=("REDCIRCLE_API_KEY",),
        signup_url="https://www.redcircleapi.com",
        cost="Per request",
        requires_seller_account=False,
        notes=(
            "Target publishes no public product API, so this routes through a "
            "licensed aggregator. Worth having as a second retail source alongside "
            "Best Buy."
        ),
    ),
    PlatformDefinition(
        slug="homedepot",
        display_name="The Home Depot",
        role=PlatformRole.SOURCE,
        status=PlatformStatus.PLANNED,
        marketplace=None,
        capabilities=(ProviderCapability.PRODUCT, ProviderCapability.PRICE),
        credentials=("BIGBOX_API_KEY",),
        signup_url="https://www.bigboxapi.com",
        cost="Per request",
        requires_seller_account=False,
        notes="No public product API; routes through a licensed aggregator.",
    ),
    # ---------------------------------------------------- planned: data feeds
    PlatformDefinition(
        slug="keepa",
        display_name="Keepa",
        role=PlatformRole.DATA,
        status=PlatformStatus.PLANNED,
        marketplace=None,
        capabilities=(
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.HISTORY,
            ProviderCapability.DEMAND,
            ProviderCapability.COMPETITION,
        ),
        credentials=("KEEPA_API_KEY",),
        signup_url="https://keepa.com/#!api",
        cost="Subscription, from about 19 EUR per month",
        requires_seller_account=False,
        notes=(
            "The highest-value addition to this platform by some margin: years of "
            "Amazon price and sales-rank history, which is the one input the "
            "statistics and anomaly engines cannot build quickly on their own. "
            "Spreadline otherwise accumulates history one observation at a time."
        ),
        docs_url="https://keepa.com/#!discuss/t/product-object/116",
        caveats=("History arrives CSV-encoded in a Keepa-specific format that needs decoding.",),
    ),
    PlatformDefinition(
        slug="rainforest",
        display_name="Rainforest API (Amazon)",
        role=PlatformRole.DATA,
        status=PlatformStatus.PLANNED,
        marketplace=None,
        capabilities=(
            ProviderCapability.SEARCH,
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.OFFERS,
        ),
        credentials=("RAINFOREST_API_KEY",),
        signup_url="https://www.rainforestapi.com",
        cost="Per request, free trial credits",
        requires_seller_account=False,
        notes=(
            "Amazon product, offer and rank data without a seller account. The "
            "pragmatic way to get Amazon coverage before SP-API access exists."
        ),
    ),
    PlatformDefinition(
        slug="bluecart",
        display_name="BlueCart API (Walmart)",
        role=PlatformRole.DATA,
        status=PlatformStatus.PLANNED,
        marketplace=None,
        capabilities=(
            ProviderCapability.SEARCH,
            ProviderCapability.PRODUCT,
            ProviderCapability.PRICE,
            ProviderCapability.OFFERS,
        ),
        credentials=("BLUECART_API_KEY",),
        signup_url="https://www.bluecartapi.com",
        cost="Per request, free trial credits",
        requires_seller_account=False,
        notes="Walmart counterpart to Rainforest, same vendor and same request shape.",
    ),
    PlatformDefinition(
        slug="serpapi",
        display_name="SerpApi",
        role=PlatformRole.DATA,
        status=PlatformStatus.PLANNED,
        marketplace=None,
        capabilities=(ProviderCapability.SEARCH, ProviderCapability.PRODUCT),
        credentials=("SERPAPI_API_KEY",),
        signup_url="https://serpapi.com",
        cost="Free tier of 100 searches per month, then subscription",
        requires_seller_account=False,
        notes=(
            "One key covering Amazon, Walmart, eBay and Google Shopping engines. "
            "The free tier is small but real, and useful for spot checks."
        ),
    ),
    # ------------------------------------------------------ planned: wholesale
    PlatformDefinition(
        slug="faire",
        display_name="Faire",
        role=PlatformRole.SOURCE,
        status=PlatformStatus.PLANNED,
        marketplace=None,
        capabilities=(ProviderCapability.PRODUCT, ProviderCapability.PRICE),
        credentials=("FAIRE_ACCESS_TOKEN",),
        signup_url="https://faire.com/api",
        cost="Free with a retailer account",
        requires_seller_account=True,
        notes=(
            "Wholesale sourcing, which is the channel the platform is meant to "
            "grow into. Wholesale cost per unit against a marketplace exit is the "
            "same calculation as arbitrage with a better acquisition price."
        ),
    ),
    PlatformDefinition(
        slug="alibaba",
        display_name="Alibaba",
        role=PlatformRole.SOURCE,
        status=PlatformStatus.PLANNED,
        marketplace=None,
        capabilities=(ProviderCapability.SEARCH, ProviderCapability.PRODUCT),
        credentials=("ALIBABA_APP_KEY", "ALIBABA_APP_SECRET"),
        signup_url="https://open.alibaba.com",
        cost="Free with an approved application",
        requires_seller_account=False,
        notes=(
            "Distributor and manufacturer sourcing. Lead times and minimum order "
            "quantities would need modelling before the economics are meaningful."
        ),
        caveats=(
            "MOQ and lead time are not yet inputs to the profitability engine.",
            "Landed cost needs duty and freight, which the fee model does not cover.",
        ),
    ),
)

BY_SLUG = {item.slug: item for item in ALL}


def by_status(status: PlatformStatus) -> tuple[PlatformDefinition, ...]:
    return tuple(item for item in ALL if item.status is status)


def connectable_now() -> tuple[PlatformDefinition, ...]:
    """Platforms that need only a credential to start returning real data."""
    return tuple(
        item
        for item in ALL
        if item.status is PlatformStatus.LIVE and not item.requires_seller_account
    )
