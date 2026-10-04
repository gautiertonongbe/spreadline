"""Signing in, and staying signed in.

Until now every request resolved to the single default operator, which was a
seam rather than a security model. This is the body that fills it.

Four decisions shape the module, and each of them costs something:

**Sessions are opaque and server-side.** A self-contained token (a JWT, say) is
cheaper to check and impossible to withdraw: it stays valid until it expires, no
matter what anyone decides in the meantime. That is the wrong trade for a
product whose central discipline is that an emergency stop takes effect at once.
Every request costs one indexed read, and in exchange a session can be ended the
moment somebody says so.

**Only the hash of the token is stored.** A database that leaks must not hand
the reader a set of live sessions. SHA-256 rather than a slow KDF is correct
*here* and only here: the token is 256 bits of randomness from the system CSPRNG,
so there is no guessable input for a slow hash to protect. Passwords, which are
guessable, go through PBKDF2 at 240,000 iterations.

**A failed sign-in is counted against the account, not the address.** An
attacker chooses their address and cannot choose the account, so per-address
counting is a lock that the attacker holds the key to. The cost is that somebody
who can guess an email can lock its owner out for a few minutes, which is the
lesser harm and is why the lockout is minutes rather than permanent.

**The failure message never says which half was wrong.** "No such account" and
"wrong password" are the same sentence here, and the work done is the same
either way: a login that returns faster for an unknown address is an endpoint
that answers "does this person have an account", which is not a question the
server should answer to an unauthenticated caller.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import ensure_utc, utcnow
from app.core.config import settings
from app.core.errors import AuthenticationError, ValidationError
from app.core.security import AuthContext, hash_secret, verify_secret
from app.models.tenancy import Organization, User, UserSession

#: The cookie the browser holds. Host-only, HttpOnly and SameSite=Lax.
SESSION_COOKIE = "spreadline_session"

#: Shortest password accepted. Length is the only property that reliably
#: survives contact with a real person; composition rules mostly produce
#: "Password1!" and a sticky note.
MINIMUM_PASSWORD_LENGTH = 12

#: Consecutive failures before the account stops accepting attempts, and for
#: how long. Short enough that a locked-out owner is inconvenienced rather than
#: stranded, long enough that online guessing is hopeless.
MAX_FAILED_ATTEMPTS = 8
LOCKOUT_MINUTES = 15

#: How often ``last_seen_at`` is written. A write on every request would turn
#: every read of the API into a write of the database for no added safety.
LAST_SEEN_RESOLUTION_SECONDS = 60


@dataclass(frozen=True)
class StartedSession:
    """A new session, and the one time its token is ever available."""

    token: str
    row: UserSession


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_password_policy(password: str) -> None:
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise ValidationError(
            f"A password must be at least {MINIMUM_PASSWORD_LENGTH} characters."
        )
    if password.strip() != password:
        raise ValidationError("A password cannot begin or end with whitespace.")


# ------------------------------------------------------------------ users


def create_user(
    session: Session,
    organization: Organization,
    *,
    email: str,
    password: str,
    full_name: str | None = None,
    role: str = "owner",
) -> User:
    """Add a user with a password. The only way an account gets one."""
    check_password_policy(password)
    address = email.strip().lower()
    if not address or "@" not in address:
        raise ValidationError("A valid email address is required.")

    existing = session.scalar(
        select(User).where(
            User.organization_id == organization.id, User.email == address
        )
    )
    if existing is not None:
        raise ValidationError(f"{address} already has an account in this organization.")

    user = User(
        organization_id=organization.id,
        email=address,
        full_name=full_name,
        role=role,
        password_hash=hash_secret(password),
    )
    session.add(user)
    session.flush()
    return user


def reset_password(session: Session, user: User, *, new: str, reason: str) -> int:
    """Replace a password without proving the old one.

    Administrative, and only reachable by somebody who already has database
    access: it is how an account locked out of itself gets back in. It is not
    exposed through the API, because through the API the current password is the
    only thing separating a stolen session from a stolen account.

    Returns the number of sessions ended, which is all of them.
    """
    check_password_policy(new)
    user.password_hash = hash_secret(new)
    user.failed_attempts = 0
    user.locked_until = None
    session.flush()
    return revoke_all(session, user, reason=reason)


def set_password(session: Session, user: User, *, current: str | None, new: str) -> User:
    """Change a password, proving the old one first.

    ``current`` may be None only for an account that has never had a password,
    which is the account created before authentication existed. Changing a set
    password always requires the set password: a session alone is not proof
    enough, because a session is exactly what an attacker would be holding.
    """
    if user.password_hash is not None:
        if current is None or not verify_secret(current, user.password_hash):
            raise AuthenticationError("The current password is not correct.")
    check_password_policy(new)
    user.password_hash = hash_secret(new)
    session.flush()
    # Every other session belonging to this user ends. A password change is
    # usually a response to a suspicion, and leaving the other sessions alive
    # would make it a gesture.
    revoke_all(session, user, reason="password changed", keep_token=None)
    return user


# ------------------------------------------------------------------ sign in


def authenticate(session: Session, *, email: str, password: str) -> User:
    """Check an email and password, or raise.

    Raises the same error for an unknown address, a wrong password and an
    inactive account. The caller cannot tell them apart, which is the point.
    """
    address = email.strip().lower()
    user = session.scalar(select(User).where(User.email == address))
    now = utcnow()

    if user is None:
        # Hash anyway. A login that returns faster for an unknown address is a
        # way to ask the server which addresses have accounts.
        hash_secret(password)
        raise AuthenticationError("Those credentials are not correct.")

    if user.locked_until is not None and ensure_utc(user.locked_until) > now:
        minutes = max(1, int((ensure_utc(user.locked_until) - now).total_seconds() // 60) + 1)
        raise AuthenticationError(
            f"Too many failed attempts. This account is locked for {minutes} more minute(s)."
        )

    if not user.is_active or user.password_hash is None:
        hash_secret(password)
        raise AuthenticationError("Those credentials are not correct.")

    if not verify_secret(password, user.password_hash):
        user.failed_attempts += 1
        if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
        session.flush()
        raise AuthenticationError("Those credentials are not correct.")

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    session.flush()
    return user


def start_session(
    session: Session, user: User, *, user_agent: str | None = None
) -> StartedSession:
    """Open a session and return its token, once.

    The token is returned here and never again: only its hash is stored, so
    there is nothing to read back later.
    """
    token = secrets.token_urlsafe(32)
    row = UserSession(
        organization_id=user.organization_id,
        user_id=user.id,
        token_hash=_digest(token),
        expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours),
        last_seen_at=utcnow(),
        user_agent=(user_agent or "")[:200] or None,
    )
    session.add(row)
    session.flush()
    return StartedSession(token=token, row=row)


def resolve(session: Session, token: str | None) -> AuthContext:
    """Turn a token into who is acting, or raise.

    Idle timeout as well as absolute expiry: a session left open on a machine
    somebody walked away from should not stay usable for its whole lifetime.
    """
    if not token:
        raise AuthenticationError("Sign in to continue.")

    row = session.scalar(select(UserSession).where(UserSession.token_hash == _digest(token)))
    if row is None or row.revoked_at is not None:
        raise AuthenticationError("This session is no longer valid. Sign in again.")

    now = utcnow()
    if ensure_utc(row.expires_at) <= now:
        raise AuthenticationError("This session has expired. Sign in again.")

    idle_limit = timedelta(minutes=settings.session_idle_timeout_minutes)
    last_seen = ensure_utc(row.last_seen_at) if row.last_seen_at else ensure_utc(row.created_at)
    if now - last_seen > idle_limit:
        row.revoked_at = now
        row.revoked_reason = "idle timeout"
        session.flush()
        raise AuthenticationError("This session timed out. Sign in again.")

    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        row.revoked_at = now
        row.revoked_reason = "account is not active"
        session.flush()
        raise AuthenticationError("This session is no longer valid. Sign in again.")

    if (now - last_seen).total_seconds() >= LAST_SEEN_RESOLUTION_SECONDS:
        row.last_seen_at = now
        session.flush()

    return AuthContext(
        organization_id=row.organization_id, user_id=user.id, email=user.email
    )


def revoke(session: Session, token: str, *, reason: str = "signed out") -> None:
    row = session.scalar(select(UserSession).where(UserSession.token_hash == _digest(token)))
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
        row.revoked_reason = reason[:120]
        session.flush()


def revoke_by_id(session: Session, auth: AuthContext, session_id: str) -> bool:
    """End one of this user's own sessions. Never somebody else's."""
    row = session.scalar(
        select(UserSession).where(
            UserSession.id == session_id,
            UserSession.organization_id == auth.organization_id,
            UserSession.user_id == auth.user_id,
        )
    )
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = utcnow()
    row.revoked_reason = "revoked by the account holder"
    session.flush()
    return True


def revoke_all(
    session: Session, user: User, *, reason: str, keep_token: str | None = None
) -> int:
    """End every session for a user, optionally sparing the current one."""
    keep = _digest(keep_token) if keep_token else None
    rows = session.scalars(
        select(UserSession).where(
            UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
        )
    ).all()
    ended = 0
    for row in rows:
        if keep is not None and row.token_hash == keep:
            continue
        row.revoked_at = utcnow()
        row.revoked_reason = reason[:120]
        ended += 1
    session.flush()
    return ended


def is_current(row: UserSession, token: str | None) -> bool:
    """Whether this row is the session the request arrived on."""
    return token is not None and row.token_hash == _digest(token)


def active_sessions(session: Session, auth: AuthContext) -> list[UserSession]:
    """This user's live sessions, newest first."""
    now = utcnow()
    rows = session.scalars(
        select(UserSession)
        .where(
            UserSession.organization_id == auth.organization_id,
            UserSession.user_id == auth.user_id,
            UserSession.revoked_at.is_(None),
        )
        .order_by(UserSession.created_at.desc())
    ).all()
    return [row for row in rows if ensure_utc(row.expires_at) > now]


def purge_expired(session: Session, *, before_days: int = 30) -> int:
    """Delete sessions that ended long ago.

    Revoked and expired rows are kept for a while on purpose: "when did that
    session end, and why" is a question worth being able to answer. Keeping them
    forever is not.
    """
    cutoff = utcnow() - timedelta(days=before_days)
    rows = session.scalars(select(UserSession).where(UserSession.expires_at < cutoff)).all()
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)
