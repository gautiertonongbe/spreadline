"""Request and response schemas for the analysis workflow."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.enums import Marketplace, SourcingChannel


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
