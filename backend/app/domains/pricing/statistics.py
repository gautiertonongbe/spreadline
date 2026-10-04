"""Price statistics over rolling windows.

The rule that shapes this module: a window reports statistics only when the
observations behind it can carry them. Three prices spread over 200 days is not a
200-day history, and presenting it as one is how a platform talks an operator into
a position (spec §10).

Every window therefore returns its own ``sufficient`` flag, observation count,
day coverage and confidence, and the presentation layer is expected to show them.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.clock import ensure_utc, iso_utc, utcnow
from app.core.money import money, ratio
from app.models.enums import Confidence, TrendDirection

#: The windows Spreadline reports on (spec §10).
WINDOWS: tuple[int, ...] = (1, 7, 30, 90, 180, 365)

#: Below this many observations a window reports nothing but its own emptiness.
MIN_OBSERVATIONS = 3
#: Dispersion measures need more than the bare minimum to mean anything.
MIN_OBSERVATIONS_FOR_DISPERSION = 5
#: Share of the window's days that must carry at least one observation before the
#: window is called representative.
MIN_COVERAGE = Decimal("0.25")
#: Share of the window the observations must actually span. Coverage alone is not
#: enough: 100 daily observations clear the coverage bar on a 365-day window while
#: describing only the most recent third of it, and calling that a "365-day
#: median" overstates what is known by a factor of three. Set high deliberately,
#: so a window is only claimed once most of it has genuinely been watched.
MIN_SPAN_RATIO = Decimal("0.75")


@dataclass(frozen=True)
class PricePoint:
    """One observation, reduced to what statistics need."""

    price: Decimal
    observed_at: datetime

    @property
    def day(self) -> datetime:
        moment = ensure_utc(self.observed_at)
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass
class WindowStats:
    window_days: int
    observation_count: int
    distinct_days: int
    coverage: Decimal
    sufficient: bool
    confidence: Confidence
    #: Every statistic is None when the window cannot support it. None is not
    #: zero and the UI must render it as "not enough data".
    average: Decimal | None = None
    median: Decimal | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    stdev: Decimal | None = None
    p25: Decimal | None = None
    p75: Decimal | None = None
    #: Coefficient of variation: stdev / mean. Comparable across price levels,
    #: unlike a raw standard deviation.
    volatility: Decimal | None = None
    trend: TrendDirection = TrendDirection.UNKNOWN
    trend_pct: Decimal | None = None
    max_drawdown: Decimal | None = None
    recovery_pct: Decimal | None = None
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        def num(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        return {
            "window_days": self.window_days,
            "observation_count": self.observation_count,
            "distinct_days": self.distinct_days,
            "coverage": str(self.coverage),
            "sufficient": self.sufficient,
            "confidence": self.confidence.value,
            "average": num(self.average),
            "median": num(self.median),
            "minimum": num(self.minimum),
            "maximum": num(self.maximum),
            "stdev": num(self.stdev),
            "p25": num(self.p25),
            "p75": num(self.p75),
            "volatility": num(self.volatility),
            "trend": self.trend.value,
            "trend_pct": num(self.trend_pct),
            "max_drawdown": num(self.max_drawdown),
            "recovery_pct": num(self.recovery_pct),
            "first_observed_at": iso_utc(self.first_observed_at),
            "last_observed_at": iso_utc(self.last_observed_at),
            "reason": self.reason,
        }


@dataclass
class PriceHistoryAnalysis:
    current_price: Decimal | None
    observation_count: int
    history_span_days: int
    windows: dict[int, WindowStats]
    #: The longest window that is actually usable. The right denominator for
    #: "is this price unusual", chosen by evidence rather than by wishful default.
    reference_window: int | None
    confidence: Confidence

    def window(self, days: int) -> WindowStats | None:
        return self.windows.get(days)

    @property
    def reference(self) -> WindowStats | None:
        return self.windows.get(self.reference_window) if self.reference_window else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "current_price": None if self.current_price is None else str(self.current_price),
            "observation_count": self.observation_count,
            "history_span_days": self.history_span_days,
            "reference_window": self.reference_window,
            "confidence": self.confidence.value,
            "windows": {str(k): v.as_dict() for k, v in sorted(self.windows.items())},
        }


def _percentile(ordered: Sequence[Decimal], fraction: float) -> Decimal:
    """Linear-interpolated percentile over a sorted sequence."""
    if not ordered:
        raise ValueError("empty sequence")
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = Decimal(str(position - lower))
    return money(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def _trend(points: Sequence[PricePoint]) -> tuple[TrendDirection, Decimal | None]:
    """Least-squares slope across the window, expressed as total % change.

    A regression rather than first-vs-last, because first-vs-last is decided by
    two observations and flips on noise.
    """
    if len(points) < MIN_OBSERVATIONS:
        return TrendDirection.UNKNOWN, None
    ordered = sorted(points, key=lambda point: ensure_utc(point.observed_at))
    origin = ensure_utc(ordered[0].observed_at)
    xs = [(ensure_utc(point.observed_at) - origin).total_seconds() / 86400 for point in ordered]
    ys = [float(point.price) for point in ordered]
    span = xs[-1] - xs[0]
    if span <= 0:
        return TrendDirection.UNKNOWN, None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0 or mean_y == 0:
        return TrendDirection.UNKNOWN, None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denominator
    total_change = (slope * span) / mean_y
    change = ratio(Decimal(str(total_change)))
    # Below 3% across the whole window is noise, not a trend.
    if abs(change) < Decimal("0.03"):
        return TrendDirection.FLAT, change
    return (TrendDirection.RISING if change > 0 else TrendDirection.FALLING), change


def _drawdown(points: Sequence[PricePoint]) -> tuple[Decimal | None, Decimal | None]:
    """Deepest peak-to-trough fall, and how much of it has been recovered."""
    if len(points) < MIN_OBSERVATIONS:
        return None, None
    ordered = sorted(points, key=lambda point: ensure_utc(point.observed_at))
    peak = ordered[0].price
    worst = Decimal("0")
    trough = ordered[0].price
    trough_index = 0
    for index, point in enumerate(ordered):
        if point.price > peak:
            peak = point.price
        if peak > 0:
            decline = (peak - point.price) / peak
            if decline > worst:
                worst = decline
                trough = point.price
                trough_index = index
    if worst == 0:
        return ratio(Decimal("0")), ratio(Decimal("1"))
    after = ordered[trough_index:]
    best_after = max((point.price for point in after), default=trough)
    peak_at_trough = trough / (Decimal("1") - worst) if worst < 1 else trough
    span = peak_at_trough - trough
    recovery = (best_after - trough) / span if span > 0 else Decimal("1")
    return ratio(worst), ratio(min(Decimal("1"), max(Decimal("0"), recovery)))


def _confidence_for(count: int, coverage: Decimal, window_days: int) -> Confidence:
    if count == 0:
        return Confidence.NONE
    if count < MIN_OBSERVATIONS:
        return Confidence.LOW
    if window_days <= 7:
        # Short windows need density rather than breadth.
        return Confidence.HIGH if count >= 3 else Confidence.MEDIUM
    if coverage >= Decimal("0.6") and count >= 10:
        return Confidence.HIGH
    if coverage >= MIN_COVERAGE and count >= MIN_OBSERVATIONS_FOR_DISPERSION:
        return Confidence.MEDIUM
    return Confidence.LOW


def analyze_window(
    points: Sequence[PricePoint], window_days: int, *, now: datetime | None = None
) -> WindowStats:
    now = now or utcnow()
    cutoff = now - timedelta(days=window_days)
    in_window = [point for point in points if ensure_utc(point.observed_at) >= cutoff]
    distinct_days = len({point.day for point in in_window})
    coverage = ratio(Decimal(distinct_days) / Decimal(window_days)) if window_days else Decimal("0")
    confidence = _confidence_for(len(in_window), coverage, window_days)

    if len(in_window) < MIN_OBSERVATIONS:
        return WindowStats(
            window_days=window_days,
            observation_count=len(in_window),
            distinct_days=distinct_days,
            coverage=coverage,
            sufficient=False,
            confidence=confidence,
            first_observed_at=min((p.observed_at for p in in_window), default=None),
            last_observed_at=max((p.observed_at for p in in_window), default=None),
            reason=(
                f"{len(in_window)} observation(s) in the last {window_days} days; "
                f"{MIN_OBSERVATIONS} are required."
            ),
        )

    first_observed = min(point.observed_at for point in in_window)
    last_observed = max(point.observed_at for point in in_window)
    span_days = (ensure_utc(last_observed) - ensure_utc(first_observed)).days
    spans_window = window_days <= 7 or Decimal(span_days) >= Decimal(window_days) * MIN_SPAN_RATIO
    sufficient = (coverage >= MIN_COVERAGE and spans_window) or window_days <= 7

    if not sufficient:
        if coverage < MIN_COVERAGE:
            reason = f"Only {distinct_days} of {window_days} days carry an observation."
        else:
            reason = (
                f"Observations span {span_days} of {window_days} days, so this is a "
                f"{span_days}-day view rather than a {window_days}-day one."
            )
    else:
        reason = None

    prices = sorted(point.price for point in in_window)
    average = money(sum(prices) / Decimal(len(prices)))
    median = money(statistics.median(prices))
    has_dispersion = len(in_window) >= MIN_OBSERVATIONS_FOR_DISPERSION
    stdev = (
        money(Decimal(str(statistics.stdev([float(price) for price in prices]))))
        if has_dispersion
        else None
    )
    volatility = ratio(stdev / average) if stdev is not None and average > 0 else None
    trend, trend_pct = _trend(in_window)
    drawdown, recovery = _drawdown(in_window)

    return WindowStats(
        window_days=window_days,
        observation_count=len(in_window),
        distinct_days=distinct_days,
        coverage=coverage,
        sufficient=sufficient,
        confidence=confidence,
        average=average,
        median=median,
        minimum=prices[0],
        maximum=prices[-1],
        stdev=stdev,
        p25=_percentile(prices, 0.25) if has_dispersion else None,
        p75=_percentile(prices, 0.75) if has_dispersion else None,
        volatility=volatility,
        trend=trend,
        trend_pct=trend_pct,
        max_drawdown=drawdown,
        recovery_pct=recovery,
        first_observed_at=first_observed,
        last_observed_at=last_observed,
        reason=reason,
    )


def analyze_prices(
    points: Iterable[PricePoint],
    *,
    windows: Sequence[int] = WINDOWS,
    now: datetime | None = None,
) -> PriceHistoryAnalysis:
    now = now or utcnow()
    ordered = sorted(points, key=lambda point: ensure_utc(point.observed_at))
    stats = {window: analyze_window(ordered, window, now=now) for window in windows}

    current = ordered[-1].price if ordered else None
    span = 0
    if len(ordered) >= 2:
        span = (ensure_utc(ordered[-1].observed_at) - ensure_utc(ordered[0].observed_at)).days

    # The reference window is the longest one that is both sufficient and at
    # least medium confidence; nothing else earns the right to define "normal".
    usable = [
        window
        for window, result in sorted(stats.items())
        if result.sufficient and result.confidence in {Confidence.HIGH, Confidence.MEDIUM}
    ]
    reference = max(usable) if usable else None

    overall = Confidence.NONE
    if reference is not None:
        overall = stats[reference].confidence
        if reference < 30:
            # A week of data can be high-confidence about the week and still tell
            # you nothing about whether today's price is normal.
            overall = Confidence.LOW if reference <= 7 else Confidence.MEDIUM
    elif ordered:
        overall = Confidence.LOW

    return PriceHistoryAnalysis(
        current_price=current,
        observation_count=len(ordered),
        history_span_days=span,
        windows=stats,
        reference_window=reference,
        confidence=overall,
    )
