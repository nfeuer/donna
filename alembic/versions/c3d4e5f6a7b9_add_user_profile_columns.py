"""Add discord_id and phone columns to users table.

Revision ID: c3d4e5f6a7b9
Revises: b2c3d4e5f6a8
Create Date: 2026-05-12
"""

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b9"
down_revision = "b2c3d4e5f6a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("discord_id", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("phone", sa.String(20), nullable=True))
    op.create_index("idx_users_discord_id", "users", ["discord_id"], unique=True)

    op.execute(
        "UPDATE users SET discord_id = '209121227925618688' "
        "WHERE donna_user_id = 'nick'"
    )


def downgrade() -> None:
    op.drop_index("idx_users_discord_id", table_name="users")
    op.drop_column("users", "phone")
    op.drop_column("users", "discord_id")
