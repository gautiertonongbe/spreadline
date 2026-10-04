"""Portable column types.

The production database is PostgreSQL. SQLite is supported for tests and
first-run development, so the few types that differ between them are wrapped
once here rather than being special-cased in each model.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import CHAR, JSON, Numeric, String, TypeDecorator
from sqlalchemy.dialects import postgresql

#: Money columns. 14 integer+fraction digits with 4 decimal places holds any
#: realistic unit price, fee or position size without float drift.
Money = Numeric(14, 4)
#: Ratios (ROI, margin, weights). 6 decimal places so 0.0001 differences survive.
Ratio = Numeric(12, 6)


class GUID(TypeDecorator):
    """UUID on PostgreSQL, CHAR(36) elsewhere; always a ``str`` in Python."""

    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.UUID(as_uuid=False))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect) -> str | None:  # type: ignore[no-untyped-def]
        if value is None:
            return None
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            # A value that is not a UUID is passed through rather than raised on.
            # Every id in this schema arrives from a URL, and a malformed one
            # should match no row and produce the route's own 404, not a 500
            # from inside the type layer on the way to the query.
            return str(value)

    def process_result_value(self, value: Any, dialect) -> str | None:  # type: ignore[no-untyped-def]
        if value is None:
            return None
        return str(value)


class JSONB(TypeDecorator):
    """JSONB on PostgreSQL, JSON elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.JSONB())
        return dialect.type_descriptor(JSON())


class DecimalString(TypeDecorator):
    """A Decimal stored as text, for JSON-adjacent values needing exactness."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> str | None:  # type: ignore[no-untyped-def]
        return None if value is None else str(value)

    def process_result_value(self, value: Any, dialect) -> Decimal | None:  # type: ignore[no-untyped-def]
        return None if value is None else Decimal(value)


def new_uuid() -> str:
    return str(uuid.uuid4())
