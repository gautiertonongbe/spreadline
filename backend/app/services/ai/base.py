"""The AI layer: contract and guardrails.

AI is an optional intelligence layer, never the foundation of the financial
engine (spec §34). This module defines what an assistant is allowed to do and
enforces it, so that enabling one cannot quietly change how money is calculated.

**Permitted**: proposing a review of an ambiguous product match, extracting
attributes from unstructured text, classifying a product, explaining an anomaly
or a risk signal in prose, natural-language search.

**Forbidden, and structurally prevented here**: calculating fees, profit or ROI;
overriding product identity rules; inventing missing data; producing a demand
estimate that no provider supplied.

Two guarantees hold regardless of which assistant is installed:

1. Every output is wrapped in ``AIOutput``, which records the model, timestamp,
   confidence, input, output and reason. An AI claim with no provenance cannot
   enter the system.
2. A match confidence proposed by AI is capped at ``ai_max_match_confidence``.
   An assistant can raise an ambiguous match to "worth a look"; it can never
   assert the high-confidence band that an identifier match earns.

No live model integration ships in this repository. ``NullAssistant`` is the
default and declines every request with a reason, which is the honest behaviour
when nothing is configured.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.clock import iso_utc, utcnow
from app.core.config import settings
from app.core.money import ratio


class AIUsageError(RuntimeError):
    """Raised when the AI layer is asked to do something it must never do."""


@dataclass(frozen=True)
class AIOutput:
    """One recorded AI result, with everything needed to audit it later."""

    kind: str
    model: str
    timestamp: datetime
    #: 0..1. None when the assistant declined or expressed no confidence.
    confidence: Decimal | None
    #: What was asked, and what came back. Kept so a bad output is reproducible.
    input: dict[str, Any]
    output: Any
    reason: str
    accepted: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "model": self.model,
            "timestamp": iso_utc(self.timestamp),
            "confidence": None if self.confidence is None else str(self.confidence),
            "input": self.input,
            "output": self.output,
            "reason": self.reason,
            "accepted": self.accepted,
        }


@dataclass
class MatchReview:
    """An AI opinion on an ambiguous match. A proposal, never a decision."""

    same_product: bool
    confidence: Decimal
    reasoning: str
    conflicts_noticed: list[str] = field(default_factory=list)


class AIAssistant(abc.ABC):
    """The only AI surface the platform is allowed to depend on."""

    name: str = "assistant"
    model: str = "none"

    @property
    def is_enabled(self) -> bool:
        return False

    @abc.abstractmethod
    async def review_match(
        self, source: dict[str, Any], target: dict[str, Any], *, deterministic_confidence: Decimal
    ) -> AIOutput:
        """Offer a second opinion on an ambiguous product match."""

    @abc.abstractmethod
    async def extract_attributes(self, title: str, description: str | None = None) -> AIOutput:
        """Pull structured attributes out of unstructured listing text."""

    @abc.abstractmethod
    async def explain(self, kind: str, payload: dict[str, Any]) -> AIOutput:
        """Render an already-computed finding in prose. Computes nothing."""

    async def close(self) -> None:  # noqa: B027 - concrete on purpose
        """Release connections.

        Concrete rather than abstract: most assistants hold no resources, and
        requiring every one to implement an empty method adds ceremony without
        adding safety.
        """


def cap_match_confidence(value: Decimal) -> Decimal:
    """Clamp an AI-proposed match confidence to the configured ceiling.

    The ceiling exists because identity is the one decision where being
    confidently wrong loses the entire position rather than the margin. AI may
    move an ambiguous match into the band where a person looks at it; it may not
    put it in the band where the platform acts on its own.
    """
    return ratio(min(Decimal(str(settings.ai_max_match_confidence)), max(Decimal("0"), value)))


def reject_financial_use(operation: str) -> None:
    """Guard for any code path that might route money through the AI layer.

    Deliberately loud. If this ever fires in production it means someone wired a
    model into the financial engine, which is the failure this whole layer is
    shaped to prevent.
    """
    raise AIUsageError(
        f"'{operation}' is a financial calculation and must be produced by the "
        "profitability engine, not by a model. See docs/decisions.md."
    )


class NullAssistant(AIAssistant):
    """The default. Declines every request and says why.

    Not a stub that returns empty values: a decline is a different answer from a
    negative result, and the caller must be able to tell them apart.
    """

    name = "null"
    model = "none"

    @property
    def is_enabled(self) -> bool:
        return False

    def _decline(self, kind: str, payload: dict[str, Any]) -> AIOutput:
        return AIOutput(
            kind=kind,
            model=self.model,
            timestamp=utcnow(),
            confidence=None,
            input=payload,
            output=None,
            reason=(
                "No AI assistant is configured. Set AI_ENABLED and AI_PROVIDER to "
                "enable the optional intelligence layer."
            ),
            accepted=False,
        )

    async def review_match(
        self, source: dict[str, Any], target: dict[str, Any], *, deterministic_confidence: Decimal
    ) -> AIOutput:
        return self._decline("match_review", {"source": source, "target": target})

    async def extract_attributes(self, title: str, description: str | None = None) -> AIOutput:
        return self._decline("attribute_extraction", {"title": title})

    async def explain(self, kind: str, payload: dict[str, Any]) -> AIOutput:
        return self._decline(f"explain:{kind}", payload)


def build_assistant() -> AIAssistant:
    """Resolve the configured assistant.

    Returns ``NullAssistant`` unless AI is both enabled and configured. A missing
    API key never falls back to a live model and never silently pretends to be
    one; it falls back to declining.
    """
    if not settings.ai_enabled or settings.ai_provider == "null" or not settings.ai_api_key:
        return NullAssistant()
    # A live assistant implements AIAssistant and is registered here. None ships
    # in this repository: an unimplemented integration that returns plausible
    # prose would be indistinguishable from a working one, and its output would
    # be acted on.
    return NullAssistant()
