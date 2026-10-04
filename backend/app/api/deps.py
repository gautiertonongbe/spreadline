"""Request dependencies.

``get_auth`` is the one place the request's identity is decided. Every scoped
query reads ``organization_id`` off the context it returns, so this function is
the whole boundary: there is no route that resolves a tenant its own way.

Two ways in, and no third. A browser sends the session cookie; a script sends
the same token as ``Authorization: Bearer``. Both resolve through the same
function against the same stored session, so a token revoked for one is revoked
for both.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import AuthContext
from app.domains.tenancy import auth as auth_service
from app.services.providers.registry import ProviderRegistry, get_registry


def session_token(request: Request) -> str | None:
    """The caller's token, from the cookie or the Authorization header."""
    cookie = request.cookies.get(auth_service.SESSION_COOKIE)
    if cookie:
        return cookie
    header = request.headers.get("Authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


def get_auth(request: Request, session: Session = Depends(get_db)) -> AuthContext:
    """Who is acting. Raises 401 when nobody is.

    The commit is here because resolving a session writes: it stamps
    ``last_seen_at`` and it revokes a session that has timed out. A read-only
    request still has to be able to end a session that should no longer work.
    """
    context = auth_service.resolve(session, session_token(request))
    session.commit()
    return context


def get_providers() -> ProviderRegistry:
    return get_registry()


def get_session() -> Iterator[Session]:
    yield from get_db()


DbSession = Annotated[Session, Depends(get_db)]
Auth = Annotated[AuthContext, Depends(get_auth)]
Providers = Annotated[ProviderRegistry, Depends(get_providers)]
