"""slice 17: escalation_core — escalation_request, daily_budget_extension, dashboard_setting

Realizes docs/superpowers/specs/manual-escalation.md §8 (slice 17 scope).
Schema drift vs the canonical spec is documented in
slices/slice_17_escalation_core.md (Spec drift section): the
escalation_request table gains delivery_status, delivery_attempts, and
last_delivery_attempt_at columns required by the 60s retry loop.

Revision ID: c7d8e9f0a1b2
Revises: c9d1e3f5a7b2
Create Date: 2026-05-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "c9d1e3f5a7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. escalation_request — every over-budget decision and outcome.
    op.create_table(
        "escalation_request",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("task_type", sa.String(length=100), nullable=False),
        sa.Column("estimate_usd", sa.Float(), nullable=False),
        sa.Column("daily_remaining_usd", sa.Float(), nullable=False),
        sa.Column("offered_modes", sa.JSON(), nullable=False),
        sa.Column("resolution", sa.String(length=50), nullable=True),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prompt_path", sa.String(length=500), nullable=True),
        sa.Column("branch_name", sa.String(length=200), nullable=True),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="open",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="2"),
        # Delivery retry bookkeeping (slice 17 drift vs spec §8 — columns
        # required by escalation_delivery_loop). pending|sent|failed.
        sa.Column("delivery_status", sa.String(length=20), nullable=True),
        sa.Column(
            "delivery_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "last_delivery_attempt_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("parent_escalation_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_escalation_id"],
            ["escalation_request.id"],
            name="fk_escalation_request_parent_id",
        ),
    )
    with op.batch_alter_table("escalation_request", schema=None) as batch_op:
        batch_op.create_index(
            "ux_escalation_request_correlation_id",
            ["correlation_id"],
            unique=True,
        )
        batch_op.create_index(
            "ix_escalation_request_user_id", ["user_id"]
        )
        batch_op.create_index(
            "ix_escalation_request_status", ["status"]
        )
        batch_op.create_index(
            "ix_escalation_request_status_delivery",
            ["status", "delivery_status"],
        )

    # 2. daily_budget_extension — created here so escalation_request FK
    # has a target. Behaviourally inert until slice 18.
    op.create_table(
        "daily_budget_extension",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount_usd", sa.Float(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by", sa.String(length=100), nullable=False),
        sa.Column("escalation_request_id", sa.Integer(), nullable=True),
        sa.Column(
            "voided",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.ForeignKeyConstraint(
            ["escalation_request_id"],
            ["escalation_request.id"],
            name="fk_daily_budget_extension_escalation_request_id",
        ),
    )
    with op.batch_alter_table("daily_budget_extension", schema=None) as batch_op:
        batch_op.create_index(
            "ix_daily_budget_extension_user_id_date",
            ["user_id", "date"],
        )

    # 3. dashboard_setting — runtime overrides for YAML defaults.
    # Read-only resolution layer ships with slice 17; write/UI in slice 23.
    op.create_table(
        "dashboard_setting",
        sa.Column("key", sa.String(length=200), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=100), nullable=False),
    )

    # 4. invocation_log — link audit rows to their escalation request.
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("escalation_request_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_invocation_log_escalation_request_id",
            "escalation_request",
            ["escalation_request_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_invocation_log_escalation_request_id",
            ["escalation_request_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("invocation_log", schema=None) as batch_op:
        batch_op.drop_index("ix_invocation_log_escalation_request_id")
        batch_op.drop_constraint(
            "fk_invocation_log_escalation_request_id", type_="foreignkey"
        )
        batch_op.drop_column("escalation_request_id")

    op.drop_table("dashboard_setting")

    with op.batch_alter_table("daily_budget_extension", schema=None) as batch_op:
        batch_op.drop_index("ix_daily_budget_extension_user_id_date")
    op.drop_table("daily_budget_extension")

    with op.batch_alter_table("escalation_request", schema=None) as batch_op:
        batch_op.drop_index("ix_escalation_request_status_delivery")
        batch_op.drop_index("ix_escalation_request_status")
        batch_op.drop_index("ix_escalation_request_user_id")
        batch_op.drop_index("ux_escalation_request_correlation_id")
    op.drop_table("escalation_request")
