"""capability.tools_json column

Add a nullable TEXT column to ``capability`` that stores the declared
tool-dependency list (JSON-encoded) for Claude-native capabilities.
``SeedCapabilityLoader`` populates it on every boot from
``config/capabilities.yaml``; ``CapabilityToolRegistryCheck`` validates
it against the registered tool registry at startup.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-04-21 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("capability") as batch_op:
        batch_op.add_column(sa.Column("tools_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("capability") as batch_op:
        batch_op.drop_column("tools_json")
