"""Price anomaly detection.

Answers "is this price unusual, and in which direction", and deliberately stops
there. A low price is not an opportunity; it is a question. Clearance, a pricing
error, a seller dumping damaged stock and a genuine promotion all look identical
in a price series, so the detector reports what it sees and hands the
interpretation to the risk and opportunity engines (spec §11).

Robust statistics throughout: median and median absolute deviation rather than
mean and standard deviation, because the very outliers being detected would
otherwise drag the baseline towards themselves.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.money import money, ratio
from app.domains.pricing.statistics import PriceHistoryAnalysis, PricePoint
from app.models.enums import AnomalyType, Availability, Confidence, Severity

#: Deviation from the reference median, as a share of it.
DISCOUNT_THRESHOLD = Decimal("0.15")
SEVERE_DISCOUNT_THRESHOLD = Decimal("0.30")
PREMIUM_THRESHOLD = Decimal("0.15")
SEVERE_PREMIUM_THRESHOLD = Decimal("0.30")
#: Single-step move that counts as a collapse or spike.
COLLAPSE_THRESHOLD = Decimal("0.20")
SPIKE_THRESHOLD = Decimal("0.20")
#: Robust z-score above which a point is an outlier regardless of the percentage.
ROBUST_Z_THRESHOLD = Decimal("3.5")
#: Scale factor making MAD a consistent estimator of the standard deviation.
MAD_SCALE = Decimal("1.4826")


@dataclass
class PriceAnomaly:
    anomaly_type: AnomalyType
    severity: Severity
    confidence: Confidence
    message: str
    #: Signed deviation from the reference median. Negative is below.
    deviation: Decimal | None = None
    robust_z: Decimal | None = None
    reference_median: Decimal | None = None
    reference_window: int | None = None
    evidence: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.evidence is None:
            self.evidence = []

    @property
    def is_anomalous(self) -> bool:
        return self.anomaly_type not in {AnomalyType.NONE, AnomalyType.UNKNOWN}

    @property
    def is_below(self) -> bool:
        return self.anomaly_type in {
            AnomalyType.BELOW_MEDIAN,
            AnomalyType.SEVERE_DISCOUNT,
            AnomalyType.PRICE_COLLAPSE,
            AnomalyType.POTENTIAL_CLEARANCE,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.anomaly_type.value,
            "severity": self.severity.value,
            "confidence": self.confidence.value,
            "message": self.message,
            "deviation": None if self.deviation is None else str(self.deviation),
            "robust_z": None if self.robust_z is None else str(self.robust_z),
            "reference_median": (
                None if self.reference_median is None else str(self.reference_median)
            ),
            "reference_window": self.reference_window,
            "evidence": self.evidence,
        }


def _robust_z(current: Decimal, prices: Sequence[Decimal]) -> tuple[Decimal | None, Decimal]:
    """(robust z-score, median). Returns None for z when dispersion is zero."""
    median = money(statistics.median(prices))
    deviations = [abs(price - median) for price in prices]
    mad = money(statistics.median(deviations)) if deviations else Decimal("0")
    if mad == 0:
        return None, median
    return ratio((current - median) / (MAD_SCALE * mad)), median


def detect_anomaly(
    analysis: PriceHistoryAnalysis,
    points: Sequence[PricePoint],
    *,
    availability: Availability = Availability.UNKNOWN,
    quantity_available: int | None = None,
) -> PriceAnomaly:
    """Classify the most recent price against its own history."""
    current = analysis.current_price
    if current is None:
        return PriceAnomaly(
            AnomalyType.UNKNOWN,
            Severity.INFO,
            Confidence.NONE,
            "No current price observed.",
        )

    reference = analysis.reference
    if reference is None or reference.median is None:
        return PriceAnomaly(
            AnomalyType.UNKNOWN,
            Severity.INFO,
            Confidence.NONE,
            f"Only {analysis.observation_count} price observation(s): "
            "there is no baseline to call this price normal or abnormal.",
            evidence=[
                "No window reached the minimum observation count and day coverage "
                "required to define a reference median."
            ],
        )

    window_prices = [
        point.price
        for point in points
        if reference.first_observed_at is None or point.observed_at >= reference.first_observed_at
    ] or [point.price for point in points]

    robust_z, median = _robust_z(current, window_prices)
    deviation = ratio((current - median) / median) if median > 0 else None
    evidence = [
        f"Current {current} against a {reference.window_days}-day median of {median} "
        f"across {reference.observation_count} observations."
    ]

    # A single-step move is graded separately: a 25% overnight fall is a
    # different event from a price that has been drifting down for a month.
    step_change = None
    if len(points) >= 2:
        previous = points[-2].price
        if previous > 0:
            step_change = ratio((current - previous) / previous)

    anomaly_type = AnomalyType.NONE
    severity = Severity.INFO
    message = f"Price is within its normal {reference.window_days}-day range."

    if step_change is not None and step_change <= -COLLAPSE_THRESHOLD:
        anomaly_type = AnomalyType.PRICE_COLLAPSE
        severity = Severity.HIGH
        message = f"Price fell {abs(step_change):.0%} since the previous observation."
        evidence.append(f"Single-step change of {step_change:.1%}.")
    elif step_change is not None and step_change >= SPIKE_THRESHOLD:
        anomaly_type = AnomalyType.PRICE_SPIKE
        severity = Severity.MEDIUM
        message = f"Price rose {step_change:.0%} since the previous observation."
        evidence.append(f"Single-step change of {step_change:.1%}.")
    elif deviation is not None and deviation <= -SEVERE_DISCOUNT_THRESHOLD:
        anomaly_type = AnomalyType.SEVERE_DISCOUNT
        severity = Severity.HIGH
        message = f"Price is {abs(deviation):.0%} below its {reference.window_days}-day median."
    elif deviation is not None and deviation <= -DISCOUNT_THRESHOLD:
        anomaly_type = AnomalyType.BELOW_MEDIAN
        severity = Severity.LOW
        message = f"Price is {abs(deviation):.0%} below its {reference.window_days}-day median."
    elif deviation is not None and deviation >= SEVERE_PREMIUM_THRESHOLD:
        anomaly_type = AnomalyType.SEVERE_PREMIUM
        severity = Severity.HIGH
        message = f"Price is {deviation:.0%} above its {reference.window_days}-day median."
    elif deviation is not None and deviation >= PREMIUM_THRESHOLD:
        anomaly_type = AnomalyType.ABOVE_MEDIAN
        severity = Severity.LOW
        message = f"Price is {deviation:.0%} above its {reference.window_days}-day median."
    elif robust_z is not None and abs(robust_z) >= ROBUST_Z_THRESHOLD:
        anomaly_type = AnomalyType.BELOW_MEDIAN if robust_z < 0 else AnomalyType.ABOVE_MEDIAN
        severity = Severity.MEDIUM
        message = f"Price is a statistical outlier (robust z = {robust_z}) against its own history."

    # Low stock alongside a deep discount is the signature of clearance, and of a
    # price that will not be there when the purchase order lands.
    if anomaly_type in {AnomalyType.SEVERE_DISCOUNT, AnomalyType.PRICE_COLLAPSE}:
        if availability is Availability.LIMITED or (
            quantity_available is not None and quantity_available <= 5
        ):
            anomaly_type = AnomalyType.POTENTIAL_CLEARANCE
            severity = Severity.HIGH
            message = (
                f"{message.rstrip('.')}, with limited stock remaining: "
                "this looks like clearance rather than a repeatable price."
            )
            evidence.append(
                f"Availability {availability.value}"
                + (
                    f", {quantity_available} units offered."
                    if quantity_available is not None
                    else "."
                )
            )

    if anomaly_type in {AnomalyType.SEVERE_PREMIUM, AnomalyType.PRICE_SPIKE} and (
        availability is Availability.LIMITED
        or (quantity_available is not None and quantity_available <= 5)
    ):
        anomaly_type = AnomalyType.POTENTIAL_SHORTAGE
        severity = Severity.MEDIUM
        message = (
            f"{message.rstrip('.')}, with limited stock: the elevated price may be a "
            "temporary shortage rather than a durable level."
        )

    if robust_z is not None:
        evidence.append(f"Robust z-score {robust_z} (median absolute deviation basis).")

    return PriceAnomaly(
        anomaly_type=anomaly_type,
        severity=severity,
        confidence=reference.confidence,
        message=message,
        deviation=deviation,
        robust_z=robust_z,
        reference_median=median,
        reference_window=reference.window_days,
        evidence=evidence,
    )
