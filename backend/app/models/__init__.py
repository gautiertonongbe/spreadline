"""ORM models.

Imported as a package so ``Base.metadata`` is complete for Alembic autogenerate
and for ``create_all`` in tests.
"""

from app.models.autonomy import (
    AgentPerformanceSnapshot,
    AgentRun,
    AutonomyDecision,
    AutonomyEvent,
    AutonomyGateResult,
    AutonomyPolicy,
    CapitalPosition,
    CircuitBreaker,
    ExecutionInstruction,
)
from app.models.base import Base
from app.models.catalog import (
    MarketplaceListing,
    Offer,
    Product,
    ProductAttribute,
    ProductIdentifier,
)
from app.models.history import TrackedListing
from app.models.identity import ProductMatch
from app.models.observations import (
    CompetitionObservation,
    DemandObservation,
    PriceObservation,
    ProviderHealth,
    ProviderRequest,
)
from app.models.opportunity import (
    Opportunity,
    OpportunityEvent,
    OpportunityValidation,
    ProfitabilitySnapshot,
    RiskAssessment,
)
from app.models.portfolio import CapitalPlan, Outcome, Purchase, Sale
from app.models.tenancy import Organization, User, UserSession, UserSettings

__all__ = [
    "AgentPerformanceSnapshot",
    "AgentRun",
    "AutonomyDecision",
    "AutonomyEvent",
    "AutonomyGateResult",
    "AutonomyPolicy",
    "Base",
    "CapitalPosition",
    "CircuitBreaker",
    "CapitalPlan",
    "CompetitionObservation",
    "DemandObservation",
    "MarketplaceListing",
    "Offer",
    "Opportunity",
    "OpportunityEvent",
    "OpportunityValidation",
    "Organization",
    "Outcome",
    "PriceObservation",
    "ProductAttribute",
    "ProductIdentifier",
    "ProductMatch",
    "Product",
    "ProfitabilitySnapshot",
    "ProviderHealth",
    "ProviderRequest",
    "Purchase",
    "RiskAssessment",
    "Sale",
    "TrackedListing",
    "ExecutionInstruction",
    "User",
    "UserSession",
    "UserSettings",
]
