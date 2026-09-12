"""Controlled vocabularies.

Enum values are stored as short strings rather than native database enums: new
risk codes, lifecycle states and match methods ship without a migration, and the
application remains the single owner of the vocabulary.
"""

from __future__ import annotations

from enum import StrEnum


class Marketplace(StrEnum):
    AMAZON = "amazon"
    WALMART = "walmart"
    MOCK = "mock"


class IdentifierType(StrEnum):
    GTIN = "gtin"
    UPC = "upc"
    EAN = "ean"
    ISBN = "isbn"
    ASIN = "asin"
    WALMART_ITEM_ID = "walmart_item_id"
    MPN = "mpn"
    SKU = "sku"
    MODEL = "model"


class MatchMethod(StrEnum):
    """Ordered strongest to weakest. The order is load-bearing: the resolver
    stops at the first method that produces a match."""

    GTIN = "gtin"
    UPC = "upc"
    EAN = "ean"
    MPN = "mpn"
    BRAND_MODEL = "brand_model"
    ATTRIBUTES = "attributes"
    TITLE_SIMILARITY = "title_similarity"
    AI_ASSISTED = "ai_assisted"
    MANUAL = "manual"


class MatchStatus(StrEnum):
    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"
    UNVERIFIED = "unverified"


class Condition(StrEnum):
    NEW = "new"
    RENEWED = "renewed"
    USED_LIKE_NEW = "used_like_new"
    USED_GOOD = "used_good"
    USED_ACCEPTABLE = "used_acceptable"
    UNKNOWN = "unknown"


class Availability(StrEnum):
    IN_STOCK = "in_stock"
    LIMITED = "limited"
    OUT_OF_STOCK = "out_of_stock"
    PREORDER = "preorder"
    UNKNOWN = "unknown"


class FulfillmentMethod(StrEnum):
    FBA = "fba"  # Amazon fulfils
    FBM = "fbm"  # merchant fulfils
    WFS = "wfs"  # Walmart fulfils
    SELLER = "seller"


class Confidence(StrEnum):
    """Used wherever a number would imply precision we do not have."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2, "critical": 3}[self.value]


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Recommendation(StrEnum):
    BUY = "buy"
    REVIEW = "review"
    PASS = "pass"


class OpportunityStatus(StrEnum):
    NEW = "new"
    REVIEW = "review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PURCHASED = "purchased"
    LISTED = "listed"
    SOLD = "sold"
    CLOSED = "closed"


#: Legal lifecycle transitions (spec §21). Enforced in the lifecycle service so
#: history can always be reconstructed from the event log.
OPPORTUNITY_TRANSITIONS: dict[OpportunityStatus, set[OpportunityStatus]] = {
    OpportunityStatus.NEW: {
        OpportunityStatus.REVIEW,
        OpportunityStatus.APPROVED,
        OpportunityStatus.REJECTED,
        OpportunityStatus.CLOSED,
    },
    OpportunityStatus.REVIEW: {
        OpportunityStatus.APPROVED,
        OpportunityStatus.REJECTED,
        OpportunityStatus.CLOSED,
    },
    OpportunityStatus.APPROVED: {
        OpportunityStatus.PURCHASED,
        OpportunityStatus.REJECTED,
        OpportunityStatus.CLOSED,
    },
    OpportunityStatus.REJECTED: {OpportunityStatus.REVIEW, OpportunityStatus.CLOSED},
    OpportunityStatus.PURCHASED: {OpportunityStatus.LISTED, OpportunityStatus.CLOSED},
    OpportunityStatus.LISTED: {OpportunityStatus.SOLD, OpportunityStatus.CLOSED},
    OpportunityStatus.SOLD: {OpportunityStatus.CLOSED},
    OpportunityStatus.CLOSED: set(),
}


class OpportunityEventType(StrEnum):
    CREATED = "created"
    RESCORED = "rescored"
    STATUS_CHANGED = "status_changed"
    DECISION_RECORDED = "decision_recorded"
    VALIDATION_RECORDED = "validation_recorded"
    PURCHASE_RECORDED = "purchase_recorded"
    SALE_RECORDED = "sale_recorded"
    OUTCOME_RECORDED = "outcome_recorded"
    NOTE_ADDED = "note_added"


class AnomalyType(StrEnum):
    NONE = "none"
    UNKNOWN = "unknown"  # insufficient history to judge
    BELOW_MEDIAN = "below_median"
    SEVERE_DISCOUNT = "severe_discount"
    ABOVE_MEDIAN = "above_median"
    SEVERE_PREMIUM = "severe_premium"
    PRICE_COLLAPSE = "price_collapse"
    PRICE_SPIKE = "price_spike"
    POTENTIAL_CLEARANCE = "potential_clearance"
    POTENTIAL_SHORTAGE = "potential_shortage"
    OUTLIER_SELLER = "outlier_seller"


class TrendDirection(StrEnum):
    RISING = "rising"
    FALLING = "falling"
    FLAT = "flat"
    UNKNOWN = "unknown"


class ProviderHealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class Direction(StrEnum):
    """Sourcing direction: buy on ``source``, sell on ``target``."""

    AMAZON_TO_WALMART = "amazon_to_walmart"
    WALMART_TO_AMAZON = "walmart_to_amazon"
    CUSTOM = "custom"


class SourcingChannel(StrEnum):
    """Where the inventory comes from. Arbitrage is one channel, not the model."""

    ONLINE_ARBITRAGE = "online_arbitrage"
    RETAIL_ARBITRAGE = "retail_arbitrage"
    WHOLESALE = "wholesale"
    DISTRIBUTOR = "distributor"
    LIQUIDATION = "liquidation"
    DIRECT_BRAND = "direct_brand"


class DataQualityDimension(StrEnum):
    PRICE = "price"
    PRODUCT_MATCH = "product_match"
    DEMAND = "demand"
    COMPETITION = "competition"
    HISTORICAL_DATA = "historical_data"
    AVAILABILITY = "availability"
