"""add overdue_thread_map table

Persists Discord thread_id → task_id mapping so overdue reply routing
survives container restarts.  Previously the mapping lived only in
DonnaBot.overdue_threads (an in-memory dict) and was lost on every
restart, silently dropping all user replies to older overdue threads.

Revision ID: b1c2d3e4f5a6
Revises: a7b8c9d0e2f3
Create Date: 2026-05-14 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a7b8c9d0e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "overdue_thread_map",
        sa.Column("discord_thread_id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
    )
    op.create_index(
        "idx_overdue_thread_map_task",
        "overdue_thread_map",
        ["task_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_overdue_thread_map_task", table_name="overdue_thread_map")
    op.drop_table("overdue_thread_map")
