"""Shared schema types."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


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
