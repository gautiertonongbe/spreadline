"""ORM models.

Imported as a package so ``Base.metadata`` is complete for Alembic autogenerate
and for ``create_all`` in tests.
"""

from app.models.base import Base
from app.models.catalog import (
    MarketplaceListing,
    Offer,
    Product,
    ProductAttribute,
    ProductIdentifier,
)
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
from app.models.tenancy import Organization, User, UserSettings

__all__ = [
    "Base",
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
    "User",
    "UserSettings",
]
