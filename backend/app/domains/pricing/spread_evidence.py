"""Is this spread a feature of the market, or is it today's accident?

A spread is a difference between two prices at one instant. Whether it is worth
buying into depends on something the instant cannot tell you: whether it was
there last week and will be there when the inventory arrives.

This module answers that from the price history of both sides, and only from
it. Four verdicts, and the distinction between them is the whole point:

``structural``
    The gap has been there. Today's spread is in line with the gap between the
    two medians, and neither side is especially volatile. Buying it is a bet
    that the market keeps doing what it has been doing.

``temporary``
    The gap exists now and did not historically, or today's is far wider than
    the typical one. Something moved recently. It may still be worth buying,
    but it is a bet on a dislocation persisting long enough to sell into, which
    is a different bet with a shorter clock.

``unstable``
    The gap exists on average but the underlying prices swing enough that the
    spread on the day of sale is close to a coin toss.

``unsupported``
    There is not enough history on one or both sides to say anything. This is
    not a mild version of the others. It is the absence of an answer, and it is
    reported as such rather than being folded into "structural" by default.

Nothing here estimates, infers or fills in. Where a window is missing the
verdict is ``unsupported`` and the confidence is ``none``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.core.money import ZERO, display_currency, money, ratio
from app.domains.pricing.statistics import PriceHistoryAnalysis, WindowStats
from app.models.enums import Confidence

SPREAD_EVIDENCE_VERSION = "spread-evidence-v1"

#: Coefficient of variation at or above which a side is treated as unstable.
UNSTABLE_VOLATILITY = Decimal("0.20")
#: A peak-to-trough fall at or above this makes a side unstable on its own.
UNSTABLE_DRAWDOWN = Decimal("0.30")
#: Today's spread this many times the typical one is a dislocation, not a level.
TEMPORARY_RATIO = Decimal("1.50")
#: Below this many observations on a side, confidence is capped at low.
THIN_EVIDENCE_OBSERVATIONS = 20


class SpreadVerdict(StrEnum):
    STRUCTURAL = "structural"
    TEMPORARY = "temporary"
    UNSTABLE = "unstable"
    UNSUPPORTED = "unsupported"


@dataclass
class SideEvidence:
    """What the history of one side of the trade supports."""

    label: str
    median: Decimal | None
    volatility: Decimal | None
    max_drawdown: Decimal | None
    window_days: int | None
    observation_count: int
    confidence: Confidence

    @property
    def is_usable(self) -> bool:
        return self.median is not None and self.window_days is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "median": None if self.median is None else str(self.median),
            "volatility": None if self.volatility is None else str(self.volatility),
            "max_drawdown": None if self.max_drawdown is None else str(self.max_drawdown),
            "window_days": self.window_days,
            "observation_count": self.observation_count,
            "confidence": self.confidence.value,
            "usable": self.is_usable,
        }


@dataclass
class SpreadEvidence:
    verdict: SpreadVerdict
    confidence: Confidence
    summary: str
    current_spread: Decimal
    #: The gap between the two medians. ``None`` when either side lacks a window.
    typical_spread: Decimal | None
    #: Current divided by typical. ``None`` when typical is missing or not positive.
    spread_ratio: Decimal | None
    source: SideEvidence
    target: SideEvidence
    reasons: list[str] = field(default_factory=list)
    version: str = SPREAD_EVIDENCE_VERSION

    @property
    def is_supported(self) -> bool:
        return self.verdict is not SpreadVerdict.UNSUPPORTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "confidence": self.confidence.value,
            "summary": self.summary,
            "current_spread": str(self.current_spread),
            "typical_spread": None if self.typical_spread is None else str(self.typical_spread),
            "spread_ratio": None if self.spread_ratio is None else str(self.spread_ratio),
            "source": self.source.as_dict(),
            "target": self.target.as_dict(),
            "reasons": self.reasons,
            "version": self.version,
        }


def _side(label: str, analysis: PriceHistoryAnalysis) -> SideEvidence:
    reference: WindowStats | None = analysis.reference
    return SideEvidence(
        label=label,
        median=reference.median if reference else None,
        volatility=reference.volatility if reference else None,
        max_drawdown=reference.max_drawdown if reference else None,
        window_days=reference.window_days if reference else None,
        observation_count=analysis.observation_count,
        confidence=reference.confidence if reference else Confidence.NONE,
    )


def _confidence(source: SideEvidence, target: SideEvidence) -> Confidence:
    """The weaker of the two sides, capped by how thin the evidence is.

    A conclusion about a spread cannot be more confident than the weaker of the
    two prices it is drawn from.
    """
    order = [Confidence.NONE, Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]
    weakest = min(source.confidence, target.confidence, key=order.index)
    thinnest = min(source.observation_count, target.observation_count)
    if thinnest < THIN_EVIDENCE_OBSERVATIONS and order.index(weakest) > order.index(Confidence.LOW):
        return Confidence.LOW
    return weakest


def assess_spread(
    *,
    current_spread: Decimal,
    source_prices: PriceHistoryAnalysis,
    target_prices: PriceHistoryAnalysis,
) -> SpreadEvidence:
    """Classify the spread against the history of both sides."""
    source = _side("source", source_prices)
    target = _side("target", target_prices)
    current_spread = money(current_spread)

    if not source.is_usable or not target.is_usable:
        missing = [side for side in (source, target) if not side.is_usable]
        return SpreadEvidence(
            verdict=SpreadVerdict.UNSUPPORTED,
            confidence=Confidence.NONE,
            summary=(
                "There is not enough price history to say whether this spread is "
                "normal or a one-off."
            ),
            current_spread=current_spread,
            typical_spread=None,
            spread_ratio=None,
            source=source,
            target=target,
            reasons=[
                f"No usable price window on the {side.label} side "
                f"({side.observation_count} observation(s))."
                for side in missing
            ],
        )

    # Both medians are present: ``is_usable`` above is exactly that check.
    typical_spread = money((target.median or ZERO) - (source.median or ZERO))
    reasons = [
        f"Typically it sells for {display_currency(target.median)} and costs "
        f"{display_currency(source.median)}, a usual gap of "
        f"{display_currency(typical_spread)}.",
        f"Right now the gap is {display_currency(current_spread)}.",
        f"Measured over {target.window_days} days on the selling side "
        f"({target.observation_count} observations) and {source.window_days} days on the "
        f"buying side ({source.observation_count} observations).",
    ]
    confidence = _confidence(source, target)

    unstable_sides = [
        side
        for side in (source, target)
        if (side.volatility is not None and side.volatility >= UNSTABLE_VOLATILITY)
        or (side.max_drawdown is not None and side.max_drawdown >= UNSTABLE_DRAWDOWN)
    ]

    spread_ratio = (
        ratio(current_spread / typical_spread) if typical_spread > 0 else None
    )

    # Instability is checked first, and deliberately outranks everything below
    # it. The other verdicts reason from the medians, and a price that swings
    # this much makes its own median a poor description of it: saying the gap is
    # "usually $30" is misleading when the daily gap ranges from -$30 to $90.
    if unstable_sides:
        for side in unstable_sides:
            if side.volatility is not None and side.volatility >= UNSTABLE_VOLATILITY:
                reasons.append(
                    f"The {side.label} price varies by {side.volatility:.0%} around its own "
                    f"average, at or above the {UNSTABLE_VOLATILITY:.0%} mark where the gap "
                    "on any given day stops being predictable."
                )
            if side.max_drawdown is not None and side.max_drawdown >= UNSTABLE_DRAWDOWN:
                reasons.append(
                    f"The {side.label} price has fallen {side.max_drawdown:.0%} peak to "
                    "trough within the window."
                )
        return SpreadEvidence(
            verdict=SpreadVerdict.UNSTABLE,
            confidence=confidence,
            summary=(
                f"Unstable. The gap averages {display_currency(typical_spread)}, but the "
                "prices behind it move enough that what you get on the day of sale is a "
                "wide range."
            ),
            current_spread=current_spread,
            typical_spread=typical_spread,
            spread_ratio=spread_ratio,
            source=source,
            target=target,
            reasons=reasons,
        )

    if typical_spread <= 0:
        reasons.append(
            "Historically it has not sold for more than it costs, so the gap is not a "
            "standing feature of these two markets."
        )
        return SpreadEvidence(
            verdict=SpreadVerdict.TEMPORARY,
            confidence=confidence,
            summary=(
                f"Temporary. The usual gap is {display_currency(typical_spread)}: today's "
                f"{display_currency(current_spread)} is a recent move, not the normal state."
            ),
            current_spread=current_spread,
            typical_spread=typical_spread,
            spread_ratio=None,
            source=source,
            target=target,
            reasons=reasons,
        )

    if spread_ratio is not None and spread_ratio >= TEMPORARY_RATIO:
        reasons.append(
            f"Today's gap is {spread_ratio:.1f} times the usual one, at or above the "
            f"{TEMPORARY_RATIO:.1f}x mark where it is better read as a dislocation than "
            "as the standing level."
        )
        return SpreadEvidence(
            verdict=SpreadVerdict.TEMPORARY,
            confidence=confidence,
            summary=(
                f"Temporary. The gap is usually {display_currency(typical_spread)} and is "
                f"{display_currency(current_spread)} today, {spread_ratio:.1f} times wider "
                "than normal."
            ),
            current_spread=current_spread,
            typical_spread=typical_spread,
            spread_ratio=spread_ratio,
            source=source,
            target=target,
            reasons=reasons,
        )

    reasons.append(
        f"Today's gap is {spread_ratio:.1f} times the usual one, which is in line with "
        "how these two markets have priced this item."
    )
    return SpreadEvidence(
        verdict=SpreadVerdict.STRUCTURAL,
        confidence=confidence,
        summary=(
            f"Structural. The gap has held around {display_currency(typical_spread)} across "
            f"{target.window_days} days, and today's {display_currency(current_spread)} is "
            "in line with it."
        ),
        current_spread=current_spread,
        typical_spread=typical_spread,
        spread_ratio=spread_ratio,
        source=source,
        target=target,
        reasons=reasons,
    )
