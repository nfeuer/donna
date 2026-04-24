"""add attendees column to calendar_mirror

Slice 15: meeting-note template writes. The meeting-note skill resolves
attendee names into ``[[People/{name}]]`` wikilinks, so the calendar
mirror must carry attendee metadata. Stored as JSON-encoded
``list[{name, email}]`` in a nullable TEXT column — legacy rows before
this migration remain NULL and the skill treats ``None`` as "no
attendees recorded".

Revision ID: c9d1e3f5a7b2
Revises: f4a5b6c7d8e9
Create Date: 2026-04-24 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d1e3f5a7b2"
down_revision: str | None = "f4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "calendar_mirror",
        sa.Column("attendees", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calendar_mirror", "attendees")
