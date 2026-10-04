"""Spreadline's own observation history.

Reads and administers the dataset the intelligence engine is built on: which
listings are under observation, what has actually been observed about them, and
how much of that is a market record rather than a fixture.

Real and simulated counts are reported separately at every level. The argument
for owning this data is that it can be trusted, and a total that mixed the two
would undermine exactly that.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import Auth, DbSession
from app.core.clock import iso_utc
from app.core.errors import NotFoundError, ValidationError
from app.domains.history import service as history
from app.domains.opportunities.analysis import load_price_points
from app.domains.pricing.statistics import analyze_prices
from app.models.catalog import MarketplaceListing
from app.models.enums import Marketplace
from app.schemas.history import TrackedListingResponse, TrackListingRequest

router = APIRouter(prefix="/history", tags=["history"])


def _entry(row: Any, due: Any = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "listing_id": row.listing_id,
        "product_id": row.product_id,
        "marketplace": row.marketplace,
        "external_id": row.external_id,
        "reason": row.reason,
        "priority": row.priority,
        "is_active": row.is_active,
        "refresh_interval_seconds": history.refresh_interval_for(row),
        "refresh_interval_is_default": row.refresh_interval_seconds is None,
        "last_refreshed_at": iso_utc(row.last_refreshed_at),
        "last_success_at": iso_utc(row.last_success_at),
        "last_failure_at": iso_utc(row.last_failure_at),
        "last_error": row.last_error,
        "consecutive_failures": row.consecutive_failures,
        "observation_count": row.observation_count,
        "due_at": iso_utc(due if due is not None else history.due_at(row)),
        "notes": row.notes,
    }


@router.get("")
def dataset(session: DbSession, auth: Auth) -> dict[str, Any]:
    """The size and honesty of the dataset as a whole."""
    return history.dataset_totals(session, auth.organization_id)


@router.get("/universe")
def list_universe(
    session: DbSession,
    auth: Auth,
    active_only: bool = True,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """What Spreadline is keeping under observation, and what is due."""
    entries = history.universe(
        session, auth.organization_id, active_only=active_only, limit=limit
    )
    return {
        "items": [_entry(entry.row, entry.due_at) for entry in entries],
        "total": len(entries),
        "due_now": sum(1 for entry in entries if entry.is_due),
    }


@router.post("/universe", response_model=TrackedListingResponse)
def add_to_universe(
    payload: TrackListingRequest, session: DbSession, auth: Auth
) -> Any:
    """Start observing a listing.

    Idempotent: tracking something already tracked updates it and reactivates
    it rather than creating a second standing instruction for the same listing.
    """
    active = [
        entry
        for entry in history.universe(session, auth.organization_id)
        if entry.row.is_active
    ]
    already = any(
        entry.row.marketplace == payload.marketplace.value
        and entry.row.external_id == payload.external_id
        for entry in active
    )
    if not already and len(active) >= payload.universe_limit():
        raise ValidationError(
            f"The tracked universe is capped at {payload.universe_limit()} listings. "
            "Stop observing something before adding another, or raise "
            "HISTORY_UNIVERSE_LIMIT."
        )

    # Linked to a stored listing when one exists, and tracked anyway when it does
    # not: an external id worth observing is worth observing before the first
    # poll has created a row for it.
    listing = session.scalar(
        select(MarketplaceListing).where(
            MarketplaceListing.organization_id == auth.organization_id,
            MarketplaceListing.marketplace == payload.marketplace.value,
            MarketplaceListing.external_id == payload.external_id,
        )
    )
    row = history.track(
        session,
        auth.organization_id,
        marketplace=payload.marketplace,
        external_id=payload.external_id,
        listing=listing,
        reason=payload.reason,
        priority=payload.priority,
        refresh_interval_seconds=payload.refresh_interval_seconds,
        notes=payload.notes,
    )
    session.commit()
    return _entry(row)


@router.delete("/universe/{tracked_id}", response_model=TrackedListingResponse)
def remove_from_universe(tracked_id: str, session: DbSession, auth: Auth) -> Any:
    """Stop observing a listing, keeping everything already observed."""
    row = history.untrack(session, auth.organization_id, tracked_id)
    if row is None:
        raise NotFoundError(f"No tracked listing {tracked_id}.")
    session.commit()
    return _entry(row)


@router.get("/listings/{listing_id}")
def listing_history(
    session: DbSession,
    auth: Auth,
    listing_id: str,
    days: int | None = Query(default=None, ge=1, le=1095),
    include_simulated: bool = True,
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict[str, Any]:
    """Everything Spreadline has observed about one listing.

    The raw series is returned alongside the window statistics because a
    statistic is only as trustworthy as the series under it, and someone
    checking a volatility figure has to be able to see the prices behind it.
    """
    listing = session.get(MarketplaceListing, listing_id)
    if listing is None or listing.organization_id != auth.organization_id:
        raise NotFoundError(f"No listing {listing_id}.")

    return {
        "listing": {
            "id": listing.id,
            "marketplace": listing.marketplace,
            "external_id": listing.external_id,
            "title": listing.title,
            "provider": listing.provider,
        },
        "coverage": history.coverage(session, listing_id).as_dict(),
        "statistics": analyze_prices(load_price_points(session, listing_id)).as_dict(),
        "observations": history.price_series(
            session,
            listing_id,
            days=days,
            include_simulated=include_simulated,
            limit=limit,
        ),
    }


@router.post("/refresh")
async def refresh_now(session: DbSession, auth: Auth) -> dict[str, Any]:
    """Run one bounded refresh pass immediately.

    The same pass the scheduler runs, exposed so the universe can be exercised
    without waiting for a tick. Bounded by the same batch size: an endpoint that
    could poll the whole universe on demand is an endpoint that can be used to
    spend a provider allowance in one request.
    """
    from app.services.scheduling.jobs import _refresh_universe

    return await _refresh_universe()


@router.get("/marketplaces")
def observed_marketplaces(session: DbSession, auth: Auth) -> dict[str, Any]:
    """Which marketplaces the dataset actually covers."""
    entries = history.universe(session, auth.organization_id, active_only=False)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.row.marketplace] = counts.get(entry.row.marketplace, 0) + 1
    return {
        "marketplaces": [
            {"marketplace": key, "tracked_listings": value}
            for key, value in sorted(counts.items())
        ],
        "known": [marketplace.value for marketplace in Marketplace],
    }
