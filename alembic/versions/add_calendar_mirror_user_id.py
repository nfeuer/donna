"""add user_id to calendar_mirror

Adds the user_id column to the calendar_mirror table to complete multi-user
support. All existing rows are backfilled with "nick" (the single user in
Phase 1–3). Future users will have their events inserted with the correct
user_id from the start.

See docs/architecture.md (App Architecture — Phase 4) and
src/donna/tasks/db_models.py (CalendarMirror).

Revision ID: d5f1a9c3e827
Revises: c4d8e3b2f165
Create Date: 2026-04-03 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5f1a9c3e827"
down_revision: Union[str, None] = "c4d8e3b2f165"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("calendar_mirror", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "user_id",
                sa.String(length=100),
                nullable=False,
                server_default="nick",
            )
        )
        batch_op.create_index(
            batch_op.f("ix_calendar_mirror_user_id"), ["user_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("calendar_mirror", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_calendar_mirror_user_id"))
        batch_op.drop_column("user_id")
