"""add calendar_mirror table

Revision ID: b3e7f2a1c954
Revises: 6c29a416f050
Create Date: 2026-03-20 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e7f2a1c954"
down_revision: Union[str, None] = "6c29a416f050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "calendar_mirror",
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("calendar_id", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=False),
        sa.Column("donna_managed", sa.Boolean(), nullable=False),
        sa.Column("donna_task_id", sa.String(length=36), nullable=True),
        sa.Column("etag", sa.String(length=200), nullable=False),
        sa.Column("last_synced", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    with op.batch_alter_table("calendar_mirror", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_calendar_mirror_calendar_id"), ["calendar_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_calendar_mirror_donna_task_id"),
            ["donna_task_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("calendar_mirror", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_calendar_mirror_donna_task_id"))
        batch_op.drop_index(batch_op.f("ix_calendar_mirror_calendar_id"))
    op.drop_table("calendar_mirror")
