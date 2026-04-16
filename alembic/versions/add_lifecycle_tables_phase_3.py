"""add lifecycle tables phase 3

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-16
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_divergence",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("skill_run_id", sa.String(length=36), nullable=False),
        sa.Column("shadow_invocation_id", sa.String(length=36), nullable=False),
        sa.Column("overall_agreement", sa.Float(), nullable=False),
        sa.Column("diff_summary", sa.JSON(), nullable=True),
        sa.Column("flagged_for_evolution", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_run_id"],
            ["skill_run.id"],
            name="fk_divergence_skill_run_id",
        ),
    )
    with op.batch_alter_table("skill_divergence", schema=None) as batch_op:
        batch_op.create_index("ix_skill_divergence_skill_run_id", ["skill_run_id"])
        batch_op.create_index("ix_skill_divergence_created_at", ["created_at"])

    op.create_table(
        "skill_candidate_report",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("capability_name", sa.String(length=200), nullable=True),
        sa.Column("task_pattern_hash", sa.String(length=64), nullable=True),
        sa.Column("expected_savings_usd", sa.Float(), nullable=False),
        sa.Column("volume_30d", sa.Integer(), nullable=False),
        sa.Column("variance_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.create_index("ix_skill_candidate_status", ["status"])
        batch_op.create_index("ix_skill_candidate_reported_at", ["reported_at"])

    op.create_table(
        "skill_evolution_log",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("skill_id", sa.String(length=36), nullable=False),
        sa.Column("from_version_id", sa.String(length=36), nullable=False),
        sa.Column("to_version_id", sa.String(length=36), nullable=True),
        sa.Column("triggered_by", sa.String(length=30), nullable=False),
        sa.Column("claude_invocation_id", sa.String(length=36), nullable=True),
        sa.Column("diagnosis", sa.JSON(), nullable=True),
        sa.Column("targeted_case_ids", sa.JSON(), nullable=True),
        sa.Column("validation_results", sa.JSON(), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skill.id"], name="fk_evo_log_skill_id"),
    )
    with op.batch_alter_table("skill_evolution_log", schema=None) as batch_op:
        batch_op.create_index("ix_evo_log_skill_id", ["skill_id"])


def downgrade() -> None:
    with op.batch_alter_table("skill_evolution_log", schema=None) as batch_op:
        batch_op.drop_index("ix_evo_log_skill_id")
    op.drop_table("skill_evolution_log")

    with op.batch_alter_table("skill_candidate_report", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_candidate_reported_at")
        batch_op.drop_index("ix_skill_candidate_status")
    op.drop_table("skill_candidate_report")

    with op.batch_alter_table("skill_divergence", schema=None) as batch_op:
        batch_op.drop_index("ix_skill_divergence_created_at")
        batch_op.drop_index("ix_skill_divergence_skill_run_id")
    op.drop_table("skill_divergence")
