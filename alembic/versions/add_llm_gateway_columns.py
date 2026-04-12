"""add LLM gateway columns to invocation_log

Revision ID: e7a3b4c5d692
Revises: d5f9a7c3e281
Create Date: 2026-04-11 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7a3b4c5d692"
down_revision: Union[str, None] = "d5f9a7c3e281"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("queue_wait_ms", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("interrupted", sa.Boolean(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("chain_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("caller", sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.drop_column("caller")
        batch_op.drop_column("chain_id")
        batch_op.drop_column("interrupted")
        batch_op.drop_column("queue_wait_ms")
