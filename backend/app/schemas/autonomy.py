"""Schemas for the autonomy layer.

Every field that widens what the system may do with money is optional and
explicit. There is no request here that raises a limit as a side effect of
something else.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import ExecutionMode, RiskLevel


class PolicyUpdateRequest(BaseModel):
    """A policy change. Unset fields are left as they are.

    ``exclude_unset`` is what makes that work: a field absent from the request
    is untouched, and a field explicitly set to null clears it. The two are
    different instructions and the API keeps them apart.
    """

    version: str | None = Field(default=None, max_length=32)
    autonomy_level: int | None = Field(default=None, ge=0, le=7)
    capital_limit: Decimal | None = Field(default=None, ge=0)
    max_position_size: Decimal | None = Field(default=None, ge=0)
    max_position_pct: Decimal | None = Field(default=None, gt=0, le=1)
    max_loss_per_position: Decimal | None = Field(default=None, ge=0)
    max_daily_deployment: Decimal | None = Field(default=None, ge=0)
    minimum_roi: Decimal | None = Field(default=None, ge=0)
    minimum_profit: Decimal | None = Field(default=None, ge=0)
    maximum_risk: RiskLevel | None = None
    minimum_match_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    minimum_data_quality: Decimal | None = Field(default=None, ge=0, le=100)
    maximum_inventory_age_days: int | None = Field(default=None, gt=0, le=3650)
    maximum_category_exposure: Decimal | None = Field(default=None, gt=0, le=1)
    maximum_brand_exposure: Decimal | None = Field(default=None, gt=0, le=1)
    maximum_marketplace_exposure: Decimal | None = Field(default=None, gt=0, le=1)
    require_human_approval: bool | None = None
    emergency_stop_enabled: bool | None = None
    execution_mode: ExecutionMode | None = None
    note: str | None = Field(default=None, max_length=2000)


class EnableAutonomyRequest(BaseModel):
    """Authorise a level of autonomous capital.

    Deliberately verbose. Turning this on is the single most consequential
    action in the product, and it should not be possible to do it by accident or
    by a default.
    """

    level: int = Field(ge=3, le=7, description="Levels below 3 do not deploy capital.")
    #: Defaults to the ladder's figure for that level.
    capital_limit: Decimal | None = Field(default=None, ge=0)
    #: Shadow records positions without buying anything. It is the honest place
    #: to start, and the default here for that reason.
    execution_mode: ExecutionMode = ExecutionMode.SHADOW
    #: Proceed even though the gate says the criteria are not met. A legitimate
    #: thing for an owner of capital to decide, and recorded as such.
    acknowledge_not_eligible: bool = False
    note: str | None = Field(default=None, max_length=1000)


class EmergencyStopRequest(BaseModel):
    active: bool
    reason: str = Field(min_length=1, max_length=500)


class RunDecisionRequest(BaseModel):
    opportunity_id: str
    #: Caps this decision below the policy limit. Never above it: the policy is
    #: the ceiling and a request cannot raise it.
    available_capital: Decimal | None = Field(default=None, ge=0)


class BacktestRequest(BaseModel):
    """Replay the active policy over recorded history.

    ``starting_capital`` lets a replay be run at a size the policy does not
    currently authorise, which is the point: the question is usually "what would
    $500 have done", asked before authorising $500.
    """

    days: int = Field(default=90, ge=7, le=730)
    starting_capital: Decimal | None = Field(default=None, ge=0)
    #: Days between simulated decision points. One is the honest default; a
    #: larger step is faster and coarser, and will miss short-lived prices.
    step_days: int = Field(default=1, ge=1, le=30)


class AllocationRequest(BaseModel):
    """Commit an allocation plan.

    The plan is recomputed server-side rather than accepted from the client. A
    client that could submit its own lines could submit a quantity nobody
    proposed, and the budget is the only thing worth carrying across.
    """

    budget: Decimal | None = Field(default=None, ge=0)
    #: The plan the person saw, by its capital total. If the recomputed plan
    #: differs the commit is refused rather than silently acting on a different
    #: one: prices move between looking and deciding.
    expect_capital: Decimal | None = Field(default=None, ge=0)
