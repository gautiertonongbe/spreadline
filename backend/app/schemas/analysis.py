"""Request and response schemas for the analysis workflow."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.enums import Availability, Condition, Marketplace, SourcingChannel


class AnalyzeRequest(BaseModel):
    """Analyse one source listing against one exit market.

    ``target_external_id`` is optional: when omitted the platform finds the
    counterpart itself, by identifier first and title search only as a fallback.
    """

    source_marketplace: Marketplace
    source_external_id: str = Field(min_length=1, max_length=64)
    target_marketplace: Marketplace
    target_external_id: str | None = Field(default=None, max_length=64)
    sourcing_channel: SourcingChannel = SourcingChannel.ONLINE_ARBITRAGE
    #: Per-request fee overrides, layered onto the marketplace defaults.
    fee_overrides: dict[str, Any] | None = None
    run_stress_test: bool = True
    persist: bool = True

    @model_validator(mode="after")
    def _distinct_markets(self) -> AnalyzeRequest:
        if self.source_marketplace == self.target_marketplace:
            raise ValueError("Source and exit marketplace must differ.")
        return self


class AnalyzeBothRequest(BaseModel):
    amazon_external_id: str = Field(min_length=1, max_length=64)
    walmart_external_id: str = Field(min_length=1, max_length=64)
    run_stress_test: bool = True


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=256)
    marketplace: Marketplace = Marketplace.AMAZON
    limit: int = Field(default=20, ge=1, le=50)


class SearchResultItem(BaseModel):
    marketplace: str
    external_id: str
    title: str
    brand: str | None = None
    category: str | None = None
    price: Decimal | None = None
    availability: str
    seller_count: int | None = None
    sales_rank: int | None = None
    url: str | None = None
    provider: str
    #: False when the row came from a fixture rather than a market observation.
    is_live_data: bool


class SearchResponse(BaseModel):
    query: str
    marketplace: str
    total_results: int | None = None
    truncated: bool = False
    items: list[SearchResultItem]


class BulkAnalyzeRequest(BaseModel):
    """CSV content, pasted or uploaded."""

    content: str = Field(min_length=1)
    default_source_marketplace: Marketplace = Marketplace.WALMART
    default_target_marketplace: Marketplace = Marketplace.AMAZON
    run_stress_test: bool = False


class CapitalSimulationRequest(BaseModel):
    available_capital: Decimal = Field(gt=0)
    max_position_size: Decimal | None = Field(default=None, gt=0)
    max_position_pct: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    min_roi: Decimal = Field(default=Decimal("0.20"), ge=0)
    max_risk_level: str = "medium"
    min_score: Decimal = Field(default=Decimal("50"), ge=0, le=100)
    max_positions: int | None = Field(default=None, ge=1)
    max_brand_pct: Decimal = Field(default=Decimal("0.40"), gt=0, le=1)
    max_category_pct: Decimal = Field(default=Decimal("0.50"), gt=0, le=1)
    min_position_size: Decimal = Field(default=Decimal("50"), ge=0)
    allowed_recommendations: list[str] = Field(default_factory=lambda: ["buy"])
    #: Restrict the candidate pool. Empty means every stored opportunity.
    opportunity_ids: list[str] | None = None
    save: bool = False
    name: str | None = None


class ManualSideRequest(BaseModel):
    """One side of a pair, as a person reads it off the product page.

    Only four fields are required, because a person typing at a keyboard will
    abandon a form that demands twelve. Everything else improves the answer and
    the response says which omissions cost what.
    """

    marketplace: Marketplace
    #: The ASIN, the item number, whatever the URL ends in. It is how the same
    #: product is recognised the next time it is entered.
    external_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=512)
    price: Decimal = Field(gt=0)

    url: str | None = Field(default=None, max_length=1000)
    brand: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=120)
    shipping: Decimal = Field(default=Decimal("0"), ge=0)
    condition: Condition = Condition.NEW
    availability: Availability = Availability.UNKNOWN

    #: gtin, upc, ean, isbn, asin or mpn. An identifier on both sides is what
    #: lets the match clear a policy threshold; titles alone cap at 60%.
    identifiers: dict[str, str] = Field(default_factory=dict)

    #: Absent rather than zero when not supplied, so demand and competition
    #: report no evidence instead of a flattering default.
    sales_rank: int | None = Field(default=None, ge=1)
    rank_category: str | None = Field(default=None, max_length=120)
    seller_count: int | None = Field(default=None, ge=0)
    offer_count: int | None = Field(default=None, ge=0)
    review_count: int | None = Field(default=None, ge=0)
    rating: Decimal | None = Field(default=None, ge=0, le=5)
    quantity_available: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)


class ManualAnalyzeRequest(BaseModel):
    """Analyse a pair somebody looked up by hand.

    No provider is called. A person read two public pages and typed what they
    saw, which is not scraping and needs no credential, and the same engine then
    judges it by the same standard it judges an API-fed pair by.
    """

    source: ManualSideRequest
    target: ManualSideRequest
    sourcing_channel: SourcingChannel = SourcingChannel.ONLINE_ARBITRAGE
    fee_overrides: dict[str, Any] | None = None
    run_stress_test: bool = True
    persist: bool = True

    @model_validator(mode="after")
    def _distinct_markets(self) -> ManualAnalyzeRequest:
        if self.source.marketplace == self.target.marketplace:
            raise ValueError(
                "The two sides have to be different marketplaces. Buying and selling "
                "in the same market is not a spread, it is a round trip."
            )
        return self


class PasteRequest(BaseModel):
    """A product page somebody copied and pasted.

    Deliberately not stored anywhere. A signed-in page carries the reader's
    name, address, cart and customer id, so the text is parsed in memory and
    discarded; only the fields the person confirms are ever written.
    """

    #: Generous, because "select all" on a product page is a lot of navigation
    #: and footer either side of the part that matters. Bounded, because an
    #: unbounded text field is a way to fill a database.
    text: str = Field(min_length=1, max_length=400_000)
    #: Optional. Detected from the text when not given.
    marketplace: Marketplace | None = None
