"""Whether a spread is a feature of the market or today's accident.

The four verdicts exist to keep three different situations from being reported
as the same thing: a gap that has held for months, a gap that appeared this
week, and a gap nobody can say anything about because there is no history. The
tests here are mostly about the boundaries between them, and about the last one
never being quietly folded into the first.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.core.clock import utcnow
from app.domains.pricing.spread_evidence import (
    TEMPORARY_RATIO,
    UNSTABLE_VOLATILITY,
    SpreadVerdict,
    assess_spread,
)
from app.domains.pricing.statistics import PricePoint, analyze_prices
from app.models.enums import Confidence

NOW = utcnow()


def series(prices: list[float], *, days_apart: int = 1):
    """One observation per day, ending now."""
    return [
        PricePoint(
            price=Decimal(str(price)),
            observed_at=NOW - timedelta(days=(len(prices) - 1 - index) * days_apart),
        )
        for index, price in enumerate(prices)
    ]


def steady(level: float, days: int = 120, wobble: float = 0.5):
    """A price that holds a level with a small, regular wobble."""
    return series([level + (index % 3) * wobble for index in range(days)])


def assess(source_prices, target_prices, current_spread):
    return assess_spread(
        current_spread=Decimal(str(current_spread)),
        source_prices=analyze_prices(source_prices),
        target_prices=analyze_prices(target_prices),
    )


class TestUnsupported:
    def test_no_history_is_not_a_mild_form_of_stable(self):
        """The absence of an answer is reported as the absence of an answer.

        Treating a spread nobody can check as "structural" is the single most
        expensive mistake this module could make, because it would put the
        confident label on exactly the cases with no evidence behind them.
        """
        evidence = assess([], [], current_spread=30)
        assert evidence.verdict is SpreadVerdict.UNSUPPORTED
        assert evidence.confidence is Confidence.NONE
        assert evidence.typical_spread is None
        assert evidence.spread_ratio is None
        assert not evidence.is_supported

    def test_one_bare_side_is_enough_to_withhold_a_verdict(self):
        """A spread needs both prices; knowing one of them is not half an answer."""
        evidence = assess(steady(100), [], current_spread=30)
        assert evidence.verdict is SpreadVerdict.UNSUPPORTED
        assert any("target" in reason for reason in evidence.reasons)

    def test_the_missing_side_is_named(self):
        evidence = assess([], steady(130), current_spread=30)
        assert any("source" in reason for reason in evidence.reasons)


class TestStructural:
    def test_a_gap_that_has_held_is_structural(self):
        evidence = assess(steady(100), steady(130), current_spread=30)
        assert evidence.verdict is SpreadVerdict.STRUCTURAL
        assert evidence.typical_spread == Decimal("30.0000")
        assert evidence.spread_ratio is not None
        assert evidence.confidence in {Confidence.MEDIUM, Confidence.HIGH}

    def test_a_narrower_gap_than_usual_is_still_structural(self):
        """Today being worse than typical is conservative, not a warning.

        The verdict is about whether the gap is a feature of the market, and a
        smaller-than-usual gap is still that market behaving normally.
        """
        evidence = assess(steady(100), steady(130), current_spread=20)
        assert evidence.verdict is SpreadVerdict.STRUCTURAL

    def test_the_numbers_behind_the_verdict_are_published(self):
        evidence = assess(steady(100), steady(130), current_spread=30)
        assert evidence.source.median is not None
        assert evidence.target.median is not None
        assert evidence.source.window_days and evidence.target.window_days
        assert evidence.source.observation_count > 0
        assert len(evidence.reasons) >= 3


class TestTemporary:
    def test_a_gap_that_did_not_exist_historically_is_temporary(self):
        """It sells for less than it costs, on the medians. Today is the anomaly."""
        evidence = assess(steady(120), steady(100), current_spread=25)
        assert evidence.verdict is SpreadVerdict.TEMPORARY
        assert evidence.typical_spread is not None
        assert evidence.typical_spread < 0

    def test_a_gap_far_wider_than_usual_is_temporary(self):
        evidence = assess(steady(100), steady(110), current_spread=40)
        assert evidence.verdict is SpreadVerdict.TEMPORARY
        assert evidence.spread_ratio is not None
        assert evidence.spread_ratio >= TEMPORARY_RATIO

    def test_just_under_the_ratio_stays_structural(self):
        """The boundary is a threshold, not a gradient, and it is stated."""
        evidence = assess(steady(100), steady(110), current_spread=14)
        assert evidence.spread_ratio is not None
        assert evidence.spread_ratio < TEMPORARY_RATIO
        assert evidence.verdict is SpreadVerdict.STRUCTURAL


class TestUnstable:
    def test_a_swinging_side_makes_the_spread_unstable(self):
        swinging = series([100 + (60 if index % 2 else -60) for index in range(120)])
        evidence = assess(steady(100), swinging, current_spread=30)
        assert evidence.verdict is SpreadVerdict.UNSTABLE
        assert evidence.target.volatility is not None
        assert evidence.target.volatility >= UNSTABLE_VOLATILITY

    def test_instability_outranks_a_wide_gap(self):
        """Both conditions can hold; the reader needs the more actionable one.

        A gap that is wide today matters much less than prices that will not
        hold still long enough to realise it.
        """
        swinging = series([100 + (60 if index % 2 else -60) for index in range(120)])
        evidence = assess(steady(100), swinging, current_spread=200)
        assert evidence.verdict is SpreadVerdict.UNSTABLE

    def test_the_reason_names_the_side_that_moves(self):
        swinging = series([100 + (60 if index % 2 else -60) for index in range(120)])
        evidence = assess(swinging, steady(200), current_spread=100)
        assert evidence.verdict is SpreadVerdict.UNSTABLE
        assert any("source" in reason for reason in evidence.reasons)


class TestConfidence:
    def test_one_thin_side_caps_the_whole_conclusion(self):
        """A conclusion about two prices is only as good as the worse of them.

        A long, clean history on the buying side does not make up for fifteen
        observations on the selling side: the spread is the difference between
        them, and the weaker one bounds what can be claimed about it.
        """
        evidence = assess(steady(100, days=120), steady(130, days=15), current_spread=30)
        assert evidence.confidence is not Confidence.HIGH

    def test_thin_evidence_is_capped_low(self):
        evidence = assess(steady(100, days=12), steady(130, days=12), current_spread=30)
        assert evidence.confidence in {Confidence.LOW, Confidence.NONE}
