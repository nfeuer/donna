"""add context-budget columns to invocation_log

Revision ID: f1b8c2d4e703
Revises: e7a3b4c5d692
Create Date: 2026-04-12 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1b8c2d4e703"
down_revision: Union[str, None] = "e7a3b4c5d692"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("estimated_tokens_in", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "overflow_escalated",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.drop_column("overflow_escalated")
        batch_op.drop_column("estimated_tokens_in")
