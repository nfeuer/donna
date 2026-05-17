"""add payload_path to invocation_log

Revision ID: a1b2c3d4e5f6
Revises: 407d0bc1f407
Create Date: 2026-05-16 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9c8d7e6f5a4"
down_revision: Union[str, None] = "407d0bc1f407"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("payload_path", sa.String(300), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.drop_column("payload_path")
