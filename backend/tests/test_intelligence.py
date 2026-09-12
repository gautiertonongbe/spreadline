"""Price statistics, anomalies, demand and competition.

The recurring theme: the engines must distinguish "we measured this and it is
low" from "we do not know", and must never present the second as the first.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.core.clock import utcnow
from app.domains.competition.service import (
    CompetitionPoint,
    OfferSnapshot,
    assess_competition,
)
from app.domains.demand.service import DemandPoint, assess_demand, rank_to_relative_score
from app.domains.pricing.anomaly import detect_anomaly
from app.domains.pricing.statistics import PricePoint, analyze_prices
from app.models.enums import (
    AnomalyType,
    Availability,
    Confidence,
    RiskLevel,
    TrendDirection,
)

NOW = utcnow()


def series(prices, *, days_apart: int = 1, end: object = None):
    """Build a price series ending now, one observation per day going back."""
    end = end or NOW
    return [
        PricePoint(
            price=Decimal(str(price)),
            observed_at=end - timedelta(days=(len(prices) - 1 - i) * days_apart),
        )
        for i, price in enumerate(prices)
    ]


class TestPriceStatistics:
    def test_three_observations_do_not_become_a_year_of_history(self):
        """The single most important claim in the statistics module."""
        analysis = analyze_prices(series([100, 101, 99], days_apart=90))
        year = analysis.window(365)
        assert year is not None
        assert year.observation_count == 3
        assert year.sufficient is False
        assert year.coverage < Decimal("0.05")
        assert "days carry an observation" in (year.reason or "")

    def test_insufficient_window_reports_no_statistics(self):
        analysis = analyze_prices(series([100, 102]))
        window = analysis.window(30)
        assert window is not None
        assert window.average is None
        assert window.median is None
        assert window.sufficient is False

    def test_dense_history_is_high_confidence(self):
        analysis = analyze_prices(series([100 + (i % 5) for i in range(120)]))
        window = analysis.window(90)
        assert window is not None
        assert window.sufficient
        assert window.confidence is Confidence.HIGH
        assert window.median is not None

    def test_reference_window_is_the_longest_usable_one(self):
        analysis = analyze_prices(series([100 + (i % 3) for i in range(100)]))
        assert analysis.reference_window == 90

    def test_no_observations_yields_no_reference(self):
        analysis = analyze_prices([])
        assert analysis.reference_window is None
        assert analysis.confidence is Confidence.NONE
        assert analysis.current_price is None

    def test_dispersion_needs_more_than_the_minimum(self):
        analysis = analyze_prices(series([100, 105, 95]))
        window = analysis.window(7)
        assert window is not None
        assert window.median is not None
        assert window.stdev is None  # 3 points is not enough for dispersion

    def test_rising_trend_is_detected(self):
        analysis = analyze_prices(series([100 + i for i in range(60)]))
        window = analysis.window(90)
        assert window is not None
        assert window.trend is TrendDirection.RISING
        assert window.trend_pct is not None and window.trend_pct > 0

    def test_noise_is_flat_not_a_trend(self):
        analysis = analyze_prices(series([100, 101, 99, 100, 101, 99] * 10))
        window = analysis.window(90)
        assert window is not None
        assert window.trend is TrendDirection.FLAT

    def test_drawdown_and_recovery(self):
        prices = [100] * 10 + [60] * 5 + [95] * 10
        window = analyze_prices(series(prices)).window(90)
        assert window is not None
        assert window.max_drawdown is not None
        assert window.max_drawdown >= Decimal("0.39")
        assert window.recovery_pct is not None and window.recovery_pct > Decimal("0.8")

    def test_volatility_is_scale_free(self):
        cheap = analyze_prices(series([10, 11, 9, 10, 11, 9] * 8)).window(90)
        dear = analyze_prices(series([1000, 1100, 900, 1000, 1100, 900] * 8)).window(90)
        assert cheap is not None and dear is not None
        assert abs(cheap.volatility - dear.volatility) < Decimal("0.01")


class TestAnomalyDetection:
    def test_no_baseline_means_unknown_not_normal(self):
        points = series([100, 50])
        analysis = analyze_prices(points)
        anomaly = detect_anomaly(analysis, points)
        assert anomaly.anomaly_type is AnomalyType.UNKNOWN
        assert anomaly.confidence is Confidence.NONE

    def test_severe_discount_against_a_real_baseline(self):
        points = series([100] * 60 + [55])
        anomaly = detect_anomaly(analyze_prices(points), points)
        assert anomaly.anomaly_type in {
            AnomalyType.SEVERE_DISCOUNT,
            AnomalyType.PRICE_COLLAPSE,
        }
        assert anomaly.deviation is not None and anomaly.deviation < 0

    def test_low_stock_plus_deep_discount_reads_as_clearance(self):
        points = series([100] * 60 + [55])
        anomaly = detect_anomaly(
            analyze_prices(points),
            points,
            availability=Availability.LIMITED,
            quantity_available=3,
        )
        assert anomaly.anomaly_type is AnomalyType.POTENTIAL_CLEARANCE
        assert "clearance" in anomaly.message

    def test_premium_is_detected_in_the_other_direction(self):
        points = series([100] * 60 + [140])
        anomaly = detect_anomaly(analyze_prices(points), points)
        assert anomaly.anomaly_type in {AnomalyType.SEVERE_PREMIUM, AnomalyType.PRICE_SPIKE}
        assert anomaly.deviation is not None and anomaly.deviation > 0

    def test_stable_price_is_not_an_anomaly(self):
        points = series([100 + (i % 3) for i in range(60)])
        anomaly = detect_anomaly(analyze_prices(points), points)
        assert anomaly.is_anomalous is False

    def test_robust_baseline_resists_outliers(self):
        """A mean-based baseline would be dragged toward the outliers it is
        supposed to detect. The median is not."""
        points = series([100] * 50 + [500, 500] + [100] * 8 + [100])
        anomaly = detect_anomaly(analyze_prices(points), points)
        assert anomaly.reference_median == Decimal("100.0000")


class TestDemand:
    def test_missing_demand_is_none_not_zero(self):
        """Refusing to score is the whole point."""
        result = assess_demand([])
        assert result.score is None
        assert result.confidence is Confidence.NONE
        assert any("unknown" in reason for reason in result.reasons)

    def test_rank_maps_to_a_relative_score_on_a_log_scale(self):
        assert rank_to_relative_score(1) == Decimal("100")
        assert rank_to_relative_score(1_000_000) == Decimal("0")
        best = rank_to_relative_score(100)
        mid = rank_to_relative_score(10_000)
        worst = rank_to_relative_score(500_000)
        assert best > mid > worst

    def test_no_unit_estimate_without_a_provider_basis(self):
        points = [
            DemandPoint(sales_rank=500, observed_at=NOW - timedelta(days=i)) for i in range(40)
        ]
        result = assess_demand(points)
        assert result.estimated_monthly_units is None
        assert any("calibration" in reason for reason in result.reasons)

    def test_provider_estimate_passes_through_with_its_basis(self):
        points = [
            DemandPoint(
                sales_rank=500,
                observed_at=NOW - timedelta(days=i),
                estimated_monthly_units=320,
                estimation_basis="provider-category-model-v3",
            )
            for i in range(40)
        ]
        result = assess_demand(points)
        assert result.estimated_monthly_units == 320
        assert result.estimation_basis == "provider-category-model-v3"

    def test_improving_rank_is_rising_demand(self):
        points = [
            DemandPoint(sales_rank=10_000 - i * 100, observed_at=NOW - timedelta(days=40 - i))
            for i in range(40)
        ]
        assert assess_demand(points).rank_trend is TrendDirection.RISING

    def test_review_velocity_needs_enough_span(self):
        short = [
            DemandPoint(sales_rank=100, observed_at=NOW - timedelta(days=i), review_count=100 + i)
            for i in range(3)
        ]
        assert assess_demand(short).review_velocity_30d is None

    def test_review_velocity_is_computed_over_a_real_span(self):
        points = [
            DemandPoint(
                sales_rank=100,
                observed_at=NOW - timedelta(days=59 - i),
                review_count=1000 + i * 2,
            )
            for i in range(60)
        ]
        result = assess_demand(points)
        assert result.review_velocity_30d is not None
        assert result.review_velocity_30d > 0

    def test_score_uses_the_median_not_the_latest_rank(self):
        points = [
            DemandPoint(sales_rank=900_000, observed_at=NOW - timedelta(days=30 - i))
            for i in range(29)
        ] + [DemandPoint(sales_rank=10, observed_at=NOW)]
        result = assess_demand(points)
        assert result.score is not None and result.score < Decimal("20")


class TestCompetition:
    def _history(self, counts):
        return [
            CompetitionPoint(
                observed_at=NOW - timedelta(days=len(counts) - 1 - i),
                seller_count=count,
                seller_ids=tuple(f"S{n}" for n in range(count)),
            )
            for i, count in enumerate(counts)
        ]

    def test_growing_seller_count_raises_pressure(self):
        stable = assess_competition(self._history([9] * 30))
        growing = assess_competition(self._history(list(range(9, 39))))
        assert growing.pressure_score > stable.pressure_score
        assert growing.seller_trend is TrendDirection.RISING

    def test_entrants_are_a_set_difference_not_a_count_delta(self):
        """Three in and three out is churn, and a net delta of zero hides it."""
        history = [
            CompetitionPoint(
                observed_at=NOW - timedelta(days=10),
                seller_count=5,
                seller_ids=("A", "B", "C", "D", "E"),
            ),
            CompetitionPoint(observed_at=NOW, seller_count=5, seller_ids=("A", "B", "X", "Y", "Z")),
        ]
        result = assess_competition(history)
        assert result.entrants_30d == 3
        assert result.exits_30d == 3

    def test_marketplace_as_seller_is_called_out(self):
        offers = [
            OfferSnapshot(price=Decimal("20"), is_buy_box=True, is_marketplace_seller=True),
            OfferSnapshot(price=Decimal("21"), seller_id="S1"),
        ]
        result = assess_competition([], offers)
        assert result.marketplace_is_seller
        assert any("marketplace itself" in reason for reason in result.reasons)

    def test_no_data_is_reported_as_no_confidence(self):
        result = assess_competition([], [])
        assert result.confidence is Confidence.NONE
        assert result.seller_count is None

    def test_crowded_listing_is_high_risk(self):
        result = assess_competition(self._history([30] * 30))
        assert result.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}

    def test_quiet_listing_is_low_risk(self):
        result = assess_competition(self._history([2] * 30))
        assert result.risk_level is RiskLevel.LOW

    def test_dispersion_is_measured_from_offers(self):
        offers = [
            OfferSnapshot(price=Decimal("20"), is_buy_box=True),
            OfferSnapshot(price=Decimal("22")),
            OfferSnapshot(price=Decimal("30")),
        ]
        result = assess_competition([], offers)
        assert result.price_dispersion is not None
        assert result.lowest_offer == Decimal("20.0000")
        assert result.highest_offer == Decimal("30.0000")
