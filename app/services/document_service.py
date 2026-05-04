# app/services/document_service.py
"""
Document service — orchestrates the complete upload pipeline:

  React (image)
      │
      ▼
  FastAPI endpoint  ←─ this service is called here
      │
      ├─ 1. Validate file (type, size)
      ├─ 2. Convert image → PDF  (in memory, pdf_service)
      ├─ 3. Upload PDF to Azure Blob Storage  (blob_service)
      ├─ 4. BEGIN PostgreSQL transaction
      │       INSERT document record
      │       INSERT activity_log record
      │       COMMIT  ──────────────────────────── SUCCESS ✓
      │       ROLLBACK + DELETE blob  ─────────────  FAIL-SAFE ✗
      └─ 5. Return DocumentOut to the React frontend

Also handles: list, get, delete, download, stats.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    DocumentNotFoundError,
    FileTooLargeError,
    TransactionRollbackError,
    UnsupportedFileTypeError,
)
from app.core.logging import get_logger
from app.models.document import (
    ActivityAction,
    ActivityLog,
    Document,
    DocumentCategory,
    DocumentStatus,
)
from app.schemas.document import (
    ActivityItemOut,
    DashboardStatsOut,
    DocumentListResponse,
    DocumentOut,
    StorageItemOut,
    UploadResponse,
)
from app.services import blob_service, pdf_service

logger = get_logger(__name__)


# ── Validation helpers ────────────────────────────────────────────────────────

def _validate_upload(file_bytes: bytes, content_type: str, filename: str) -> None:
    """Raise appropriate exception if the upload is invalid."""
    if len(file_bytes) > settings.max_upload_size_bytes:
        raise FileTooLargeError(
            detail=(
                f"File '{filename}' is {len(file_bytes):,} bytes — "
                f"maximum allowed is {settings.MAX_UPLOAD_SIZE_MB} MB."
            ),
            size=len(file_bytes),
        )

    if content_type not in settings.allowed_image_types_list:
        raise UnsupportedFileTypeError(
            detail=(
                f"File type '{content_type}' is not supported. "
                f"Allowed types: {', '.join(settings.allowed_image_types_list)}"
            ),
            content_type=content_type,
        )


def _parse_tags(tags_raw: str) -> List[str]:
    """
    Parse JSON tags string from form data.
    Accepts: '["invoice","q1"]' or 'invoice,q1' (fallback).
    """
    if not tags_raw or tags_raw.strip() == "":
        return []
    try:
        parsed = json.loads(tags_raw)
        if isinstance(parsed, list):
            return [str(t).strip().lower() for t in parsed if t]
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback: comma-separated plain string
    return [t.strip().lower() for t in tags_raw.split(",") if t.strip()]


# ── Activity log helper ───────────────────────────────────────────────────────

def _create_activity_log(
    action: ActivityAction,
    document: Document,
    user_id: Optional[str],
    user_name: Optional[str],
) -> ActivityLog:
    return ActivityLog(
        id=uuid.uuid4(),
        action=action,
        document_id=document.id,
        document_name=document.original_name,
        document_category=document.category,
        user_id=uuid.UUID(user_id) if user_id and user_id != "dev-user" else None,
        user_name=user_name or "Admin",
        timestamp=datetime.now(timezone.utc),
    )


# ── Core upload pipeline ──────────────────────────────────────────────────────

async def upload_document(
    db: AsyncSession,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    category: str,
    tags_raw: str,
    user_id: Optional[str],
    user_name: Optional[str] = "Admin",
) -> UploadResponse:
    """
    Full upload pipeline with FAIL-SAFE rollback.

    If the DB insert succeeds  → return success.
    If the DB insert fails      → delete the blob from Azure, then raise.
    """

    # ── Step 1: Validate ────────────────────────────────────────────────────
    _validate_upload(file_bytes, content_type, filename)
    tags = _parse_tags(tags_raw)

    logger.info(
        "upload_pipeline_start",
        filename=filename,
        content_type=content_type,
        category=category,
        tags=tags,
        user_id=user_id,
    )

    # ── Step 2: Image → PDF (in memory) ─────────────────────────────────────
    pdf_bytes, page_count = pdf_service.convert_image_to_pdf(file_bytes, content_type)

    # ── Step 3: Upload PDF to Azure Blob ─────────────────────────────────────
    blob_url, blob_name = blob_service.upload_pdf_to_blob(
        pdf_bytes=pdf_bytes,
        original_filename=filename,
        category=category,
    )

    # ── Step 4: PostgreSQL transaction with FAIL-SAFE ────────────────────────
    doc_id = uuid.uuid4()
    pdf_filename = blob_name.split("/")[-1]  # e.g. "a3f8c1d2_invoice.pdf"

    document = Document(
        id=doc_id,
        file_name=pdf_filename,
        original_name=filename,
        blob_url=blob_url,
        blob_name=blob_name,
        category=DocumentCategory(category),
        status=DocumentStatus.uploaded,
        file_size=len(file_bytes),
        pdf_size=len(pdf_bytes),
        page_count=page_count,
        mime_type=content_type,
        tags=tags,
        uploaded_by_id=uuid.UUID(user_id) if user_id and user_id != "dev-user" else None,
        uploaded_by_name=user_name,
        uploaded_at=datetime.now(timezone.utc),
    )

    activity = _create_activity_log(
        action=ActivityAction.upload,
        document=document,
        user_id=user_id,
        user_name=user_name,
    )

    try:
        db.add(document)
        db.add(activity)
        await db.commit()
        await db.refresh(document)

        logger.info(
            "upload_pipeline_success",
            document_id=str(doc_id),
            blob_name=blob_name,
        )

        return UploadResponse(
            success=True,
            message="Document uploaded and stored successfully.",
            document=DocumentOut.from_orm_model(document),
            blob_url=blob_url,
        )

    except Exception as db_exc:
        # ── FAIL-SAFE: DB failed → roll back → delete orphaned blob ──────────
        logger.error(
            "upload_db_failed_rollback",
            document_id=str(doc_id),
            blob_name=blob_name,
            error=str(db_exc),
        )
        await db.rollback()

        # Best-effort blob cleanup — log but don't re-raise blob errors
        try:
            blob_service.delete_blob(blob_name)
            logger.info("fail_safe_blob_deleted", blob_name=blob_name)
        except Exception as blob_exc:
            logger.error(
                "fail_safe_blob_delete_failed",
                blob_name=blob_name,
                error=str(blob_exc),
            )

        raise TransactionRollbackError(
            detail=(
                "Database insertion failed. "
                "The uploaded PDF has been removed from Azure Blob Storage. "
                "No orphaned files remain. Please try again."
            ),
            original_error=str(db_exc),
        )


# ── List documents ────────────────────────────────────────────────────────────

async def list_documents(
    db: AsyncSession,
    category: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
) -> DocumentListResponse:
    """Return filtered, paginated document list."""
    stmt = (
        select(Document)
        .where(Document.is_deleted == False)  # noqa: E712
        .order_by(Document.uploaded_at.desc())
    )

    if category and category != "All":
        stmt = stmt.where(Document.category == DocumentCategory(category))

    if status and status != "All":
        stmt = stmt.where(Document.status == DocumentStatus(status))

    if search:
        ilike = f"%{search}%"
        stmt = stmt.where(
            Document.original_name.ilike(ilike)
            | Document.file_name.ilike(ilike)
        )

    if date_from:
        stmt = stmt.where(Document.uploaded_at >= datetime.fromisoformat(date_from))

    if date_to:
        stmt = stmt.where(Document.uploaded_at <= datetime.fromisoformat(date_to))

    # Total count (for pagination)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    # Apply pagination
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)

    result = await db.execute(stmt)
    documents = result.scalars().all()

    return DocumentListResponse(
        documents=[DocumentOut.from_orm_model(d) for d in documents],
        total=total,
        page=page,
        page_size=page_size,
    )


# ── Get single document ───────────────────────────────────────────────────────

async def get_document(db: AsyncSession, document_id: str) -> Document:
    """Fetch a document by UUID.  Raises DocumentNotFoundError if missing."""
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise DocumentNotFoundError(detail=f"Invalid document ID: {document_id}")

    stmt = select(Document).where(
        Document.id == doc_uuid,
        Document.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(stmt)
    doc = result.scalar_one_or_none()

    if doc is None:
        raise DocumentNotFoundError(
            detail=f"Document '{document_id}' not found.",
            document_id=document_id,
        )
    return doc


# ── Delete document ───────────────────────────────────────────────────────────

async def delete_document(
    db: AsyncSession,
    document_id: str,
    user_id: Optional[str],
    user_name: Optional[str] = "Admin",
) -> None:
    """
    Soft-delete the DB record AND hard-delete the blob from Azure.
    Order: blob first, then DB — if DB fails the blob is gone but record remains
    (preferable to orphaned blobs).
    """
    doc = await get_document(db, document_id)

    # Delete blob from Azure
    blob_service.delete_blob(doc.blob_name)
    logger.info("document_blob_deleted", blob_name=doc.blob_name)

    # Soft-delete in DB
    doc.is_deleted = True
    doc.deleted_at = datetime.now(timezone.utc)

    activity = _create_activity_log(
        action=ActivityAction.delete,
        document=doc,
        user_id=user_id,
        user_name=user_name,
    )

    db.add(activity)
    await db.commit()

    logger.info("document_deleted", document_id=document_id)


# ── Download URL ──────────────────────────────────────────────────────────────

async def get_download_url(
    db: AsyncSession,
    document_id: str,
    user_id: Optional[str],
    user_name: Optional[str] = "Admin",
) -> str:
    """Generate a SAS download URL and log the download activity."""
    doc = await get_document(db, document_id)

    url = blob_service.generate_download_sas_url(doc.blob_name, expires_in_minutes=60)

    activity = _create_activity_log(
        action=ActivityAction.download,
        document=doc,
        user_id=user_id,
        user_name=user_name,
    )
    db.add(activity)
    await db.commit()

    return url


# ── Dashboard stats ───────────────────────────────────────────────────────────

async def get_dashboard_stats(db: AsyncSession) -> DashboardStatsOut:
    """Aggregate stats for the React dashboard page."""

    # Total document count
    total_stmt = select(func.count(Document.id)).where(Document.is_deleted == False)  # noqa: E712
    total_documents: int = (await db.execute(total_stmt)).scalar_one()

    # Documents uploaded this calendar month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_stmt = select(func.count(Document.id)).where(
        Document.is_deleted == False,  # noqa: E712
        Document.uploaded_at >= month_start,
    )
    docs_this_month: int = (await db.execute(month_stmt)).scalar_one()

    # Per-category counts
    cat_stmt = (
        select(Document.category, func.count(Document.id))
        .where(Document.is_deleted == False)  # noqa: E712
        .group_by(Document.category)
    )
    cat_result = await db.execute(cat_stmt)
    category_counts: dict = {
        row[0].value: row[1] for row in cat_result.all()
    }
    # Ensure all categories present
    for cat in ["Sales", "Purchase", "Inventory", "HR", "Finance", "Legal"]:
        category_counts.setdefault(cat, 0)

    # Per-category storage
    storage_stmt = (
        select(Document.category, func.sum(Document.pdf_size), func.count(Document.id))
        .where(Document.is_deleted == False)  # noqa: E712
        .group_by(Document.category)
    )
    storage_result = await db.execute(storage_stmt)
    storage_by_category = [
        StorageItemOut(
            category=row[0].value,
            size=int(row[1] or 0),
            count=int(row[2] or 0),
        )
        for row in storage_result.all()
    ]

    # Total storage (sum of all PDF sizes in DB — fast, no Blob listing)
    total_storage_stmt = select(func.sum(Document.pdf_size)).where(
        Document.is_deleted == False  # noqa: E712
    )
    total_storage: int = (await db.execute(total_storage_stmt)).scalar_one() or 0

    # Recent activity (last 10 entries)
    activity_stmt = (
        select(ActivityLog)
        .order_by(ActivityLog.timestamp.desc())
        .limit(10)
    )
    activity_result = await db.execute(activity_stmt)
    recent_activity = [
        ActivityItemOut.from_orm_model(log)
        for log in activity_result.scalars().all()
    ]

    return DashboardStatsOut(
        totalDocuments=total_documents,
        totalStorage=total_storage,
        documentsThisMonth=docs_this_month,
        categoryCounts=category_counts,
        recentActivity=recent_activity,
        storageByCategory=storage_by_category,
    )