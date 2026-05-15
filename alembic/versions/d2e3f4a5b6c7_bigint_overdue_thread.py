"""widen overdue_thread_map.discord_thread_id to BigInteger

SQLite stores 64-bit integers regardless of declared type, so existing
data is unaffected.  This migration corrects the schema declaration for
Postgres portability (Supabase sync).

Revision ID: d2e3f4a5b6c7
Revises: b1c2d3e4f5a6
Create Date: 2026-05-15 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("overdue_thread_map") as batch_op:
        batch_op.alter_column(
            "discord_thread_id",
            type_=sa.BigInteger(),
            existing_type=sa.Integer(),
        )


def downgrade() -> None:
    with op.batch_alter_table("overdue_thread_map") as batch_op:
        batch_op.alter_column(
            "discord_thread_id",
            type_=sa.Integer(),
            existing_type=sa.BigInteger(),
        )
