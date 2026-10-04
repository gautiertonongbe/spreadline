"""Request bodies for recording what was actually bought."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class RecordExecutionRequest(BaseModel):
    """What happened when somebody acted on an instruction.

    ``quantity`` of zero is valid and means nothing was bought, which is an
    outcome rather than an absence of one. ``unit_price`` is then not required,
    and is required whenever anything was.
    """

    quantity: int = Field(ge=0)
    unit_price: Decimal | None = Field(default=None, gt=0)
    order_reference: str | None = Field(default=None, max_length=120)
    shipping_cost: Decimal = Field(default=Decimal("0"), ge=0)
    tax: Decimal = Field(default=Decimal("0"), ge=0)
    other_costs: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class CancelInstructionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
