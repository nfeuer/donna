"""add automation.gpu_model and automation.preferred_window

Revision ID: a7b8c9d0e2f3
Revises: 9c6cfd1afb9c
Create Date: 2026-05-13 00:00:00.000000

gpu_model tells the scheduler which GPU model an automation needs.
preferred_window is an optional time window for flexible scheduling
(e.g., "01:00-06:00" for vision tasks that should run overnight).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e2f3"
down_revision: Union[str, None] = "9c6cfd1afb9c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.add_column(sa.Column("gpu_model", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("preferred_window", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.drop_column("preferred_window")
        batch_op.drop_column("gpu_model")
