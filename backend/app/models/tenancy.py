"""Organizations, users and per-user settings."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, String, UniqueConstraint
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
    #: Populated only once real authentication exists (spec §37).
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)

    organization: Mapped[Organization] = relationship(back_populates="users")


class UserSettings(Base, UUIDPrimaryKeyMixin, OrganizationScopedMixin, TimestampMixin):
    __tablename__ = "user_settings"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_settings_user_id"),)

    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    #: Fee assumption overrides, scoring weight overrides, decision thresholds,
    #: capital constraints. Stored as JSON because the operator tunes these
    #: constantly and each tweak should not be a schema change.
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False, default=dict)
