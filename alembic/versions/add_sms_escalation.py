"""add sms escalation state and hard_expires_at

Revision ID: c4d8e3b2f165
Revises: b3e7f2a1c954
Create Date: 2026-03-20 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d8e3b2f165"
down_revision: Union[str, None] = "b3e7f2a1c954"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add hard_expires_at to conversation_context (nullable — existing rows have no value).
    with op.batch_alter_table("conversation_context", schema=None) as batch_op:
        batch_op.add_column(sa.Column("hard_expires_at", sa.DateTime(), nullable=True))

    # Create escalation_state table.
    op.create_table(
        "escalation_state",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("task_title", sa.String(length=500), nullable=False),
        sa.Column("current_tier", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("next_escalation_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("escalation_state", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_escalation_state_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_escalation_state_task_id"), ["task_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("escalation_state", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_escalation_state_task_id"))
        batch_op.drop_index(batch_op.f("ix_escalation_state_user_id"))
    op.drop_table("escalation_state")

    with op.batch_alter_table("conversation_context", schema=None) as batch_op:
        batch_op.drop_column("hard_expires_at")
