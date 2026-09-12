"""Declarative base and shared mixins."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.models.types import GUID, new_uuid

#: Explicit naming so Alembic emits stable constraint names and SQLite
#: batch_alter_table migrations can find them.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class OrganizationScopedMixin:
    """Multi-tenant scoping.

    v0.1 has a single organization, but every tenant-owned row carries its
    organization from day one so that adding the second one is a data problem
    rather than a migration of every table in the schema.
    """

    @declared_attr
    def organization_id(cls) -> Mapped[str]:  # noqa: N805
        return mapped_column(
            GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
        )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


def org_index(table_name: str, *columns: str, unique: bool = False) -> Index:
    """The (organization_id, …) composite index every tenant-scoped scan needs."""
    name = f"ix_{table_name}_org_" + "_".join(columns)
    return Index(name, "organization_id", *columns, unique=unique)
