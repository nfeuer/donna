"""slice 18: budget_extension_mode — idempotency index on daily_budget_extension

Adds a partial unique index on (escalation_request_id, granted_by) so that
Discord interaction retries cannot double-grant an extension. The table itself
was created complete in slice 17 (c7d8e9f0a1b2); this revision adds only the
idempotency constraint.

Realizes docs/superpowers/specs/manual-escalation.md §10.6 (row 3: Discord 5xx
idempotency) and §5.1 (grant keyed on escalation_request_id).

Revision ID: e2f3a4b5c6d7
Revises: c7d8e9f0a1b2
Create Date: 2026-05-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("daily_budget_extension", schema=None) as batch_op:
        batch_op.create_index(
            "ux_daily_budget_extension_idempotency",
            ["escalation_request_id", "granted_by"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("daily_budget_extension", schema=None) as batch_op:
        batch_op.drop_index("ux_daily_budget_extension_idempotency")
