"""Signing in and out.

The only routes in the API that do not require an existing session, and the
reason the rest of them can. Everything here is deliberately narrow: there is no
password reset by email, no account self-registration and no way to raise your
own role, because each of those is a way in and none of them is needed by a
product with one operator and a command line.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from app.api.deps import Auth, DbSession, session_token
from app.core.clock import iso_utc
from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError
from app.domains.tenancy import auth as auth_service
from app.models.tenancy import Organization, User
from app.schemas.auth import ChangePasswordRequest, CreateUserRequest, LoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    """Attach the session cookie.

    HttpOnly so a cross-site script cannot read it, SameSite=Lax so it is not
    sent on a cross-site POST, host-only so it is not shared with anything else
    on the domain, and Secure everywhere but plain-HTTP local development, where
    marking it Secure would mean no cookie at all.
    """
    response.set_cookie(
        auth_service.SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )


def _user_payload(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "organization_id": user.organization_id,
        "last_login_at": iso_utc(user.last_login_at),
        #: True for an account that predates authentication. The interface uses
        #: it to insist on a password before anything else.
        "must_set_password": user.password_hash is None,
    }


@router.post("/login")
def login(
    payload: LoginRequest, request: Request, response: Response, session: DbSession
) -> dict[str, Any]:
    """Exchange an email and password for a session.

    Answers identically for an unknown address, a wrong password and a disabled
    account, and does the same work in each case. A login that fails faster for
    an address with no account is a way to ask which addresses have one.
    """
    user = auth_service.authenticate(
        session, email=payload.email, password=payload.password
    )
    started = auth_service.start_session(
        session, user, user_agent=request.headers.get("user-agent")
    )
    session.commit()
    _set_cookie(response, started.token)
    return {
        "user": _user_payload(user),
        "expires_at": iso_utc(started.row.expires_at),
        #: Returned so a script can use the same token as a bearer credential.
        #: A browser never needs to read it and cannot: the cookie is HttpOnly.
        "token": started.token,
    }


@router.post("/logout")
def logout(request: Request, response: Response, session: DbSession) -> dict[str, Any]:
    """End this session. Safe to call without one."""
    token = session_token(request)
    if token:
        auth_service.revoke(session, token)
        session.commit()
    response.delete_cookie(auth_service.SESSION_COOKIE, path="/")
    return {"status": "signed out"}


@router.get("/me")
def me(request: Request, session: DbSession, auth: Auth) -> dict[str, Any]:
    """The signed-in account, and every session it has open.

    Each session says whether it is the one this request arrived on. Without
    that a person deciding which session to end is guessing, and the one they
    most want to keep is the one they are looking at.
    """
    user = session.get(User, auth.user_id)
    if user is None:
        raise NotFoundError("This account no longer exists.")
    token = session_token(request)
    return {
        "user": _user_payload(user),
        "sessions": [
            {
                "id": row.id,
                "created_at": iso_utc(row.created_at),
                "last_seen_at": iso_utc(row.last_seen_at),
                "expires_at": iso_utc(row.expires_at),
                "user_agent": row.user_agent,
                "is_current": auth_service.is_current(row, token),
            }
            for row in auth_service.active_sessions(session, auth)
        ],
    }


@router.post("/password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    session: DbSession,
    auth: Auth,
) -> dict[str, Any]:
    """Change the signed-in account's password.

    Proving the current password is required even though a session is already
    held, because a session is exactly what an attacker would be holding. Every
    other session then ends: a password change is usually a response to a
    suspicion, and leaving the other sessions alive would make it a gesture.
    """
    user = session.get(User, auth.user_id)
    if user is None:
        raise NotFoundError("This account no longer exists.")

    auth_service.set_password(
        session, user, current=payload.current_password, new=payload.new_password
    )
    # The current session went with the others. Issue a fresh one rather than
    # signing the person out of the browser they are standing in front of.
    started = auth_service.start_session(
        session, user, user_agent=request.headers.get("user-agent")
    )
    session.commit()
    _set_cookie(response, started.token)
    return {"status": "password changed", "other_sessions_ended": True}


@router.post("/sessions/{session_id}/revoke")
def revoke_session(session_id: str, session: DbSession, auth: Auth) -> dict[str, Any]:
    """End one of your own sessions."""
    if not auth_service.revoke_by_id(session, auth, session_id):
        raise NotFoundError("No such active session on this account.")
    session.commit()
    return {"status": "revoked", "id": session_id}


@router.post("/users")
def create_user(payload: CreateUserRequest, session: DbSession, auth: Auth) -> dict[str, Any]:
    """Add an account to this organization. Owners only.

    There is no self-registration: an account is created by somebody who already
    has one, or from the command line. A product that manages capital should not
    have a public sign-up form.
    """
    actor = session.get(User, auth.user_id)
    if actor is None or actor.role != "owner":
        raise ForbiddenError("Only an owner may add an account.")

    organization = session.get(Organization, auth.organization_id)
    if organization is None:
        raise NotFoundError("This organization no longer exists.")
    user = auth_service.create_user(
        session,
        organization,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        role=payload.role,
    )
    session.commit()
    return {"user": _user_payload(user)}
