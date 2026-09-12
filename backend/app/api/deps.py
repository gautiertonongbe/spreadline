"""Request dependencies.

``get_auth`` is the one place the request's organization is decided. Today it
bootstraps the single default tenant; when authentication arrives it reads a
session or token here and every scoped query keeps working unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import AuthContext
from app.domains.tenancy.service import bootstrap
from app.services.providers.registry import ProviderRegistry, get_registry


def get_auth(session: Session = Depends(get_db)) -> AuthContext:
    auth = bootstrap(session)
    session.commit()
    return auth


def get_providers() -> ProviderRegistry:
    return get_registry()


def get_session() -> Iterator[Session]:
    yield from get_db()


DbSession = Annotated[Session, Depends(get_db)]
Auth = Annotated[AuthContext, Depends(get_auth)]
Providers = Annotated[ProviderRegistry, Depends(get_providers)]
