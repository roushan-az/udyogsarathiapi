# app/models/document.py
"""
SQLAlchemy ORM models for Udyog Sarathi.

Tables:
  documents        — uploaded PDF records (one row per upload)
  activity_logs    — audit trail of every action
  users            — basic user table (for auth)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


# ── Enums ─────────────────────────────────────────────────────────────────────

import enum


class DocumentCategory(str, enum.Enum):
    Sales     = "Sales"
    Purchase  = "Purchase"
    Inventory = "Inventory"
    HR        = "HR"
    Finance   = "Finance"
    Legal     = "Legal"


class DocumentStatus(str, enum.Enum):
    queued     = "queued"
    processing = "processing"
    uploaded   = "uploaded"
    failed     = "failed"


class ActivityAction(str, enum.Enum):
    upload   = "upload"
    view     = "view"
    download = "download"
    delete   = "delete"


# ── User ──────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # relationships
    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="uploader", lazy="select"
    )
    activity_logs: Mapped[list["ActivityLog"]] = relationship(
        "ActivityLog", back_populates="user", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"


# ── Document ──────────────────────────────────────────────────────────────────

class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # File identifiers
    file_name: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="Stored PDF filename in Blob (e.g. uuid.pdf)"
    )
    original_name: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="Original uploaded image filename"
    )

    # Azure Blob reference — the source of truth for the stored PDF
    blob_url: Mapped[str] = mapped_column(
        String(1000), nullable=False, unique=True,
        comment="Full Azure Blob URL to the PDF"
    )
    blob_name: Mapped[str] = mapped_column(
        String(500), nullable=False,
        comment="Blob object name within the container"
    )

    # Classification
    category: Mapped[DocumentCategory] = mapped_column(
        Enum(DocumentCategory, name="document_category"), nullable=False, index=True
    )
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        nullable=False, default=DocumentStatus.uploaded, index=True
    )

    # File metadata
    file_size: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="Size in bytes of the original image"
    )
    pdf_size: Mapped[int] = mapped_column(
        BigInteger, nullable=True, comment="Size in bytes of the generated PDF"
    )
    page_count: Mapped[int] = mapped_column(
        Integer, nullable=True, default=1
    )
    mime_type: Mapped[str] = mapped_column(
        String(100), nullable=True, comment="Original image MIME type"
    )

    # Tags stored as PostgreSQL array
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list,
        comment="User-defined tags for search/filter"
    )

    # Uploader reference (nullable so we can have anonymous uploads in dev)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    uploaded_by_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Denormalised name for quick display"
    )

    # Timestamps
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    uploader: Mapped["User | None"] = relationship("User", back_populates="documents")
    activity_logs: Mapped[list["ActivityLog"]] = relationship(
        "ActivityLog", back_populates="document", lazy="select", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Document {self.original_name} ({self.category})>"


# ── Activity Log ──────────────────────────────────────────────────────────────

class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    action: Mapped[ActivityAction] = mapped_column(
        Enum(ActivityAction, name="activity_action"), nullable=False, index=True
    )

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True, index=True
    )
    document_name: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="Denormalised name in case document is deleted"
    )
    document_category: Mapped[DocumentCategory] = mapped_column(
        Enum(DocumentCategory, name="document_category"), nullable=False
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Optional extra detail (IP, user agent, etc.)
    extra: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    document: Mapped["Document | None"] = relationship(
        "Document", back_populates="activity_logs"
    )
    user: Mapped["User | None"] = relationship("User", back_populates="activity_logs")

    def __repr__(self) -> str:
        return f"<ActivityLog {self.action} on {self.document_name}>"