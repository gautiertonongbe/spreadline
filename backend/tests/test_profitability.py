"""Profitability: the deterministic core.

If anything in this file is wrong, every number the platform shows is wrong, so
the tests assert exact Decimal values rather than approximate ones and check the
algebraic identities that must hold between profit, break-even and maximum bid.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.money import cents, money, pct_of, to_decimal
from app.domains.profitability.assumptions import (
    AMAZON_DEFAULT_ASSUMPTIONS,
    FeeAssumptions,
    assumptions_for,
    default_assumptions,
)
from app.domains.profitability.engine import (
    ProfitabilityInput,
    breakeven_sale_price,
    calculate_profitability,
    max_acquisition_cost,
)
from app.models.enums import FulfillmentMethod, Marketplace


def base_input(**overrides) -> ProfitabilityInput:
    defaults = {
        "sale_price": Decimal("49.99"),
        "acquisition_cost": Decimal("22.00"),
        "category": "electronics",
        "weight_lb": Decimal("1.0"),
        "cubic_feet": Decimal("0.2"),
    }
    defaults.update(overrides)
    return ProfitabilityInput(**defaults)


class TestMoneyPrimitives:
    def test_float_never_leaks_binary_error(self):
        assert to_decimal(0.1) == Decimal("0.1")

    def test_money_rounds_half_up(self):
        assert money("1.00005") == Decimal("1.0001")

    def test_ratio_of_zero_base_is_none_not_zero(self):
        """An undefined ROI and a 0% ROI lead to different decisions."""
        assert pct_of(Decimal("5"), Decimal("0")) is None
        assert pct_of(Decimal("5"), Decimal("10")) == Decimal("0.5")

    def test_cents_presentation(self):
        assert cents(Decimal("12.3456")) == Decimal("12.35")


class TestProfitability:
    def test_line_items_sum_to_net_profit(self):
        """The displayed breakdown must reconcile to the headline figure."""
        result = calculate_profitability(base_input())
        total = sum(item.amount * item.sign for item in result.line_items)
        assert money(total) == result.net_profit

    def test_roi_uses_invested_capital_not_total_cost(self):
        result = calculate_profitability(base_input())
        invested = (
            result.acquisition_cost
            + result.acquisition_shipping
            + result.inbound_shipping
            + result.tax
            + result.misc_cost
        )
        assert result.roi == pct_of(result.net_profit, invested)

    def test_margin_is_against_sale_price(self):
        result = calculate_profitability(base_input())
        assert result.margin == pct_of(result.net_profit, result.sale_price)

    def test_spread_is_not_profit(self):
        """The distinction the whole platform exists to make."""
        result = calculate_profitability(base_input())
        assert result.spread > result.net_profit
        assert result.spread == money(Decimal("49.99") - Decimal("22.00"))

    def test_breakeven_produces_exactly_zero_profit(self):
        data = base_input()
        breakeven = breakeven_sale_price(data, AMAZON_DEFAULT_ASSUMPTIONS)
        assert breakeven is not None
        recomputed = calculate_profitability(
            base_input(sale_price=breakeven), AMAZON_DEFAULT_ASSUMPTIONS
        )
        assert abs(recomputed.net_profit) <= Decimal("0.01")

    @pytest.mark.parametrize("target_roi", ["0", "0.15", "0.30", "0.50"])
    def test_max_acquisition_cost_hits_its_target_roi(self, target_roi):
        data = base_input()
        ceiling = max_acquisition_cost(
            data, AMAZON_DEFAULT_ASSUMPTIONS, target_roi=Decimal(target_roi)
        )
        assert ceiling is not None
        recomputed = calculate_profitability(
            base_input(acquisition_cost=ceiling), AMAZON_DEFAULT_ASSUMPTIONS
        )
        assert recomputed.roi is not None
        assert abs(recomputed.roi - Decimal(target_roi)) <= Decimal("0.001")

    def test_heavier_units_cost_more_to_fulfil(self):
        light = calculate_profitability(base_input(weight_lb=Decimal("0.5")))
        heavy = calculate_profitability(base_input(weight_lb=Decimal("15.0")))
        assert heavy.fulfillment_fee > light.fulfillment_fee
        assert heavy.net_profit < light.net_profit

    def test_category_changes_the_referral_rate(self):
        electronics = calculate_profitability(base_input(category="electronics"))
        clothing = calculate_profitability(base_input(category="clothing"))
        assert clothing.referral_fee > electronics.referral_fee

    def test_missing_category_warns_and_uses_the_default_rate(self):
        result = calculate_profitability(base_input(category=None))
        assert any("category" in warning for warning in result.warnings)
        assert result.referral_fee == money(
            Decimal("49.99") * AMAZON_DEFAULT_ASSUMPTIONS.default_referral_rate
        )

    def test_missing_weight_is_assumed_and_disclosed(self):
        result = calculate_profitability(base_input(weight_lb=None))
        assert any("weight" in warning for warning in result.warnings)

    def test_merchant_fulfilment_charges_shipping_not_fba(self):
        fba = calculate_profitability(base_input(fulfillment=FulfillmentMethod.FBA))
        fbm = calculate_profitability(base_input(fulfillment=FulfillmentMethod.FBM))
        assert fbm.storage_fee == Decimal("0")
        assert fbm.fulfillment_fee == money(AMAZON_DEFAULT_ASSUMPTIONS.merchant_shipping_per_unit)
        assert fba.storage_fee > 0

    def test_return_allowance_scales_with_both_price_and_fulfilment(self):
        result = calculate_profitability(base_input())
        expected = money(
            (result.sale_price + result.fulfillment_fee)
            * AMAZON_DEFAULT_ASSUMPTIONS.return_rate
            * AMAZON_DEFAULT_ASSUMPTIONS.return_loss_rate
        )
        assert result.return_allowance == expected

    def test_loss_is_reported_as_a_loss(self):
        result = calculate_profitability(base_input(acquisition_cost=Decimal("48.00")))
        assert result.net_profit < 0
        assert result.is_profitable is False

    def test_media_category_adds_a_closing_fee(self):
        books = calculate_profitability(base_input(category="books"))
        assert books.closing_fee == AMAZON_DEFAULT_ASSUMPTIONS.closing_fee

    def test_tax_is_applied_to_acquisition_only(self):
        taxed = calculate_profitability(base_input(acquisition_tax_rate=Decimal("0.08")))
        assert taxed.tax == money(Decimal("22.00") * Decimal("0.08"))

    def test_walmart_uses_its_own_fee_schedule(self):
        amazon = calculate_profitability(base_input(target_marketplace=Marketplace.AMAZON))
        walmart = calculate_profitability(base_input(target_marketplace=Marketplace.WALMART))
        assert amazon.fulfillment_fee != walmart.fulfillment_fee

    def test_determinism(self):
        first = calculate_profitability(base_input())
        second = calculate_profitability(base_input())
        assert first.as_dict() == second.as_dict()


class TestAssumptions:
    def test_fingerprint_changes_with_any_fee(self):
        original = default_assumptions(Marketplace.AMAZON)
        changed = original.model_copy(update={"default_referral_rate": Decimal("0.20")})
        assert original.fingerprint() != changed.fingerprint()

    def test_fingerprint_is_stable_across_copies(self):
        original = default_assumptions(Marketplace.AMAZON)
        assert original.fingerprint() == original.model_copy().fingerprint()

    def test_overrides_layer_onto_defaults(self):
        custom = assumptions_for(Marketplace.AMAZON, {"return_rate": Decimal("0.10")})
        assert custom.return_rate == Decimal("0.10")
        assert custom.default_referral_rate == AMAZON_DEFAULT_ASSUMPTIONS.default_referral_rate

    def test_unknown_override_keys_are_ignored(self):
        custom = assumptions_for(Marketplace.AMAZON, {"not_a_fee": 1})
        assert custom.fingerprint() == AMAZON_DEFAULT_ASSUMPTIONS.fingerprint()

    def test_weight_tier_boundaries(self):
        fees = AMAZON_DEFAULT_ASSUMPTIONS
        assert fees.fulfillment_fee_for(Decimal("0.25")) < fees.fulfillment_fee_for(Decimal("0.26"))

    def test_beyond_the_top_tier_charges_per_pound(self):
        fees = AMAZON_DEFAULT_ASSUMPTIONS
        at_top = fees.fulfillment_fee_for(Decimal("20"))
        beyond = fees.fulfillment_fee_for(Decimal("30"))
        assert beyond == at_top + Decimal("10") * fees.fulfillment_tiers[-1].per_additional_lb

    def test_no_tiers_configured_charges_nothing_and_warns(self):
        bare = FeeAssumptions(version="bare", fulfillment_tiers=())
        result = calculate_profitability(base_input(), bare)
        assert result.fulfillment_fee == Decimal("0")
        assert any("fulfilment fee tiers" in warning for warning in result.warnings)
