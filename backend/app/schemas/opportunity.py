"""Opportunity, product and portfolio schemas."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import OpportunityStatus
from app.schemas.common import APIModel


class ListingSummary(APIModel):
    id: str
    marketplace: str
    external_id: str
    title: str
    brand: str | None = None
    category: str | None = None
    url: str | None = None
    current_price: Decimal | None = None
    current_shipping: Decimal | None = None
    availability: str
    seller_count: int | None = None
    offer_count: int | None = None
    sales_rank: int | None = None
    review_count: int | None = None
    provider: str | None = None
    last_seen_at: datetime | None = None


class ProductSummary(APIModel):
    id: str
    title: str
    brand: str | None = None
    model: str | None = None
    category: str | None = None
    primary_gtin: str | None = None
    pack_count: int | None = None


class OpportunitySummary(APIModel):
    id: str
    product_id: str
    #: Denormalised for list views. Without it a row cannot be identified as a
    #: product at all, only as a pair of marketplaces.
    title: str | None = None
    brand: str | None = None
    direction: str
    sourcing_channel: str
    source_marketplace: str
    target_marketplace: str
    status: str
    recommendation: str
    score: Decimal | None = None
    acquisition_cost: Decimal | None = None
    expected_sale_price: Decimal | None = None
    net_profit: Decimal | None = None
    roi: Decimal | None = None
    margin: Decimal | None = None
    spread: Decimal | None = None
    risk_level: str
    risk_score: Decimal | None = None
    match_confidence: Decimal | None = None
    data_quality_score: Decimal | None = None
    demand_confidence: str
    analyzed_at: datetime | None = None
    created_at: datetime


class OpportunityDetail(OpportunitySummary):
    score_model_version: str | None = None
    score_components: dict[str, Any] = Field(default_factory=dict)
    explanation: dict[str, Any] = Field(default_factory=dict)
    recommended_quantity: int | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    notes: str | None = None
    product: ProductSummary | None = None
    source_listing: ListingSummary | None = None
    target_listing: ListingSummary | None = None
    #: Engine output, rendered by the detail endpoint.
    match: dict[str, Any] | None = None
    economics: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    stress_test: dict[str, Any] | None = None
    price_history: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    validations: list[dict[str, Any]] = Field(default_factory=list)


class DecisionRequest(BaseModel):
    status: OpportunityStatus
    note: str | None = Field(default=None, max_length=1000)


class ValidationRequest(BaseModel):
    """Personal testing mode. Every field is optional on purpose.

    An operator who only checked the price should not be forced to assert
    anything about the match, and a null answer is recorded as null.
    """

    actual_source_price: Decimal | None = Field(default=None, ge=0)
    actual_target_price: Decimal | None = Field(default=None, ge=0)
    actual_availability: str | None = None
    match_confirmed: bool | None = None
    profit_estimate_correct: bool | None = None
    would_buy: bool | None = None
    notes: str | None = Field(default=None, max_length=4000)


class PurchaseRequest(BaseModel):
    product_id: str
    quantity: int = Field(ge=1)
    unit_price: Decimal = Field(ge=0)
    purchased_at: datetime | None = None
    opportunity_id: str | None = None
    source_marketplace: str | None = None
    supplier: str | None = None
    sourcing_channel: str = "online_arbitrage"
    order_reference: str | None = None
    shipping_cost: Decimal = Field(default=Decimal("0"), ge=0)
    tax: Decimal = Field(default=Decimal("0"), ge=0)
    other_costs: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = "USD"
    expected_delivery: date | None = None
    notes: str | None = None


class PurchaseResponse(APIModel):
    id: str
    opportunity_id: str | None = None
    product_id: str
    quantity: int
    unit_price: Decimal
    shipping_cost: Decimal
    tax: Decimal
    other_costs: Decimal
    total_cost: Decimal
    currency: str
    supplier: str | None = None
    source_marketplace: str | None = None
    order_reference: str | None = None
    purchased_at: datetime
    notes: str | None = None


class SaleRequest(BaseModel):
    product_id: str
    marketplace: str
    quantity: int = Field(ge=1)
    unit_price: Decimal = Field(ge=0)
    sold_at: datetime | None = None
    purchase_id: str | None = None
    opportunity_id: str | None = None
    order_reference: str | None = None
    marketplace_fees: Decimal = Field(default=Decimal("0"), ge=0)
    fulfillment_fees: Decimal = Field(default=Decimal("0"), ge=0)
    storage_fees: Decimal = Field(default=Decimal("0"), ge=0)
    shipping_cost: Decimal = Field(default=Decimal("0"), ge=0)
    refunds: Decimal = Field(default=Decimal("0"), ge=0)
    other_costs: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = "USD"
    notes: str | None = None


class SaleResponse(APIModel):
    id: str
    purchase_id: str | None = None
    opportunity_id: str | None = None
    product_id: str
    marketplace: str
    quantity: int
    unit_price: Decimal
    gross_revenue: Decimal
    marketplace_fees: Decimal
    fulfillment_fees: Decimal
    storage_fees: Decimal
    shipping_cost: Decimal
    refunds: Decimal
    other_costs: Decimal
    net_proceeds: Decimal
    currency: str
    sold_at: datetime
    notes: str | None = None


class OutcomeResponse(APIModel):
    id: str
    opportunity_id: str | None = None
    purchase_id: str | None = None
    product_id: str
    quantity_purchased: int
    quantity_sold: int
    capital_deployed: Decimal
    predicted_unit_profit: Decimal | None = None
    predicted_total_profit: Decimal | None = None
    predicted_roi: Decimal | None = None
    predicted_recommendation: str | None = None
    actual_revenue: Decimal | None = None
    actual_costs: Decimal | None = None
    actual_profit: Decimal | None = None
    actual_roi: Decimal | None = None
    profit_variance: Decimal | None = None
    profit_variance_pct: Decimal | None = None
    roi_variance: Decimal | None = None
    days_to_sell: int | None = None
    is_closed: bool
    result: str
