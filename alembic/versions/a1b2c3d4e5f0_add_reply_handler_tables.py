"""add thread_memory, pending_action_plan, capability_gap

Universal Reply Handler tables. thread_memory stores per-thread
conversation history for LLM context. pending_action_plan tracks
proposed action plans awaiting user confirmation. capability_gap
logs requests Donna cannot handle yet.

Revision ID: a1b2c3d4e5f0
Revises: f4a5b6c7d8e9
Create Date: 2026-05-12 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f0"
down_revision: str | None = "f4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "thread_memory",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("context_type", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
    )
    op.create_index(
        "idx_thread_memory_thread",
        "thread_memory",
        ["thread_id", "created_at"],
    )

    op.create_table(
        "pending_action_plan",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("actions_json", sa.Text(), nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.String(length=64), nullable=False),
    )
    op.create_index(
        "idx_pending_plan_thread",
        "pending_action_plan",
        ["thread_id", "status"],
    )

    op.create_table(
        "capability_gap",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_request", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("context_type", sa.String(length=32), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="logged",
        ),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("last_hit_at", sa.String(length=64), nullable=False),
    )


def downgrade() -> None:
    op.drop_index("idx_pending_plan_thread", table_name="pending_action_plan")
    op.drop_index("idx_thread_memory_thread", table_name="thread_memory")
    op.drop_table("capability_gap")
    op.drop_table("pending_action_plan")
    op.drop_table("thread_memory")
