# alembic/versions/001_initial_schema.py
"""Initial schema — users, documents, activity_logs

Revision ID: 001_initial
Revises:
Create Date: 2024-03-01 00:00:00.000000 UTC
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── ENUMs ─────────────────────────────────────────────────────────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE document_category AS ENUM
                ('Sales','Purchase','Inventory','HR','Finance','Legal');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE document_status AS ENUM
                ('queued','processing','uploaded','failed');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    op.execute("""
        DO $$ BEGIN
            CREATE TYPE activity_action AS ENUM
                ('upload','view','download','delete');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id",              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email",           sa.String(255), nullable=False),
        sa.Column("full_name",       sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active",       sa.Boolean(),   nullable=False, server_default="true"),
        sa.Column("is_superuser",    sa.Boolean(),   nullable=False, server_default="false"),
        sa.Column("created_at",      sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",      sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── documents ─────────────────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id",               postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("file_name",        sa.String(500), nullable=False),
        sa.Column("original_name",    sa.String(500), nullable=False),
        sa.Column("blob_url",         sa.String(1000), nullable=False),
        sa.Column("blob_name",        sa.String(500), nullable=False),
        sa.Column("category",         postgresql.ENUM(
            "Sales","Purchase","Inventory","HR","Finance","Legal",
            name="document_category", create_type=False
        ), nullable=False),
        sa.Column("status",           postgresql.ENUM(
            "queued","processing","uploaded","failed",
            name="document_status", create_type=False
        ), nullable=False, server_default="uploaded"),
        sa.Column("file_size",        sa.BigInteger(), nullable=False),
        sa.Column("pdf_size",         sa.BigInteger(), nullable=True),
        sa.Column("page_count",       sa.Integer(),    nullable=True, server_default="1"),
        sa.Column("mime_type",        sa.String(100),  nullable=True),
        sa.Column("tags",             postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("uploaded_by_id",   postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_by_name", sa.String(255),  nullable=True),
        sa.Column("uploaded_at",      sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at",       sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_deleted",       sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at",       sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_documents_category",    "documents", ["category"])
    op.create_index("ix_documents_status",      "documents", ["status"])
    op.create_index("ix_documents_uploaded_at", "documents", ["uploaded_at"])
    op.create_index("ix_documents_uploaded_by", "documents", ["uploaded_by_id"])
    op.create_index("ix_documents_blob_url",    "documents", ["blob_url"], unique=True)

    # ── activity_logs ─────────────────────────────────────────────────────────
    op.create_table(
        "activity_logs",
        sa.Column("id",                postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("action",            postgresql.ENUM(
            "upload","view","download","delete",
            name="activity_action", create_type=False
        ), nullable=False),
        sa.Column("document_id",       postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_name",     sa.String(500), nullable=False),
        sa.Column("document_category", postgresql.ENUM(
            "Sales","Purchase","Inventory","HR","Finance","Legal",
            name="document_category", create_type=False
        ), nullable=False),
        sa.Column("user_id",           postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_name",         sa.String(255), nullable=True),
        sa.Column("timestamp",         sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("extra",             sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"],     ["users.id"],     ondelete="SET NULL"),
    )
    op.create_index("ix_activity_logs_action",      "activity_logs", ["action"])
    op.create_index("ix_activity_logs_document_id", "activity_logs", ["document_id"])
    op.create_index("ix_activity_logs_timestamp",   "activity_logs", ["timestamp"])


def downgrade() -> None:
    op.drop_table("activity_logs")
    op.drop_table("documents")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS activity_action")
    op.execute("DROP TYPE IF EXISTS document_status")
    op.execute("DROP TYPE IF EXISTS document_category")