"""risk category breakdown

Stores the per-category risk assessment alongside the flat signal list.

The breakdown is persisted rather than derived on read because it records what
could be assessed *at the time*: a category marked "no evidence" is a fact about
what was known when the decision was made, and observations arriving afterwards
must not quietly rewrite the record into something that looks better.

The column is added with a server default so existing rows become an empty list
rather than blocking the migration, and the default is kept: a row inserted
without a breakdown is one with no categories, not one with a null.

Revision ID: db98e7b9c847
Revises: 25274d26d2a9
Create Date: 2026-09-12 14:28:42.173798
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Migrations reference the portable column types (GUID, JSONB) by their module
# path, so the module is always imported.
import app.models.types  # noqa: F401

revision: str = "db98e7b9c847"
down_revision: str | None = "25274d26d2a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("risk_assessments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "categories",
                app.models.types.JSONB(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
    # The default existed only to give existing rows a value. It is dropped
    # again so the column matches the model, which fills it from Python like
    # every other JSON column here; leaving it would show up as schema drift on
    # every `alembic check`.
    with op.batch_alter_table("risk_assessments", schema=None) as batch_op:
        batch_op.alter_column("categories", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("risk_assessments", schema=None) as batch_op:
        batch_op.drop_column("categories")
