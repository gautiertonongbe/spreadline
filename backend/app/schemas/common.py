"""Shared schema types."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @field_validator("*", mode="before")
    @classmethod
    def _stamp_naive_datetimes_as_utc(cls, value: Any) -> Any:
        """Every timestamp leaves this API with an explicit UTC offset.

        Every datetime Spreadline writes is UTC, but SQLite round-trips a
        ``DateTime(timezone=True)`` column without the tzinfo, so rows read back
        arrive naive and serialise as ``2026-09-12T21:05:45`` with no offset. A
        browser parses that as *local* time: west of UTC it lands hours in the
        future, and "analysed 4 minutes ago" renders as "in 24,515 seconds".

        Stamping it here rather than in each schema means the invariant holds for
        every endpoint, including ones not written yet. It is a boundary
        concern - the wire format has to say what the value means - not a
        per-field one.
        """
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class ErrorDetail(BaseModel):
    code: str
    message: str
    fields: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class MoneyField(BaseModel):
    """Money crosses the wire as a decimal string.

    Serialising a price as a JSON number hands it to a float in every JavaScript
    client, and 0.1 + 0.2 problems in a profit figure are not acceptable.
    """

    amount: Decimal = Field(description="Decimal string, never a float")
    currency: str = "USD"
