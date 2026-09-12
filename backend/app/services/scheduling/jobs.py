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
    return scheduler
