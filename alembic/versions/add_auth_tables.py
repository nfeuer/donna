"""add auth tables

Revision ID: a1c9d3e5f701
Revises: 42bdc9502b1b
Create Date: 2026-04-14 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c9d3e5f701"
down_revision: Union[str, None] = "42bdc9502b1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trusted_ips",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ip_address", sa.String(45), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("access_level", sa.String(20), nullable=True),
        sa.Column("trust_duration", sa.String(10), nullable=True),
        sa.Column("trusted_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("verified_by", sa.String(254), nullable=True),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("source", sa.String(20), nullable=False, server_default="web"),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", sa.String(254), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
    )
    op.create_index("idx_trusted_ips_status", "trusted_ips", ["status"])

    op.create_table(
        "verification_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "trust_duration", sa.String(10), nullable=False, server_default="30d"
        ),
    )
    op.create_index(
        "idx_verification_tokens_hash", "verification_tokens", ["token_hash"]
    )

    op.create_table(
        "ip_connections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ip_address", sa.String(45), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("service", sa.String(100), nullable=True),
        sa.Column("action", sa.String(100), nullable=True),
        sa.Column("user_id", sa.String(100), nullable=True),
    )
    op.create_index("idx_ip_connections_ip", "ip_connections", ["ip_address"])
    op.create_index(
        "idx_ip_connections_timestamp", "ip_connections", ["timestamp"]
    )

    op.create_table(
        "allowed_emails",
        sa.Column("email", sa.String(254), primary_key=True),
        sa.Column("immich_user_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("is_admin", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("donna_user_id", sa.String(100), primary_key=True),
        sa.Column("immich_user_id", sa.String(100), nullable=False, unique=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(200), nullable=False, unique=True),
        sa.Column("token_lookup", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("last_seen_ip", sa.String(45), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", sa.String(100), nullable=True),
    )
    op.create_index("idx_device_tokens_user", "device_tokens", ["user_id"])
    op.create_index("idx_device_tokens_expires", "device_tokens", ["expires_at"])
    op.create_index(
        "idx_device_tokens_lookup", "device_tokens", ["token_lookup"], unique=True
    )

    op.create_table(
        "llm_gateway_callers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("caller_id", sa.String(100), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(200), nullable=False),
        sa.Column(
            "monthly_budget_usd",
            sa.Float(),
            nullable=False,
            server_default="0.0",
        ),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_llm_gateway_callers_enabled", "llm_gateway_callers", ["enabled"]
    )


def downgrade() -> None:
    op.drop_table("llm_gateway_callers")
    op.drop_table("device_tokens")
    op.drop_table("users")
    op.drop_table("allowed_emails")
    op.drop_table("ip_connections")
    op.drop_table("verification_tokens")
    op.drop_table("trusted_ips")
