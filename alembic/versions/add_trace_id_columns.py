"""Add trace_id and invocation_ids to conversation_messages, trace_id to invocation_log.

Supports the tool-use agent loop trace correlation for Inspector integration.
See docs/superpowers/specs/2026-05-17-quick-chat-tool-agent-design.md §7 and §13.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t1r2a3c4e5i6"
down_revision: Union[str, Sequence[str]] = "b9c8d7e6f5a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("conversation_messages") as batch_op:
        batch_op.add_column(sa.Column("trace_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("invocation_ids", sa.Text(), nullable=True))

    with op.batch_alter_table("invocation_log") as batch_op:
        batch_op.add_column(sa.Column("trace_id", sa.String(36), nullable=True))
        batch_op.create_index("ix_invocation_log_trace_id", ["trace_id"])


def downgrade() -> None:
    with op.batch_alter_table("invocation_log") as batch_op:
        batch_op.drop_index("ix_invocation_log_trace_id")
        batch_op.drop_column("trace_id")

    with op.batch_alter_table("conversation_messages") as batch_op:
        batch_op.drop_column("invocation_ids")
        batch_op.drop_column("trace_id")
