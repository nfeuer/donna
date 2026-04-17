"""add automation.active_cadence_cron + capability.cadence_policy_override

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-04-17 00:00:02.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.add_column(sa.Column("active_cadence_cron", sa.Text(), nullable=True))
    # Backfill from existing schedule so the proactive-cadence engine has a
    # starting point for automations created before this column existed.
    op.execute(
        "UPDATE automation SET active_cadence_cron = schedule "
        "WHERE active_cadence_cron IS NULL AND schedule IS NOT NULL"
    )

    with op.batch_alter_table("capability", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cadence_policy_override", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("capability", schema=None) as batch_op:
        batch_op.drop_column("cadence_policy_override")
    with op.batch_alter_table("automation", schema=None) as batch_op:
        batch_op.drop_column("active_cadence_cron")
