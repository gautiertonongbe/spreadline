"""Schemas for the observation history layer."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.config import settings
from app.domains.history.service import REASON_MANUAL
from app.models.enums import Marketplace
from app.schemas.common import APIModel


class TrackListingRequest(BaseModel):
    """Start observing one listing on one marketplace."""

    marketplace: Marketplace
    external_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(default=REASON_MANUAL, max_length=32)
    priority: int = Field(default=0, ge=0, le=100)
    #: Null means "use the configured default". An explicit value is the
    #: operator overriding the cadence for this one listing.
    refresh_interval_seconds: int | None = Field(default=None, ge=60, le=86_400 * 7)
    notes: str | None = Field(default=None, max_length=1000)

    def universe_limit(self) -> int:
        return settings.history_universe_limit


class TrackedListingResponse(APIModel):
    id: str
    listing_id: str | None = None
    product_id: str | None = None
    marketplace: str
    external_id: str
    reason: str
    priority: int
    is_active: bool
    refresh_interval_seconds: int
    refresh_interval_is_default: bool
    last_refreshed_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int
    observation_count: int
    due_at: datetime | None = None
    notes: str | None = None
