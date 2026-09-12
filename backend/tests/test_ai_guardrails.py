"""The AI layer's guardrails.

These tests exist because the failure they prevent is silent. A model wired into
the financial engine, or allowed to assert a high-confidence product match, would
produce output that looks exactly like correct output.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.config import settings
from app.services.ai.base import (
    AIUsageError,
    NullAssistant,
    build_assistant,
    cap_match_confidence,
    reject_financial_use,
)


class TestDefaults:
    def test_default_assistant_is_disabled(self):
        assistant = build_assistant()
        assert isinstance(assistant, NullAssistant)
        assert assistant.is_enabled is False

    async def test_declining_is_distinguishable_from_a_negative_result(self):
        """A decline and a "no" must not look the same to the caller."""
        output = await NullAssistant().review_match(
            {"title": "a"}, {"title": "b"}, deterministic_confidence=Decimal("0.6")
        )
        assert output.accepted is False
        assert output.output is None
        assert output.confidence is None
        assert "No AI assistant is configured" in output.reason

    async def test_every_output_carries_its_provenance(self):
        output = await NullAssistant().extract_attributes("Sony WH-1000XM5")
        payload = output.as_dict()
        for field in ("kind", "model", "timestamp", "confidence", "input", "output", "reason"):
            assert field in payload

    def test_missing_api_key_never_yields_a_live_assistant(self, monkeypatch):
        monkeypatch.setattr(settings, "ai_enabled", True)
        monkeypatch.setattr(settings, "ai_provider", "anthropic")
        monkeypatch.setattr(settings, "ai_api_key", None)
        assert isinstance(build_assistant(), NullAssistant)


class TestConfidenceCeiling:
    def test_ai_cannot_assert_the_high_confidence_band(self):
        """Identity is where being confidently wrong loses the whole position."""
        assert cap_match_confidence(Decimal("0.99")) <= Decimal(
            str(settings.ai_max_match_confidence)
        )

    def test_a_modest_proposal_passes_through(self):
        assert cap_match_confidence(Decimal("0.55")) == Decimal("0.55")

    def test_negative_confidence_is_clamped_to_zero(self):
        assert cap_match_confidence(Decimal("-1")) == Decimal("0")

    def test_the_cap_is_below_the_confirmed_threshold(self):
        """The ceiling has to sit under the matcher's confirmed band, or it does
        nothing."""
        from app.domains.identity.matcher import DEFAULT_POLICY

        assert Decimal(str(settings.ai_max_match_confidence)) < DEFAULT_POLICY.confirmed_threshold


class TestFinancialGuard:
    @pytest.mark.parametrize(
        "operation", ["net_profit", "referral_fee", "roi", "breakeven_sale_price"]
    )
    def test_financial_operations_are_refused_loudly(self, operation):
        with pytest.raises(AIUsageError) as caught:
            reject_financial_use(operation)
        assert "profitability engine" in str(caught.value)
