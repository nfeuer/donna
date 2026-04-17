"""add automation + automation_run tables (phase 5)

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-16
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("capability_name", sa.String(length=200), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("schedule", sa.String(length=200), nullable=True),
        sa.Column("alert_conditions", sa.JSON(), nullable=False),
        sa.Column("alert_channels", sa.JSON(), nullable=False),
        sa.Column("max_cost_per_run_usd", sa.Float(), nullable=True),
        sa.Column("min_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_via", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["capability_name"], ["capability.name"],
            name="fk_automation_capability_name",
        ),
    )
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.create_index("ix_automation_user_id", ["user_id"])
        batch_op.create_index("ix_automation_status", ["status"])
        batch_op.create_index("ix_automation_next_run_at", ["next_run_at"])
        batch_op.create_index("ix_automation_capability_name", ["capability_name"])

    op.create_table(
        "automation_run",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("automation_id", sa.String(length=36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("execution_path", sa.String(length=20), nullable=False),
        sa.Column("skill_run_id", sa.String(length=36), nullable=True),
        sa.Column("invocation_log_id", sa.String(length=36), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("alert_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alert_content", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["automation_id"], ["automation.id"],
            name="fk_automation_run_automation_id",
        ),
    )
    with op.batch_alter_table("automation_run", schema=None) as batch_op:
        batch_op.create_index("ix_automation_run_automation_id", ["automation_id"])
        batch_op.create_index("ix_automation_run_started_at", ["started_at"])
        batch_op.create_index("ix_automation_run_status", ["status"])


def downgrade() -> None:
    with op.batch_alter_table("automation_run", schema=None) as batch_op:
        batch_op.drop_index("ix_automation_run_status")
        batch_op.drop_index("ix_automation_run_started_at")
        batch_op.drop_index("ix_automation_run_automation_id")
    op.drop_table("automation_run")

    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.drop_index("ix_automation_capability_name")
        batch_op.drop_index("ix_automation_next_run_at")
        batch_op.drop_index("ix_automation_status")
        batch_op.drop_index("ix_automation_user_id")
    op.drop_table("automation")
