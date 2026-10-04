"""Organizations, users and per-user settings."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, OrganizationScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import JSONB


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Org-wide defaults: fee assumption set, scoring model, decision thresholds.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)

    users: Mapped[list[User]] = relationship(back_populates="organization")


class User(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("organization_id", "email", name="uq_users_org_email"),)

    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="owner")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    #: Consecutive failed sign-ins. Reset by a success, and the only input to
    #: ``locked_until``. Counted on the account rather than on an address
    #: because an attacker controls their address and not the account.
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship(back_populates="users")


class UserSettings(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_settings_user_id"),)

    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    #: Fee assumption overrides, scoring weight overrides, decision thresholds,
    #: capital constraints. Stored as JSON because the operator tunes these
    #: constantly and each tweak should not be a schema change.
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)


class UserSession(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    """One signed-in session.

    Opaque server-side sessions rather than self-contained tokens, deliberately.
    A product whose central discipline is that an emergency stop takes effect at
    once cannot hold a credential it is unable to revoke until it expires: a
    stolen token has to stop working the moment somebody says so, and that means
    the server has to be asked every time.

    Only the hash of the token is stored. A database that leaks must not hand
    the reader a set of live sessions.
    """

    __tablename__ = "user_sessions"

    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    #: SHA-256 of the token. Unique so a lookup is a single indexed read.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(120))
    #: Recorded so a person can recognise their own sessions in a list and spot
    #: one they do not. Truncated; it is a label, not telemetry.
    user_agent: Mapped[str | None] = mapped_column(String(200))
