"""Make immich_user_id nullable for Discord-onboarded users.

Revision ID: e3f4a5b6c7d8
Revises: c3d4e5f6a7b9
Create Date: 2026-05-12
"""

import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "c3d4e5f6a7b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "immich_user_id",
            existing_type=sa.String(100),
            nullable=True,
        )
    # Also make email nullable — Discord-onboarded users won't have one.
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "email",
            existing_type=sa.String(254),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "email",
            existing_type=sa.String(254),
            nullable=False,
        )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "immich_user_id",
            existing_type=sa.String(100),
            nullable=False,
        )
