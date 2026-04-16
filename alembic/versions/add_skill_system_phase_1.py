"""add skill system phase 1 tables

Revision ID: a1b2c3d4e5f6
Revises: f1b8c2d4e703, f8b2d4e6a913
Create Date: 2026-04-15 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[tuple, str, None] = ("f1b8c2d4e703", "f8b2d4e6a913")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "capability",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema", sa.Text(), nullable=False),
        sa.Column("trigger_type", sa.Text(), nullable=False),
        sa.Column("default_output_shape", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    with op.batch_alter_table("capability", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_capability_status"), ["status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_capability_trigger_type"), ["trigger_type"], unique=False
        )

    op.create_table(
        "skill",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("capability_name", sa.Text(), nullable=False),
        sa.Column("current_version_id", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("requires_human_gate", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("baseline_agreement", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["capability_name"], ["capability.name"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("capability_name"),
    )
    with op.batch_alter_table("skill", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_skill_state"), ["state"], unique=False
        )

    op.create_table(
        "skill_version",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("skill_id", sa.Text(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("yaml_backbone", sa.Text(), nullable=False),
        sa.Column("step_content", sa.Text(), nullable=False),
        sa.Column("output_schemas", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skill.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("skill_version", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_skill_version_skill_id"), ["skill_id"], unique=False
        )

    op.create_table(
        "skill_state_transition",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("skill_id", sa.Text(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=False),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("at", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["skill_id"], ["skill.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("skill_state_transition", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_skill_state_transition_skill_id"), ["skill_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("skill_state_transition", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_skill_state_transition_skill_id"))
    op.drop_table("skill_state_transition")

    with op.batch_alter_table("skill_version", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_skill_version_skill_id"))
    op.drop_table("skill_version")

    with op.batch_alter_table("skill", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_skill_state"))
    op.drop_table("skill")

    with op.batch_alter_table("capability", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_capability_trigger_type"))
        batch_op.drop_index(batch_op.f("ix_capability_status"))
    op.drop_table("capability")
