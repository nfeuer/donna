"""add automation.state_blob column (F-W4-D)

Revision ID: f5a1b2c3d4e5
Revises: f3a4b5c6d7e8
Create Date: 2026-04-20
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision = "f5a1b2c3d4e5"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("state_blob", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.drop_column("state_blob")
