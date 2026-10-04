"""Spreadline's own cross-market observation history.

Every window statistic, volatility figure and spread verdict in this platform is
computed from observations it recorded itself. That is the point: the
intelligence engine should get less dependent on third-party history over time,
not more, and the only way that happens is by observing consistently from the
first day.

This module owns two halves of that:

**The universe.** Which listings are being kept under observation, at what
cadence, and which are due. Bounded on purpose. Spreadline does not crawl a
marketplace; it observes what the operator is working on, and it backs off a
listing that keeps failing rather than spending a quota on a dead id.

**Retrieval.** Reading the observations back as a series, with the coverage
behind them stated. The window statistics themselves live in
``domains/pricing/statistics``; nothing is duplicated here.

Nothing in this module invents an observation. A field a provider did not return
is stored as null and read back as null, and simulated observations are marked as
such at capture so that a dataset built during development can never be mistaken
for a market record.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session

from app.core.clock import ensure_utc, iso_utc, utcnow
from app.core.config import settings
from app.models.catalog import MarketplaceListing
from app.models.enums import Marketplace
from app.models.history import TrackedListing
from app.models.observations import CompetitionObservation, DemandObservation, PriceObservation

#: Why a listing entered the universe. Kept so a universe that grew by accident
#: can be pruned deliberately rather than by guesswork.
REASON_MANUAL = "manual"
REASON_ANALYSIS = "analysis"
REASON_OPPORTUNITY = "opportunity"

#: Backoff for a listing that keeps failing, as a multiplier on its interval.
#: A dead external id should cost one call a day, not one call a run.
MAX_BACKOFF_MULTIPLIER = 16


@dataclass
class UniverseEntry:
    """One tracked listing, with everything needed to decide whether to poll it."""

    row: TrackedListing
    due_at: datetime | None

    @property
    def is_due(self) -> bool:
        return self.due_at is None or self.due_at <= utcnow()


def refresh_interval_for(row: TrackedListing) -> int:
    """Seconds between polls for one listing.

    An explicit interval on the row is the operator overriding the default for
    this listing. Otherwise the price TTL is used, because re-polling faster
    than the freshness policy considers stale spends a quota to learn nothing.
    """
    if row.refresh_interval_seconds and row.refresh_interval_seconds > 0:
        return row.refresh_interval_seconds
    return settings.ttl_current_price_seconds


def due_at(row: TrackedListing) -> datetime | None:
    """When this listing may next be polled, or None when it never has been.

    Consecutive failures push the next attempt out geometrically. A listing that
    has failed five times in a row is almost certainly a bad id, and retrying it
    on every run is how one bad id consumes a provider allowance.
    """
    if row.last_refreshed_at is None:
        return None
    interval = refresh_interval_for(row)
    backoff = min(2**row.consecutive_failures, MAX_BACKOFF_MULTIPLIER)
    return ensure_utc(row.last_refreshed_at) + timedelta(seconds=interval * backoff)


def track(
    session: Session,
    organization_id: str,
    *,
    marketplace: Marketplace | str,
    external_id: str,
    listing: MarketplaceListing | None = None,
    product_id: str | None = None,
    reason: str = REASON_MANUAL,
    priority: int = 0,
    refresh_interval_seconds: int | None = None,
    notes: str | None = None,
) -> TrackedListing:
    """Add a listing to the universe, or refresh what is known about one.

    Idempotent by (organization, marketplace, external id): calling it again for
    a listing already tracked updates the linkage and reactivates it rather than
    creating a second standing instruction for the same thing.
    """
    value = marketplace.value if isinstance(marketplace, Marketplace) else str(marketplace)
    existing = session.scalar(
        select(TrackedListing).where(
            TrackedListing.organization_id == organization_id,
            TrackedListing.marketplace == value,
            TrackedListing.external_id == external_id,
        )
    )
    if existing is not None:
        existing.is_active = True
        existing.listing_id = listing.id if listing is not None else existing.listing_id
        existing.product_id = product_id or (
            listing.product_id if listing is not None else existing.product_id
        )
        if refresh_interval_seconds is not None:
            existing.refresh_interval_seconds = refresh_interval_seconds
        existing.priority = max(existing.priority, priority)
        if notes:
            existing.notes = notes
        session.flush()
        return existing

    row = TrackedListing(
        organization_id=organization_id,
        listing_id=listing.id if listing is not None else None,
        product_id=product_id or (listing.product_id if listing is not None else None),
        marketplace=value,
        external_id=external_id,
        reason=reason,
        priority=priority,
        refresh_interval_seconds=refresh_interval_seconds,
        notes=notes,
    )
    session.add(row)
    session.flush()
    return row


def untrack(session: Session, organization_id: str, tracked_id: str) -> TrackedListing | None:
    """Stop observing a listing without losing what was already observed.

    Deactivated rather than deleted: the observations remain, and a history with
    a gap in it should be explainable by a row saying when observation stopped.
    """
    row = session.scalar(
        select(TrackedListing).where(
            TrackedListing.organization_id == organization_id,
            TrackedListing.id == tracked_id,
        )
    )
    if row is None:
        return None
    row.is_active = False
    session.flush()
    return row


def universe(
    session: Session,
    organization_id: str,
    *,
    active_only: bool = True,
    limit: int | None = None,
) -> list[UniverseEntry]:
    """The tracked universe, highest priority and longest unobserved first."""
    query = select(TrackedListing).where(TrackedListing.organization_id == organization_id)
    if active_only:
        query = query.where(TrackedListing.is_active.is_(True))
    query = query.order_by(
        TrackedListing.priority.desc(),
        TrackedListing.last_refreshed_at.asc().nulls_first(),
    )
    if limit is not None:
        query = query.limit(limit)
    return [UniverseEntry(row=row, due_at=due_at(row)) for row in session.scalars(query)]


def due_for_refresh(
    session: Session, organization_id: str, *, limit: int
) -> list[TrackedListing]:
    """The next listings to poll, bounded.

    The bound is the point. An unbounded refresh is how a scheduled job turns
    into an unplanned bill, and a run that cannot cover the whole universe
    should cover the most important part of it rather than an arbitrary part.
    """
    entries = [entry for entry in universe(session, organization_id) if entry.is_due]
    return [entry.row for entry in entries[:limit]]


def record_attempt(
    row: TrackedListing,
    *,
    succeeded: bool,
    observations: int = 0,
    error: str | None = None,
    now: datetime | None = None,
) -> None:
    """Record the outcome of one poll on the tracked row."""
    moment = now or utcnow()
    row.last_refreshed_at = moment
    if succeeded:
        row.last_success_at = moment
        row.consecutive_failures = 0
        row.last_error = None
        row.observation_count += observations
    else:
        row.last_failure_at = moment
        row.consecutive_failures += 1
        row.last_error = (error or "unknown error")[:500]


# ------------------------------------------------------------------ retrieval


@dataclass
class ObservationCoverage:
    """How much history exists for one listing, and how much of it is real.

    The simulated count is reported separately rather than filtered out. A
    development database is mostly fixtures, and a coverage figure that hid that
    would be the most misleading number on the screen.
    """

    listing_id: str
    price_observations: int
    demand_observations: int
    competition_observations: int
    simulated_price_observations: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    distinct_days: int

    @property
    def real_price_observations(self) -> int:
        return self.price_observations - self.simulated_price_observations

    @property
    def span_days(self) -> int:
        if self.first_observed_at is None or self.last_observed_at is None:
            return 0
        return max(
            0, (ensure_utc(self.last_observed_at) - ensure_utc(self.first_observed_at)).days
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "listing_id": self.listing_id,
            "price_observations": self.price_observations,
            "demand_observations": self.demand_observations,
            "competition_observations": self.competition_observations,
            "simulated_price_observations": self.simulated_price_observations,
            "real_price_observations": self.real_price_observations,
            "first_observed_at": iso_utc(self.first_observed_at),
            "last_observed_at": iso_utc(self.last_observed_at),
            "distinct_days": self.distinct_days,
            "span_days": self.span_days,
        }


def coverage(session: Session, listing_id: str) -> ObservationCoverage:
    """What Spreadline has actually observed about one listing."""
    totals = session.execute(
        select(
            func.count(PriceObservation.id),
            func.min(PriceObservation.observed_at),
            func.max(PriceObservation.observed_at),
            func.count(func.distinct(func.date(PriceObservation.observed_at))),
            func.sum(func.cast(PriceObservation.is_simulated, Integer)),
        ).where(PriceObservation.listing_id == listing_id)
    ).one()

    demand_count = session.scalar(
        select(func.count(DemandObservation.id)).where(
            DemandObservation.listing_id == listing_id
        )
    )
    competition_count = session.scalar(
        select(func.count(CompetitionObservation.id)).where(
            CompetitionObservation.listing_id == listing_id
        )
    )

    return ObservationCoverage(
        listing_id=listing_id,
        price_observations=int(totals[0] or 0),
        first_observed_at=totals[1],
        last_observed_at=totals[2],
        distinct_days=int(totals[3] or 0),
        simulated_price_observations=int(totals[4] or 0),
        demand_observations=int(demand_count or 0),
        competition_observations=int(competition_count or 0),
    )


def price_series(
    session: Session,
    listing_id: str,
    *,
    days: int | None = None,
    include_simulated: bool = True,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """The raw observations, newest first, exactly as recorded.

    Exposed because a statistic is only as trustworthy as the series under it,
    and someone checking a volatility figure has to be able to see the prices it
    was computed from.
    """
    query = select(PriceObservation).where(PriceObservation.listing_id == listing_id)
    if days is not None:
        query = query.where(PriceObservation.observed_at >= utcnow() - timedelta(days=days))
    if not include_simulated:
        query = query.where(PriceObservation.is_simulated.is_(False))
    query = query.order_by(PriceObservation.observed_at.desc()).limit(limit)

    return [
        {
            "observed_at": iso_utc(row.observed_at),
            "retrieved_at": iso_utc(row.retrieved_at),
            "price": str(row.price),
            "shipping": str(row.shipping),
            "landed_price": str(row.landed_price),
            "currency": row.currency,
            "availability": row.availability,
            "condition": row.condition,
            "is_buy_box": row.is_buy_box,
            "marketplace": row.marketplace,
            "external_id": row.external_id,
            "gtin": row.gtin,
            "asin": row.asin,
            "provider": row.provider,
            "source": row.source,
            "is_simulated": row.is_simulated,
            "quality_score": None if row.quality_score is None else str(row.quality_score),
        }
        for row in session.scalars(query)
    ]


def dataset_totals(session: Session, organization_id: str) -> dict[str, Any]:
    """The size and honesty of the dataset as a whole.

    Real and simulated are counted separately at every level, because the whole
    argument for owning this data is that it can be trusted, and a total that
    mixed the two would undermine exactly that.
    """

    def _counts(model: Any) -> tuple[int, int]:
        total = session.scalar(
            select(func.count(model.id)).where(model.organization_id == organization_id)
        )
        simulated = session.scalar(
            select(func.count(model.id)).where(
                model.organization_id == organization_id,
                model.is_simulated.is_(True),
            )
        )
        return int(total or 0), int(simulated or 0)

    price_total, price_simulated = _counts(PriceObservation)
    demand_total, demand_simulated = _counts(DemandObservation)
    competition_total, competition_simulated = _counts(CompetitionObservation)

    tracked = session.scalar(
        select(func.count(TrackedListing.id)).where(
            TrackedListing.organization_id == organization_id
        )
    )
    tracked_active = session.scalar(
        select(func.count(TrackedListing.id)).where(
            TrackedListing.organization_id == organization_id,
            TrackedListing.is_active.is_(True),
        )
    )
    earliest = session.scalar(
        select(func.min(PriceObservation.observed_at)).where(
            PriceObservation.organization_id == organization_id
        )
    )

    return {
        "tracked_listings": int(tracked or 0),
        "tracked_listings_active": int(tracked_active or 0),
        "price_observations": price_total,
        "price_observations_real": price_total - price_simulated,
        "price_observations_simulated": price_simulated,
        "demand_observations": demand_total,
        "demand_observations_real": demand_total - demand_simulated,
        "competition_observations": competition_total,
        "competition_observations_real": competition_total - competition_simulated,
        "earliest_observation_at": iso_utc(earliest),
        "windows_supported": list(_supported_windows()),
    }


def _supported_windows() -> Sequence[int]:
    from app.domains.pricing.statistics import WINDOWS

    return WINDOWS
