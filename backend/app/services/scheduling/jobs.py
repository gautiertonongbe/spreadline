"""Background jobs.

Deliberately few. The platform is not trying to monitor millions of products
(spec §43): it refreshes what the operator is actually tracking, and it obeys the
TTL policy so a refresh that is not due costs nothing.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.clock import is_fresh, utcnow
from app.core.config import settings
from app.core.database import session_scope
from app.core.logging import get_logger
from app.domains.catalog import service as catalog
from app.domains.history import capture
from app.domains.history import service as history
from app.domains.tenancy.service import bootstrap
from app.models.catalog import MarketplaceListing
from app.models.enums import Marketplace, OpportunityStatus
from app.models.opportunity import Opportunity
from app.services.providers.base import ProviderCapability
from app.services.providers.registry import get_registry
from app.services.scheduling.base import JobDefinition, Scheduler

logger = get_logger(__name__)

#: Listings refreshed per run. A bound keeps provider spend predictable.
REFRESH_BATCH_SIZE = 25


def refresh_tracked_listings() -> None:
    """Re-poll the listings behind live opportunities whose price has gone stale."""
    asyncio.run(_refresh_tracked_listings())


async def _refresh_tracked_listings() -> None:
    registry = get_registry()
    now = utcnow()
    with session_scope() as session:
        auth = bootstrap(session)
        active = session.scalars(
            select(Opportunity).where(
                Opportunity.organization_id == auth.organization_id,
                Opportunity.status.in_(
                    [
                        OpportunityStatus.NEW.value,
                        OpportunityStatus.REVIEW.value,
                        OpportunityStatus.APPROVED.value,
                    ]
                ),
            )
        ).all()

        listing_ids = {row.source_listing_id for row in active} | {
            row.target_listing_id for row in active
        }
        refreshed = 0
        for listing_id in list(listing_ids)[: REFRESH_BATCH_SIZE * 4]:
            if refreshed >= REFRESH_BATCH_SIZE:
                break
            listing = session.get(MarketplaceListing, listing_id)
            if listing is None:
                continue
            if listing.last_seen_at and is_fresh(
                listing.last_seen_at, settings.ttl_current_price_seconds, now=now
            ):
                continue
            try:
                provider = registry.for_marketplace(
                    Marketplace(listing.marketplace), capability=ProviderCapability.PRODUCT
                )
                raw = await provider.get_product(listing.external_id)
            except Exception as exc:  # noqa: BLE001 - one listing must not stop the batch
                logger.info(
                    "refresh failed",
                    extra={"context": {"listing": listing.external_id, "error": str(exc)}},
                )
                continue
            updated = catalog.upsert_listing(session, auth.organization_id, raw)
            catalog.snapshot_current_price(
                session, auth.organization_id, updated, provider=provider.slug
            )
            refreshed += 1
        logger.info("listing refresh complete", extra={"context": {"refreshed": refreshed}})


def register_jobs(scheduler: Scheduler) -> Scheduler:
    scheduler.add_job(
        JobDefinition(
            key="refresh_tracked_listings",
            func=refresh_tracked_listings,
            interval_seconds=settings.ttl_current_price_seconds,
            description="Re-poll listings behind live opportunities once their price TTL expires.",
        )
    )
    if settings.history_capture_enabled:
        scheduler.add_job(
            JobDefinition(
                key="refresh_universe",
                func=refresh_universe,
                interval_seconds=settings.history_refresh_interval_seconds,
                description=(
                    "Poll the tracked universe so Spreadline accumulates its own history."
                ),
            )
        )
    return scheduler


# --------------------------------------------------------- the tracked universe


def refresh_universe() -> None:
    """Poll the tracked universe so Spreadline keeps accumulating its own history."""
    asyncio.run(_refresh_universe())


async def _refresh_universe() -> dict[str, int]:
    """One bounded pass over the listings that are due.

    Bounded, prioritised and TTL-aware for the same reason throughout: provider
    calls cost money and quota, and a scheduled job that can grow without limit
    is a bill waiting to happen. A listing that keeps failing backs off rather
    than being retried every run.

    Each poll is one provider call whose answer is captured as history whether or
    not anything else is interested in it today. That is the point of the layer:
    the value of owning the data comes from having observed consistently, not
    from having observed when something happened to ask.
    """
    if not settings.history_capture_enabled:
        return {"polled": 0, "observations": 0, "failures": 0}

    registry = get_registry()
    polled = observations = failures = 0
    with session_scope() as session:
        auth = bootstrap(session)
        due = history.due_for_refresh(
            session, auth.organization_id, limit=settings.history_refresh_batch_size
        )
        for tracked in due:
            try:
                provider = registry.for_marketplace(
                    Marketplace(tracked.marketplace), capability=ProviderCapability.PRODUCT
                )
                raw = await provider.get_product(tracked.external_id)
            except Exception as exc:  # noqa: BLE001 - one listing must not stop the batch
                history.record_attempt(tracked, succeeded=False, error=str(exc))
                failures += 1
                continue

            captured = capture.capture_listing(
                session,
                auth.organization_id,
                raw,
                provider=provider.slug,
                is_simulated=not provider.is_live,
                source=capture.SOURCE_REFRESH,
            )
            if captured.errors:
                history.record_attempt(tracked, succeeded=False, error=captured.errors[0])
                failures += 1
                continue

            tracked.listing_id = captured.listing_id or tracked.listing_id
            tracked.product_id = captured.product_id or tracked.product_id
            history.record_attempt(tracked, succeeded=True, observations=captured.total)
            polled += 1
            observations += captured.total

        logger.info(
            "universe refresh complete",
            extra={
                "context": {
                    "due": len(due),
                    "polled": polled,
                    "observations": observations,
                    "failures": failures,
                }
            },
        )
    return {"polled": polled, "observations": observations, "failures": failures}
