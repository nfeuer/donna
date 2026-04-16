"""add skill_run, skill_step_result, skill_fixture tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-15 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[tuple, str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skill_run",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("skill_id", sa.Text(), nullable=False),
        sa.Column("skill_version_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=True),
        sa.Column("automation_run_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("total_latency_ms", sa.Integer(), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=True),
        sa.Column("state_object", sa.JSON(), nullable=False),
        sa.Column("tool_result_cache", sa.JSON(), nullable=True),
        sa.Column("final_output", sa.JSON(), nullable=True),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["skill_id"], ["skill.id"], name="fk_skill_run_skill_id"),
        sa.ForeignKeyConstraint(["skill_version_id"], ["skill_version.id"], name="fk_skill_run_version_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("skill_run", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_skill_run_skill_id"), ["skill_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_skill_run_status"), ["status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_skill_run_started_at"), ["started_at"], unique=False
        )

    op.create_table(
        "skill_step_result",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("skill_run_id", sa.Text(), nullable=False),
        sa.Column("step_name", sa.Text(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_kind", sa.Text(), nullable=False),
        sa.Column("invocation_log_id", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("validation_status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["skill_run_id"], ["skill_run.id"], name="fk_step_result_run_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("skill_step_result", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_skill_step_result_run_id"), ["skill_run_id"], unique=False
        )

    op.create_table(
        "skill_fixture",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("skill_id", sa.Text(), nullable=False),
        sa.Column("case_name", sa.Text(), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("expected_output_shape", sa.JSON(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("captured_run_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skill.id"], name="fk_skill_fixture_skill_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("skill_fixture", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_skill_fixture_skill_id"), ["skill_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("skill_fixture", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_skill_fixture_skill_id"))
    op.drop_table("skill_fixture")

    with op.batch_alter_table("skill_step_result", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_skill_step_result_run_id"))
    op.drop_table("skill_step_result")

    with op.batch_alter_table("skill_run", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_skill_run_started_at"))
        batch_op.drop_index(batch_op.f("ix_skill_run_status"))
        batch_op.drop_index(batch_op.f("ix_skill_run_skill_id"))
    op.drop_table("skill_run")
