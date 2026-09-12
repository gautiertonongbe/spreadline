"""Fee assumptions.

Every number the profitability engine uses that is not observed market data lives
here, is configurable, and is fingerprinted. The fingerprint travels with each
calculation so a fee change published today cannot silently rewrite a prediction
made last month (spec §14).

The shipped defaults are a starting point, not an authority. Marketplace fee
schedules change, vary by category and size tier, and differ per account. They
are labelled with a source string that says exactly that, and the operator is
expected to calibrate them against their own settlement reports.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import FulfillmentMethod, Marketplace

DEFAULTS_SOURCE = "spreadline-default-v1 (uncalibrated: replace with your settlement data)"


class WeightTier(BaseModel):
    """A fulfilment fee band: base fee up to ``max_weight_lb``, then per-pound."""

    model_config = ConfigDict(frozen=True)

    max_weight_lb: Decimal
    base_fee: Decimal
    per_additional_lb: Decimal = Decimal("0")


class FeeAssumptions(BaseModel):
    """A complete, self-describing fee model for one marketplace."""

    model_config = ConfigDict(frozen=True)

    version: str = "v1"
    marketplace: Marketplace = Marketplace.AMAZON
    source: str = DEFAULTS_SOURCE
    currency: str = "USD"

    # -- marketplace commission -------------------------------------------
    default_referral_rate: Decimal = Decimal("0.15")
    referral_rate_by_category: dict[str, Decimal] = Field(default_factory=dict)
    min_referral_fee: Decimal = Decimal("0.30")
    #: Per-item fee on media categories (books, music, video, software).
    closing_fee: Decimal = Decimal("0")
    closing_fee_categories: tuple[str, ...] = ()
    #: Flat per-order fee charged on every sale regardless of category. eBay
    #: charges one; Amazon and Walmart do not. Modelled separately from the
    #: closing fee because it is unconditional, and folding it into the referral
    #: rate would make break-even wrong at every price except one.
    fixed_transaction_fee: Decimal = Decimal("0")

    # -- fulfilment --------------------------------------------------------
    fulfillment_tiers: tuple[WeightTier, ...] = ()
    #: Used when the product has no weight on file. Producing a fee from an
    #: unknown weight is a modelling choice, so it is explicit and flagged in the
    #: result's warnings rather than hidden.
    default_weight_lb: Decimal = Decimal("1.0")
    #: Merchant-fulfilled: what it costs the operator to ship one unit.
    merchant_shipping_per_unit: Decimal = Decimal("5.50")

    # -- holding costs -----------------------------------------------------
    storage_fee_per_cubic_foot_month: Decimal = Decimal("0.87")
    default_cubic_feet: Decimal = Decimal("0.15")
    expected_months_in_storage: Decimal = Decimal("1.5")

    # -- acquisition-side --------------------------------------------------
    inbound_shipping_per_unit: Decimal = Decimal("0.60")
    prep_cost_per_unit: Decimal = Decimal("0.35")
    acquisition_tax_rate: Decimal = Decimal("0")
    misc_cost_per_unit: Decimal = Decimal("0")

    # -- risk-adjusted costs ----------------------------------------------
    #: Share of revenue reserved for returns. A real cost of doing business that
    #: a naive spread calculation omits entirely.
    return_rate: Decimal = Decimal("0.03")
    #: Share of a returned unit's value that is unrecoverable.
    return_loss_rate: Decimal = Decimal("0.50")

    def referral_rate_for(self, category: str | None) -> Decimal:
        if category:
            key = category.strip().lower()
            if key in self.referral_rate_by_category:
                return self.referral_rate_by_category[key]
        return self.default_referral_rate

    def fulfillment_fee_for(self, weight_lb: Decimal) -> Decimal:
        if not self.fulfillment_tiers:
            return Decimal("0")
        for tier in self.fulfillment_tiers:
            if weight_lb <= tier.max_weight_lb:
                return tier.base_fee
        last = self.fulfillment_tiers[-1]
        extra = max(Decimal("0"), weight_lb - last.max_weight_lb)
        return last.base_fee + (extra * last.per_additional_lb)

    def has_closing_fee(self, category: str | None) -> bool:
        if not category or not self.closing_fee:
            return False
        return category.strip().lower() in {c.lower() for c in self.closing_fee_categories}

    def fingerprint(self) -> str:
        """Stable hash of the whole assumption set.

        Two snapshots with the same fingerprint were computed under identical
        assumptions; two with different fingerprints are not comparable without
        saying so.
        """
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return f"{self.version}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


#: Category commission rates. Illustrative defaults; calibrate before trusting.
_AMAZON_REFERRAL_RATES = {
    "electronics": Decimal("0.08"),
    "computers": Decimal("0.08"),
    "video games": Decimal("0.15"),
    "home & kitchen": Decimal("0.15"),
    "kitchen": Decimal("0.15"),
    "grocery": Decimal("0.08"),
    "health & personal care": Decimal("0.15"),
    "beauty": Decimal("0.15"),
    "toys & games": Decimal("0.15"),
    "office products": Decimal("0.15"),
    "tools & home improvement": Decimal("0.15"),
    "pet supplies": Decimal("0.15"),
    "sports & outdoors": Decimal("0.15"),
    "clothing": Decimal("0.17"),
    "books": Decimal("0.15"),
    "cell phone accessories": Decimal("0.08"),
}

_AMAZON_FBA_TIERS = (
    WeightTier(max_weight_lb=Decimal("0.25"), base_fee=Decimal("3.22")),
    WeightTier(max_weight_lb=Decimal("0.50"), base_fee=Decimal("3.63")),
    WeightTier(max_weight_lb=Decimal("0.75"), base_fee=Decimal("4.06")),
    WeightTier(max_weight_lb=Decimal("1.00"), base_fee=Decimal("4.42")),
    WeightTier(max_weight_lb=Decimal("1.50"), base_fee=Decimal("4.97")),
    WeightTier(max_weight_lb=Decimal("2.00"), base_fee=Decimal("5.42")),
    WeightTier(max_weight_lb=Decimal("3.00"), base_fee=Decimal("6.13")),
    WeightTier(
        max_weight_lb=Decimal("20.00"),
        base_fee=Decimal("7.17"),
        per_additional_lb=Decimal("0.16"),
    ),
)

_WALMART_REFERRAL_RATES = {
    "electronics": Decimal("0.08"),
    "computers": Decimal("0.06"),
    "home & kitchen": Decimal("0.15"),
    "kitchen": Decimal("0.15"),
    "grocery": Decimal("0.08"),
    "health & personal care": Decimal("0.15"),
    "beauty": Decimal("0.15"),
    "toys & games": Decimal("0.15"),
    "office products": Decimal("0.15"),
    "clothing": Decimal("0.15"),
}

_WFS_TIERS = (
    WeightTier(max_weight_lb=Decimal("1.00"), base_fee=Decimal("3.45")),
    WeightTier(max_weight_lb=Decimal("2.00"), base_fee=Decimal("4.95")),
    WeightTier(max_weight_lb=Decimal("3.00"), base_fee=Decimal("5.45")),
    WeightTier(
        max_weight_lb=Decimal("20.00"),
        base_fee=Decimal("6.45"),
        per_additional_lb=Decimal("0.40"),
    ),
)

AMAZON_DEFAULT_ASSUMPTIONS = FeeAssumptions(
    version="amazon-v1",
    marketplace=Marketplace.AMAZON,
    referral_rate_by_category=_AMAZON_REFERRAL_RATES,
    fulfillment_tiers=_AMAZON_FBA_TIERS,
    closing_fee=Decimal("1.80"),
    closing_fee_categories=("books", "music", "video", "dvd", "software"),
)

WALMART_DEFAULT_ASSUMPTIONS = FeeAssumptions(
    version="walmart-v1",
    marketplace=Marketplace.WALMART,
    referral_rate_by_category=_WALMART_REFERRAL_RATES,
    fulfillment_tiers=_WFS_TIERS,
    storage_fee_per_cubic_foot_month=Decimal("0.75"),
)

#: eBay. The seller ships, so there is no fulfilment tier table and no storage:
#: the unit sits in the operator's own space, which the misc cost can carry.
EBAY_DEFAULT_ASSUMPTIONS = FeeAssumptions(
    version="ebay-v1",
    marketplace=Marketplace.EBAY,
    default_referral_rate=Decimal("0.1325"),
    referral_rate_by_category={
        "electronics": Decimal("0.1325"),
        "computers": Decimal("0.1325"),
        "cell phones & accessories": Decimal("0.1325"),
        "clothing": Decimal("0.15"),
        "jewelry": Decimal("0.15"),
        "books": Decimal("0.1495"),
        "musical instruments": Decimal("0.0635"),
        "video games": Decimal("0.1325"),
        "home & kitchen": Decimal("0.1325"),
        "toys & games": Decimal("0.1325"),
    },
    min_referral_fee=Decimal("0"),
    fixed_transaction_fee=Decimal("0.40"),
    fulfillment_tiers=(),
    merchant_shipping_per_unit=Decimal("6.50"),
    storage_fee_per_cubic_foot_month=Decimal("0"),
    expected_months_in_storage=Decimal("0"),
    inbound_shipping_per_unit=Decimal("0"),
    prep_cost_per_unit=Decimal("0.50"),
    return_rate=Decimal("0.04"),
    return_loss_rate=Decimal("0.50"),
)

#: Best Buy is a retail source, not a place the operator sells. The schedule
#: exists so the marketplace resolves, and carries no selling fees.
BESTBUY_DEFAULT_ASSUMPTIONS = FeeAssumptions(
    version="bestbuy-v1",
    marketplace=Marketplace.BESTBUY,
    default_referral_rate=Decimal("0"),
    min_referral_fee=Decimal("0"),
    fulfillment_tiers=(),
    merchant_shipping_per_unit=Decimal("0"),
    storage_fee_per_cubic_foot_month=Decimal("0"),
    expected_months_in_storage=Decimal("0"),
    inbound_shipping_per_unit=Decimal("0"),
    prep_cost_per_unit=Decimal("0"),
    return_rate=Decimal("0"),
)

MOCK_DEFAULT_ASSUMPTIONS = AMAZON_DEFAULT_ASSUMPTIONS.model_copy(
    update={"version": "mock-v1", "marketplace": Marketplace.MOCK}
)

_DEFAULTS: dict[Marketplace, FeeAssumptions] = {
    Marketplace.AMAZON: AMAZON_DEFAULT_ASSUMPTIONS,
    Marketplace.WALMART: WALMART_DEFAULT_ASSUMPTIONS,
    Marketplace.EBAY: EBAY_DEFAULT_ASSUMPTIONS,
    Marketplace.BESTBUY: BESTBUY_DEFAULT_ASSUMPTIONS,
    Marketplace.MOCK: MOCK_DEFAULT_ASSUMPTIONS,
}


def default_assumptions(marketplace: Marketplace | str) -> FeeAssumptions:
    return _DEFAULTS[Marketplace(marketplace)]


def assumptions_for(
    marketplace: Marketplace | str, overrides: dict[str, object] | None = None
) -> FeeAssumptions:
    """Org- or user-level overrides layered onto the marketplace defaults."""
    base = default_assumptions(marketplace)
    if not overrides:
        return base
    known = {key: value for key, value in overrides.items() if key in FeeAssumptions.model_fields}
    if not known:
        return base
    return base.model_copy(update=known)


def fulfillment_default_for(marketplace: Marketplace | str) -> FulfillmentMethod:
    return {
        Marketplace.AMAZON: FulfillmentMethod.FBA,
        Marketplace.WALMART: FulfillmentMethod.WFS,
        # eBay and a retail source are both merchant-shipped.
        Marketplace.EBAY: FulfillmentMethod.SELLER,
        Marketplace.BESTBUY: FulfillmentMethod.SELLER,
        Marketplace.MOCK: FulfillmentMethod.FBA,
    }[Marketplace(marketplace)]
