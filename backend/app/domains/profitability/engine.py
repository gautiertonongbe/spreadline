"""The profitability engine.

Deterministic, Decimal-only, and the single authority on what a unit earns. No
model, heuristic or provider ever produces a financial figure; they produce
inputs, and this module produces the money.

    selling price
      - marketplace referral fee
      - closing fee
      - fulfilment
      - storage
      - return allowance
      - acquisition cost
      - acquisition shipping
      - inbound shipping and prep
      - tax
      - miscellaneous
      = net profit

Every result carries its line items, the assumptions fingerprint and the
warnings that describe which inputs were assumed rather than observed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.money import ZERO, apply_pct, money, pct_of, ratio, to_decimal
from app.domains.profitability.assumptions import (
    FeeAssumptions,
    assumptions_for,
    fulfillment_default_for,
)
from app.models.enums import FulfillmentMethod, Marketplace


@dataclass(frozen=True)
class LineItem:
    code: str
    label: str
    amount: Decimal
    #: +1 adds to the result, -1 subtracts. Kept explicit so the UI never has to
    #: infer a sign from a name.
    sign: int
    basis: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "amount": str(self.amount),
            "sign": self.sign,
            "basis": self.basis,
        }


@dataclass
class ProfitabilityInput:
    """Everything needed to price one unit.

    Quantities are deliberately absent: the engine works per unit, and position
    sizing is the capital allocator's job. Mixing them is how a per-unit fee ends
    up multiplied twice.
    """

    sale_price: Decimal
    acquisition_cost: Decimal
    target_marketplace: Marketplace | str = Marketplace.AMAZON
    acquisition_shipping: Decimal = ZERO
    category: str | None = None
    weight_lb: Decimal | None = None
    cubic_feet: Decimal | None = None
    fulfillment: FulfillmentMethod | str | None = None
    months_in_storage: Decimal | None = None
    #: Per-unit extras the operator knows about and the provider does not.
    misc_cost: Decimal = ZERO
    #: Set when the acquisition is taxable and not resale-exempt.
    acquisition_tax_rate: Decimal | None = None

    def normalized_marketplace(self) -> Marketplace:
        return Marketplace(self.target_marketplace)


@dataclass
class ProfitabilityResult:
    sale_price: Decimal
    acquisition_cost: Decimal
    acquisition_shipping: Decimal
    referral_fee: Decimal
    closing_fee: Decimal
    fixed_fee: Decimal
    fulfillment_fee: Decimal
    storage_fee: Decimal
    inbound_shipping: Decimal
    return_allowance: Decimal
    tax: Decimal
    misc_cost: Decimal
    total_fees: Decimal
    total_cost: Decimal
    net_profit: Decimal
    #: None, not zero, when the denominator is zero. An unknown ROI and a 0% ROI
    #: lead to different decisions.
    roi: Decimal | None
    margin: Decimal | None
    breakeven_sale_price: Decimal | None
    max_acquisition_cost: Decimal | None
    line_items: list[LineItem]
    assumptions: FeeAssumptions
    assumptions_version: str
    fulfillment: FulfillmentMethod
    #: Inputs that were assumed rather than observed. Surfaced in the UI and fed
    #: to the data-quality score.
    warnings: list[str] = field(default_factory=list)

    @property
    def is_profitable(self) -> bool:
        return self.net_profit > 0

    @property
    def spread(self) -> Decimal:
        """Raw price difference before any cost. Never mistaken for profit."""
        return money(self.sale_price - (self.acquisition_cost + self.acquisition_shipping))

    def as_dict(self) -> dict[str, Any]:
        return {
            "sale_price": str(self.sale_price),
            "acquisition_cost": str(self.acquisition_cost),
            "acquisition_shipping": str(self.acquisition_shipping),
            "referral_fee": str(self.referral_fee),
            "closing_fee": str(self.closing_fee),
            "fixed_fee": str(self.fixed_fee),
            "fulfillment_fee": str(self.fulfillment_fee),
            "storage_fee": str(self.storage_fee),
            "inbound_shipping": str(self.inbound_shipping),
            "return_allowance": str(self.return_allowance),
            "tax": str(self.tax),
            "misc_cost": str(self.misc_cost),
            "total_fees": str(self.total_fees),
            "total_cost": str(self.total_cost),
            "net_profit": str(self.net_profit),
            "spread": str(self.spread),
            "roi": None if self.roi is None else str(self.roi),
            "margin": None if self.margin is None else str(self.margin),
            "breakeven_sale_price": (
                None if self.breakeven_sale_price is None else str(self.breakeven_sale_price)
            ),
            "max_acquisition_cost": (
                None if self.max_acquisition_cost is None else str(self.max_acquisition_cost)
            ),
            "fulfillment": self.fulfillment.value,
            "line_items": [item.as_dict() for item in self.line_items],
            "assumptions_version": self.assumptions_version,
            "warnings": self.warnings,
        }


def calculate_profitability(
    data: ProfitabilityInput,
    assumptions: FeeAssumptions | None = None,
    *,
    overrides: dict[str, object] | None = None,
) -> ProfitabilityResult:
    """Price one unit under one explicit set of assumptions."""
    marketplace = data.normalized_marketplace()
    fees = assumptions or assumptions_for(marketplace, overrides)
    warnings: list[str] = []

    sale_price = money(data.sale_price)
    acquisition_cost = money(data.acquisition_cost)
    acquisition_shipping = money(data.acquisition_shipping)

    if sale_price <= 0:
        warnings.append("Sale price is zero or negative; all revenue-based fees are zero.")

    fulfillment = FulfillmentMethod(data.fulfillment or fulfillment_default_for(marketplace))

    # -- marketplace commission -------------------------------------------
    referral_rate = fees.referral_rate_for(data.category)
    if not data.category:
        warnings.append(
            f"No category on file; applied the default referral rate of {referral_rate:.1%}."
        )
    referral_fee = max(
        apply_pct(sale_price, referral_rate),
        fees.min_referral_fee if sale_price > 0 else ZERO,
    )
    closing_fee = fees.closing_fee if fees.has_closing_fee(data.category) else ZERO
    fixed_fee = money(fees.fixed_transaction_fee) if sale_price > 0 else ZERO

    # -- fulfilment --------------------------------------------------------
    weight = to_decimal(data.weight_lb) if data.weight_lb is not None else None
    if weight is None:
        weight = fees.default_weight_lb
        warnings.append(f"No unit weight on file; assumed {weight} lb for the fulfilment fee.")
    if fulfillment in {FulfillmentMethod.FBA, FulfillmentMethod.WFS}:
        fulfillment_fee = money(fees.fulfillment_fee_for(weight))
        if not fees.fulfillment_tiers:
            warnings.append("No fulfilment fee tiers configured; fulfilment charged at zero.")
    else:
        fulfillment_fee = money(fees.merchant_shipping_per_unit)

    # -- storage -----------------------------------------------------------
    cubic_feet = to_decimal(data.cubic_feet) if data.cubic_feet is not None else None
    if cubic_feet is None:
        cubic_feet = fees.default_cubic_feet
        warnings.append(f"No dimensions on file; assumed {cubic_feet} cubic feet for storage.")
    months = (
        to_decimal(data.months_in_storage)
        if data.months_in_storage is not None
        else fees.expected_months_in_storage
    )
    storage_fee = (
        money(cubic_feet * months * fees.storage_fee_per_cubic_foot_month)
        if fulfillment in {FulfillmentMethod.FBA, FulfillmentMethod.WFS}
        else ZERO
    )

    # -- acquisition side --------------------------------------------------
    inbound_shipping = money(fees.inbound_shipping_per_unit + fees.prep_cost_per_unit)
    tax_rate = (
        to_decimal(data.acquisition_tax_rate)
        if data.acquisition_tax_rate is not None
        else fees.acquisition_tax_rate
    )
    tax = apply_pct(acquisition_cost, tax_rate)
    misc_cost = money(data.misc_cost + fees.misc_cost_per_unit)

    # -- returns -----------------------------------------------------------
    # Expected cost of the share of units that come back: the lost revenue plus
    # the fulfilment already spent, times the share that cannot be resold.
    return_allowance = money(
        (sale_price + fulfillment_fee) * fees.return_rate * fees.return_loss_rate
    )

    total_fees = money(
        referral_fee
        + closing_fee
        + fixed_fee
        + fulfillment_fee
        + storage_fee
        + return_allowance
    )
    total_cost = money(
        acquisition_cost + acquisition_shipping + inbound_shipping + tax + misc_cost + total_fees
    )
    net_profit = money(sale_price - total_cost)

    #: ROI is measured against cash actually put at risk to acquire the unit, not
    #: against total cost: marketplace fees are netted out of the sale proceeds
    #: and were never capital the operator had to find.
    invested_capital = money(
        acquisition_cost + acquisition_shipping + inbound_shipping + tax + misc_cost
    )
    roi = pct_of(net_profit, invested_capital)
    margin = pct_of(net_profit, sale_price)

    line_items = [
        LineItem("sale_price", "Selling price", sale_price, 1, "observed target price"),
        LineItem(
            "referral_fee",
            "Marketplace referral fee",
            referral_fee,
            -1,
            f"{referral_rate:.1%} of selling price"
            + (
                f", floored at {fees.min_referral_fee}"
                if referral_fee == fees.min_referral_fee
                else ""
            ),
        ),
        LineItem(
            "fulfillment_fee",
            "Fulfilment",
            fulfillment_fee,
            -1,
            f"{fulfillment.value} at {weight} lb",
        ),
        LineItem(
            "storage_fee",
            "Storage",
            storage_fee,
            -1,
            f"{cubic_feet} cu ft x {months} months x "
            f"{fees.storage_fee_per_cubic_foot_month}/cu ft/mo",
        ),
        LineItem(
            "return_allowance",
            "Return allowance",
            return_allowance,
            -1,
            f"{fees.return_rate:.1%} return rate x {fees.return_loss_rate:.0%} unrecoverable",
        ),
        LineItem(
            "acquisition_cost", "Acquisition cost", acquisition_cost, -1, "observed source price"
        ),
        LineItem(
            "acquisition_shipping",
            "Acquisition shipping",
            acquisition_shipping,
            -1,
            "source shipping",
        ),
        LineItem(
            "inbound_shipping",
            "Inbound shipping and prep",
            inbound_shipping,
            -1,
            f"{fees.inbound_shipping_per_unit} inbound + {fees.prep_cost_per_unit} prep",
        ),
    ]
    if closing_fee:
        line_items.insert(
            2,
            LineItem(
                "closing_fee", "Closing fee", closing_fee, -1, f"media category: {data.category}"
            ),
        )
    if fixed_fee:
        line_items.insert(
            2, LineItem("fixed_fee", "Fixed transaction fee", fixed_fee, -1, "flat, per order")
        )
    if tax:
        line_items.append(
            LineItem("tax", "Acquisition tax", tax, -1, f"{tax_rate:.2%} of acquisition")
        )
    if misc_cost:
        line_items.append(LineItem("misc_cost", "Other costs", misc_cost, -1, "operator-entered"))

    return ProfitabilityResult(
        sale_price=sale_price,
        acquisition_cost=acquisition_cost,
        acquisition_shipping=acquisition_shipping,
        referral_fee=referral_fee,
        closing_fee=closing_fee,
        fixed_fee=fixed_fee,
        fulfillment_fee=fulfillment_fee,
        storage_fee=storage_fee,
        inbound_shipping=inbound_shipping,
        return_allowance=return_allowance,
        tax=tax,
        misc_cost=misc_cost,
        total_fees=total_fees,
        total_cost=total_cost,
        net_profit=net_profit,
        roi=roi,
        margin=margin,
        breakeven_sale_price=breakeven_sale_price(data, fees),
        max_acquisition_cost=max_acquisition_cost(data, fees, target_roi=Decimal("0")),
        line_items=line_items,
        assumptions=fees,
        assumptions_version=fees.fingerprint(),
        fulfillment=fulfillment,
        warnings=warnings,
    )


def _fixed_costs(data: ProfitabilityInput, fees: FeeAssumptions) -> tuple[Decimal, Decimal]:
    """Split costs into (fixed per unit, rate applied to sale price).

    Separating them is what makes break-even solvable in closed form instead of
    by iteration.
    """
    marketplace = data.normalized_marketplace()
    fulfillment = FulfillmentMethod(data.fulfillment or fulfillment_default_for(marketplace))
    weight = to_decimal(data.weight_lb) if data.weight_lb is not None else fees.default_weight_lb
    cubic_feet = (
        to_decimal(data.cubic_feet) if data.cubic_feet is not None else fees.default_cubic_feet
    )
    months = (
        to_decimal(data.months_in_storage)
        if data.months_in_storage is not None
        else fees.expected_months_in_storage
    )
    fulfillment_fee = (
        fees.fulfillment_fee_for(weight)
        if fulfillment in {FulfillmentMethod.FBA, FulfillmentMethod.WFS}
        else fees.merchant_shipping_per_unit
    )
    storage_fee = (
        cubic_feet * months * fees.storage_fee_per_cubic_foot_month
        if fulfillment in {FulfillmentMethod.FBA, FulfillmentMethod.WFS}
        else ZERO
    )
    tax_rate = (
        to_decimal(data.acquisition_tax_rate)
        if data.acquisition_tax_rate is not None
        else fees.acquisition_tax_rate
    )
    closing = fees.closing_fee if fees.has_closing_fee(data.category) else ZERO

    fixed = (
        to_decimal(data.acquisition_cost) * (Decimal("1") + tax_rate)
        + to_decimal(data.acquisition_shipping)
        + fees.inbound_shipping_per_unit
        + fees.prep_cost_per_unit
        + to_decimal(data.misc_cost)
        + fees.misc_cost_per_unit
        + fulfillment_fee
        + storage_fee
        + closing
        + fees.fixed_transaction_fee
        # The fulfilment component of the return allowance does not scale with
        # the sale price, so it belongs on the fixed side.
        + (fulfillment_fee * fees.return_rate * fees.return_loss_rate)
    )
    rate = fees.referral_rate_for(data.category) + (fees.return_rate * fees.return_loss_rate)
    return money(fixed), ratio(rate)


def breakeven_sale_price(data: ProfitabilityInput, fees: FeeAssumptions) -> Decimal | None:
    """The selling price at which net profit is exactly zero."""
    fixed, rate = _fixed_costs(data, fees)
    if rate >= 1:
        return None
    return money(fixed / (Decimal("1") - rate))


def max_acquisition_cost(
    data: ProfitabilityInput, fees: FeeAssumptions, *, target_roi: Decimal = Decimal("0.30")
) -> Decimal | None:
    """The most the operator can pay per unit and still hit ``target_roi``.

    The single most useful number when standing in front of a shelf or a
    supplier price list.
    """
    sale_price = to_decimal(data.sale_price)
    if sale_price <= 0:
        return None
    probe = ProfitabilityInput(
        sale_price=sale_price,
        acquisition_cost=ZERO,
        target_marketplace=data.target_marketplace,
        acquisition_shipping=data.acquisition_shipping,
        category=data.category,
        weight_lb=data.weight_lb,
        cubic_feet=data.cubic_feet,
        fulfillment=data.fulfillment,
        months_in_storage=data.months_in_storage,
        misc_cost=data.misc_cost,
        acquisition_tax_rate=data.acquisition_tax_rate,
    )
    fixed_at_zero_cost, rate = _fixed_costs(probe, fees)
    tax_rate = (
        to_decimal(data.acquisition_tax_rate)
        if data.acquisition_tax_rate is not None
        else fees.acquisition_tax_rate
    )
    net_revenue = sale_price * (Decimal("1") - rate) - fixed_at_zero_cost

    # net_revenue - C*(1+tax) = target_roi * (C*(1+tax) + other invested capital),
    # where the invested capital already counted in fixed_at_zero_cost is the
    # inbound, prep, misc and acquisition shipping.
    invested_fixed = (
        to_decimal(data.acquisition_shipping)
        + fees.inbound_shipping_per_unit
        + fees.prep_cost_per_unit
        + to_decimal(data.misc_cost)
        + fees.misc_cost_per_unit
    )
    denominator = (Decimal("1") + tax_rate) * (Decimal("1") + target_roi)
    if denominator <= 0:
        return None
    result = (net_revenue - (target_roi * invested_fixed)) / denominator
    return money(max(ZERO, result))
