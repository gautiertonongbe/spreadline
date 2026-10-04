"""Controlled vocabularies.

Enum values are stored as short strings rather than native database enums: new
risk codes, lifecycle states and match methods ship without a migration, and the
application remains the single owner of the vocabulary.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class Marketplace(StrEnum):
    AMAZON = "amazon"
    WALMART = "walmart"
    #: Free official APIs, usable for real data without a seller account.
    EBAY = "ebay"
    BESTBUY = "bestbuy"
    MOCK = "mock"


class IdentifierType(StrEnum):
    GTIN = "gtin"
    UPC = "upc"
    EAN = "ean"
    ISBN = "isbn"
    ASIN = "asin"
    WALMART_ITEM_ID = "walmart_item_id"
    EBAY_ITEM_ID = "ebay_item_id"
    BESTBUY_SKU = "bestbuy_sku"
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
    SELLER = "seller"  # the operator ships it, as on eBay


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


class RiskCategory(StrEnum):
    """The kinds of risk Spreadline assesses separately.

    They are kept apart because they fail differently and are mitigated
    differently. A volatile exit price is waited out or hedged with a lower buy
    price; a rejected product match is not a risk to be priced, it is a reason
    not to buy at all; thin data is fixed by fetching more. Averaging them into
    one number loses exactly the information that decides what to do next.
    """

    #: How the prices themselves move: volatility, trend, anomalies, baselines.
    PRICE = "price"
    #: How many others are selling it, and whether that number is growing.
    COMPETITION = "competition"
    #: Whether it sells at all, how fast, and whether that is slowing.
    DEMAND = "demand"
    #: Whether there is stock to buy, and how much.
    INVENTORY = "inventory"
    #: Whether the two listings are the same product.
    PRODUCT_MATCH = "product_match"
    #: Whether the evidence behind the other categories is present and fresh.
    DATA_QUALITY = "data_quality"
    #: Gating, restricted brands, categories with high return or counterfeit rates.
    BRAND_CATEGORY = "brand_category"
    #: The economics themselves: no profit, or a margin too thin to survive a move.
    ECONOMICS = "economics"


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
    BESTBUY_TO_EBAY = "bestbuy_to_ebay"
    EBAY_TO_AMAZON = "ebay_to_amazon"
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

class AutonomyLevel(IntEnum):
    """How much Spreadline is allowed to do without a human.

    Autonomy is earned, not assumed. Each level is unlocked by demonstrated
    performance against explicit criteria, never by elapsed time and never
    automatically: the gate reports eligibility, a person acts on it.
    """

    OBSERVE = 0
    RECOMMEND = 1
    HUMAN_APPROVAL = 2
    CONTROLLED = 3
    LIMITED = 4
    EXPANDED = 5
    PORTFOLIO = 6
    FULL = 7

    @property
    def label(self) -> str:
        return {
            0: "Observe",
            1: "Recommend",
            2: "Human approval",
            3: "Controlled autonomy",
            4: "Limited autonomy",
            5: "Expanded autonomy",
            6: "Portfolio autonomy",
            7: "Full authorised autonomy",
        }[int(self)]

    @property
    def deploys_capital(self) -> bool:
        """Whether this level may commit capital without a human decision."""
        return int(self) >= int(AutonomyLevel.CONTROLLED)


class AgentStage(StrEnum):
    """The stages of one decision, named for the role each plays.

    These are the existing deterministic engines under the names an investment
    organisation would use for them. Naming them is what makes a decision
    auditable stage by stage, and what lets two stages disagree in a way the
    system can escalate rather than average away.
    """

    SCOUT = "scout"
    UNDERWRITING = "underwriting"
    RISK = "risk"
    ELIGIBILITY = "eligibility"
    CAPITAL = "capital"
    POLICY = "policy"
    DECISION = "decision"
    INVENTORY = "inventory"
    SELL = "sell"
    OUTCOME = "outcome"


class AgentVerdict(StrEnum):
    """What one stage concluded."""

    PROCEED = "proceed"
    REVIEW = "review"
    REJECT = "reject"
    #: The stage could not reach a conclusion, which is not the same as rejecting.
    INCONCLUSIVE = "inconclusive"


class ExecutionMode(StrEnum):
    """Whether a decision moves real money."""

    #: Analysis only; no position is opened at all.
    OBSERVE = "observe"
    #: A position is recorded as if capital had been deployed, and tracked
    #: against real market outcomes, but nothing was bought.
    SHADOW = "shadow"
    #: Real capital, human-executed order.
    LIVE = "live"


class PositionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class BreakerState(StrEnum):
    OK = "ok"
    TRIPPED = "tripped"


class AutonomyEventType(StrEnum):
    POLICY_CHANGED = "policy_changed"
    LEVEL_CHANGED = "level_changed"
    EXPERIMENT_STARTED = "experiment_started"
    EXPERIMENT_STOPPED = "experiment_stopped"
    DECISION_AUTHORIZED = "decision_authorized"
    DECISION_BLOCKED = "decision_blocked"
    DECISION_ESCALATED = "decision_escalated"
    PLAN_COMMITTED = "plan_committed"
    EXECUTION_RECORDED = "execution_recorded"
    BREAKER_TRIPPED = "breaker_tripped"
    BREAKER_RESET = "breaker_reset"
    EMERGENCY_STOP = "emergency_stop"
    EMERGENCY_STOP_CLEARED = "emergency_stop_cleared"
    HUMAN_OVERRIDE = "human_override"
