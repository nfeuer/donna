"""slice 22: tool_request — gap surfacing table

Adds the ``tool_request`` table that backs the §7 / §8 tool-gap protocol:

- One row per (user, missing tool) gap.
- ``severity ∈ {high, speculative}`` distinguishes the real-time
  Discord ping path from the silent / digest-aggregated path.
- ``detection_point`` records which subsystem raised the gap
  (``capability_tool_check``, ``scheduler_pre_run``,
  ``automation_creation``, ``skill_draft``, ``runtime_dispatch``).
- Partial-unique ``(user_id, tool_name) WHERE status='open'`` index
  encodes the dedup decision: re-emission of the same open gap bumps
  ``priority`` + refreshes ``rationale`` / ``last_seen_at`` rather than
  creating a second row. Resolved or rejected rows allow new emissions
  so historical pattern isn't lost.
- ``snoozed_until`` (column, not separate table) holds the
  ``[Snooze 24h]`` button's quiet-period deadline.
- ``escalation_request_id`` FK links a tool_request to the
  ``claude_code`` escalation that fulfills it (created from the
  ``[File request]`` button). The escalation row references this row
  back via ``originating_entity_type='tool_request'`` /
  ``originating_entity_id``, mirroring the slice-21 skill convention.

Realizes docs/superpowers/specs/manual-escalation.md §7 (protocol),
§8 (schema). Drift vs original §8: the spec listed only id / user_id /
tool_name / proposed_signature / rationale / blocking_capability_id /
priority / status / created_at / resolved_at / resolved_branch.  The
extra columns here (severity, detection_point, snoozed_until,
first_seen_at, last_seen_at, escalation_request_id) are required by the
gap-resolution decisions; spec §8 is updated in the same PR.

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-05-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "b2c3d4e5f6a8"
down_revision: str | None = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_request",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("proposed_signature", sa.JSON(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("blocking_capability_id", sa.String(length=128), nullable=True),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column(
            "severity",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'speculative'"),
        ),
        sa.Column("detection_point", sa.String(length=64), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_branch", sa.String(length=255), nullable=True),
        sa.Column(
            "escalation_request_id",
            sa.Integer(),
            sa.ForeignKey("escalation_request.id"),
            nullable=True,
        ),
        sa.Column("last_pinged_at", sa.DateTime(), nullable=True),
    )
    # Dedup: only one open row per (user, tool). Resolved/rejected rows
    # allow new emissions so historical pattern isn't lost.
    op.create_index(
        "ix_tool_request_open_user_tool",
        "tool_request",
        ["user_id", "tool_name"],
        unique=True,
        sqlite_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_tool_request_status_severity",
        "tool_request",
        ["status", "severity"],
    )
    op.create_index(
        "ix_tool_request_blocking_capability",
        "tool_request",
        ["blocking_capability_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_request_blocking_capability", table_name="tool_request")
    op.drop_index("ix_tool_request_status_severity", table_name="tool_request")
    op.drop_index("ix_tool_request_open_user_tool", table_name="tool_request")
    op.drop_table("tool_request")
