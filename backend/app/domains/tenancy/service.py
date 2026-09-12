"""Organization and user bootstrap.

v0.1 runs with a single organization and a single operator. The schema and every
query are already multi-tenant, so growing past one is a matter of creating rows
rather than rewriting scopes.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import AuthContext
from app.models.tenancy import Organization, User

DEFAULT_ORG_SLUG = "default"
DEFAULT_ORG_NAME = "Spreadline"
DEFAULT_USER_EMAIL = "operator@spreadline.local"


def get_organization_by_slug(session: Session, slug: str) -> Organization | None:
    return session.scalar(select(Organization).where(Organization.slug == slug))


def ensure_default_organization(session: Session) -> Organization:
    organization = get_organization_by_slug(session, DEFAULT_ORG_SLUG)
    if organization is None:
        organization = Organization(
            name=DEFAULT_ORG_NAME, slug=DEFAULT_ORG_SLUG, base_currency="USD", settings={}
        )
        session.add(organization)
        session.flush()
    return organization


def ensure_default_user(session: Session, organization: Organization) -> User:
    user = session.scalar(
        select(User).where(
            User.organization_id == organization.id, User.email == DEFAULT_USER_EMAIL
        )
    )
    if user is None:
        user = User(
            organization_id=organization.id,
            email=DEFAULT_USER_EMAIL,
            full_name="Operator",
            role="owner",
        )
        session.add(user)
        session.flush()
    return user


def bootstrap(session: Session) -> AuthContext:
    """Create the default tenant if needed and return its auth context."""
    organization = ensure_default_organization(session)
    user = ensure_default_user(session, organization)
    return AuthContext(organization_id=organization.id, user_id=user.id, email=user.email)
