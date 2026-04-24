"""add memory_documents, memory_chunks, vec_memory_chunks

Slice 13: semantic memory index backed by sqlite-vec. Adds two ORM
tables (``memory_documents``, ``memory_chunks``) plus the vec0 virtual
table (``vec_memory_chunks``). The virtual table is created via raw SQL
so the sqlite-vec extension must be loaded onto the migration bind
before `CREATE VIRTUAL TABLE` runs; we do that in-place here so the
rest of the migration stack doesn't need to care.

Revision ID: f4a5b6c7d8e9
Revises: d1e2f3a4b5c6
Create Date: 2026-04-24 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _load_sqlite_vec_on_bind() -> None:
    """Load the vec0 extension on the current migration connection.

    sqlite-vec ships as a loadable extension; `CREATE VIRTUAL TABLE ...
    USING vec0(...)` fails without it. We load it here so the rest of
    the upgrade is self-contained.
    """
    bind = op.get_bind()
    raw = bind.connection.dbapi_connection
    if raw is None:  # pragma: no cover — raw is always present for SQLite
        raw = bind.connection

    import sqlite_vec

    raw.enable_load_extension(True)
    sqlite_vec.load(raw)
    raw.enable_load_extension(False)


def upgrade() -> None:
    op.create_table(
        "memory_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("uri", sa.String(length=1024), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column(
            "sensitive",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "user_id",
            "source_type",
            "source_id",
            name="ux_memory_doc_user_source",
        ),
    )
    op.create_index(
        "ix_memory_doc_user_updated",
        "memory_documents",
        ["user_id", "updated_at"],
    )
    op.create_index(
        "ix_memory_doc_user_deleted",
        "memory_documents",
        ["user_id", "deleted_at"],
    )

    op.create_table(
        "memory_chunks",
        sa.Column("chunk_id", sa.String(length=36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=36),
            sa.ForeignKey("memory_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding_version", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_memory_chunk_doc",
        "memory_chunks",
        ["document_id"],
    )
    op.create_index(
        "ix_memory_chunk_user_version",
        "memory_chunks",
        ["user_id", "embedding_version"],
    )

    _load_sqlite_vec_on_bind()
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory_chunks "
        "USING vec0(chunk_id TEXT PRIMARY KEY, embedding FLOAT[384])"
    )


def downgrade() -> None:
    _load_sqlite_vec_on_bind()
    op.execute("DROP TABLE IF EXISTS vec_memory_chunks")

    op.drop_index("ix_memory_chunk_user_version", table_name="memory_chunks")
    op.drop_index("ix_memory_chunk_doc", table_name="memory_chunks")
    op.drop_table("memory_chunks")

    op.drop_index("ix_memory_doc_user_deleted", table_name="memory_documents")
    op.drop_index("ix_memory_doc_user_updated", table_name="memory_documents")
    op.drop_table("memory_documents")
