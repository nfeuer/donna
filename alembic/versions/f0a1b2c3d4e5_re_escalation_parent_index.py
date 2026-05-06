"""slice 25: index on escalation_request.parent_escalation_id

Adds ``ix_escalation_request_parent_id`` to the ``parent_escalation_id``
column so :meth:`EscalationRepository.find_chain_depth` (a recursive CTE
walking the chain on every re-fire) does not full-scan
``escalation_request``. The column itself was added back in slice 17
(``c7d8e9f0a1b2_escalation_core``) and remained unused — slice 25 is
the first slice to actually persist values into it.

Realizes docs/superpowers/specs/manual-escalation.md §10.6 row 1
(re-estimate + re-escalation), §12 Q5 (max_re_escalation_depth),
slice 25 brief.

Revision ID: f0a1b2c3d4e5
Revises: b2c3d4e5f6a8
Create Date: 2026-05-06
"""

from __future__ import annotations

from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: str | None = "b2c3d4e5f6a8"
branch_labels = None
depends_on = None


INDEX_NAME = "ix_escalation_request_parent_escalation_id"
TABLE_NAME = "escalation_request"
COLUMN_NAME = "parent_escalation_id"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        TABLE_NAME,
        [COLUMN_NAME],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
