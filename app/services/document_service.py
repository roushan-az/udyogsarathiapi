import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy import func, select, or_
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


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_upload(file_bytes: bytes, content_type: str, filename: str) -> None:
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
                f"Allowed: {', '.join(settings.allowed_image_types_list)}"
            ),
            content_type=content_type,
        )


def _parse_tags(raw: str) -> List[str]:
    if not raw or raw.strip() in ("", "[]"):
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(t).strip().lower() for t in parsed if t]
    except (json.JSONDecodeError, ValueError):
        pass
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


# ── Activity log helper ───────────────────────────────────────────────────────

def _make_activity(
    action: ActivityAction,
    document: Document,
    user_id: Optional[str],
    user_name: str,
) -> ActivityLog:
    return ActivityLog(
        id=uuid.uuid4(),
        action=action,
        document_id=document.id,
        document_name=document.original_name,
        document_category=document.category,
        user_id=uuid.UUID(user_id) if user_id and user_id != "dev-user" else None,
        user_name=user_name,
        timestamp=datetime.now(timezone.utc),
    )


# ── Upload ────────────────────────────────────────────────────────────────────

async def upload_document(
    db:           AsyncSession,
    file_bytes:   bytes,
    filename:     str,
    content_type: str,
    category:     str,
    tags_raw:     str,
    user_id:      Optional[str],
    user_name:    str = "Admin",
) -> UploadResponse:
    """
    Full upload pipeline with FAIL-SAFE rollback.
    """
    _validate_upload(file_bytes, content_type, filename)
    tags = _parse_tags(tags_raw)

    logger.info("upload_start", filename=filename, category=category, user=user_name)

    # Step 1 — Image → PDF in memory
    pdf_bytes, page_count = pdf_service.convert_image_to_pdf(file_bytes, content_type)

    # Step 2 — Upload PDF to Storage (AWAITED - Cloudflare R2 or Local)
    storage_url, blob_name = await blob_service.upload_pdf_to_blob(
        pdf_bytes=pdf_bytes,
        original_filename=filename,
        category=category,
    )

    # Step 3 — PostgreSQL transaction
    doc_id       = uuid.uuid4()
    pdf_filename = blob_name.split("/")[-1]

    document = Document(
        id=doc_id,
        file_name=pdf_filename,
        original_name=filename,
        blob_url=storage_url, # Uses the new storage_url
        blob_name=blob_name,
        category=DocumentCategory(category),
        status=DocumentStatus.uploaded,
        file_size=len(file_bytes),      # RESTORED
        pdf_size=len(pdf_bytes),        # RESTORED
        page_count=page_count,
        mime_type=content_type,         # RESTORED
        tags=tags,                      # RESTORED
        uploaded_by_id=uuid.UUID(user_id) if user_id and user_id != "dev-user" else None,
        uploaded_by_name=user_name,
        uploaded_at=datetime.now(timezone.utc),
    )
    activity = _make_activity(ActivityAction.upload, document, user_id, user_name)

    try:
        db.add(document)
        db.add(activity)
        await db.commit()
        await db.refresh(document)

        logger.info("upload_success", doc_id=str(doc_id), blob=blob_name)
        return UploadResponse(
            success=True,
            message="Document uploaded and stored successfully.",
            document=DocumentOut.from_orm_model(document),
            storageUrl=storage_url,
        )

    except Exception as db_exc:
        # FAIL-SAFE: DB failed → rollback → delete orphaned blob
        logger.error("upload_db_failed", doc_id=str(doc_id), blob=blob_name, error=str(db_exc))
        await db.rollback()

        try:
            await blob_service.delete_blob(blob_name) # AWAITED
            logger.info("fail_safe_blob_deleted", blob=blob_name)
        except Exception as blob_exc:
            logger.error("fail_safe_delete_failed", blob=blob_name, error=str(blob_exc))

        raise TransactionRollbackError(
            detail="Database insertion failed. The PDF has been removed from storage. Please try again.",
            original_error=str(db_exc),
        )


# ── List ──────────────────────────────────────────────────────────────────────

async def list_documents(
    db:        AsyncSession,
    category:  Optional[str] = None,
    status:    Optional[str] = None,
    search:    Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    page:      int = 1,
    page_size: int = 10,
) -> DocumentListResponse:
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
            or_(
                Document.original_name.ilike(ilike),
                Document.file_name.ilike(ilike),
            )
        )

    if date_from:
        stmt = stmt.where(Document.uploaded_at >= datetime.fromisoformat(date_from))
    if date_to:
        stmt = stmt.where(Document.uploaded_at <= datetime.fromisoformat(date_to))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    docs = (await db.execute(stmt)).scalars().all()

    return DocumentListResponse(
        documents=[DocumentOut.from_orm_model(d) for d in docs],
        total=total,
        page=page,
        page_size=page_size,
    )


# ── Get single ────────────────────────────────────────────────────────────────

async def get_document(db: AsyncSession, document_id: str) -> Document:
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        raise DocumentNotFoundError(detail=f"Invalid document ID: {document_id}")

    result = await db.execute(
        select(Document).where(
            Document.id == doc_uuid,
            Document.is_deleted == False,  # noqa: E712
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise DocumentNotFoundError(detail=f"Document '{document_id}' not found.")
    return doc


# ── Log view ──────────────────────────────────────────────────────────────────

async def log_view(
    db: AsyncSession,
    document: Document,
    user_id: Optional[str],
    user_name: str = "Unknown",
) -> None:
    activity = _make_activity(ActivityAction.view, document, user_id, user_name)
    db.add(activity)
    try:
        await db.commit()
    except Exception:
        await db.rollback()


# ── Delete ────────────────────────────────────────────────────────────────────

async def delete_document(
    db:        AsyncSession,
    document_id: str,
    user_id:   Optional[str],
    user_name: str = "Admin",
) -> None:
    doc = await get_document(db, document_id)

    # 1 — Delete from Storage (AWAITED)
    await blob_service.delete_blob(doc.blob_name)
    logger.info("blob_deleted", blob=doc.blob_name)

    # 2 — Soft-delete in PostgreSQL
    doc.is_deleted = True
    doc.deleted_at = datetime.now(timezone.utc)

    activity = _make_activity(ActivityAction.delete, doc, user_id, user_name)
    db.add(activity)
    await db.commit()
    logger.info("document_deleted", doc_id=str(doc.id))


# ── Download URL ──────────────────────────────────────────────────────────────

async def get_download_url(
    db:          AsyncSession,
    document_id: str,
    user_id:     Optional[str],
    user_name:   str = "Admin",
) -> str:
    doc = await get_document(db, document_id)
    url = blob_service.generate_download_sas_url(doc.blob_name, expires_in_minutes=60)

    activity = _make_activity(ActivityAction.download, doc, user_id, user_name)
    db.add(activity)
    await db.commit()

    return url


# ── Dashboard stats ───────────────────────────────────────────────────────────

async def get_dashboard_stats(db: AsyncSession) -> DashboardStatsOut:
    total_docs: int = (
        await db.execute(
            select(func.count(Document.id)).where(Document.is_deleted == False)  # noqa: E712
        )
    ).scalar_one()

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    docs_this_month: int = (
        await db.execute(
            select(func.count(Document.id)).where(
                Document.is_deleted == False,  # noqa: E712
                Document.uploaded_at >= month_start,
            )
        )
    ).scalar_one()

    cat_rows = (
        await db.execute(
            select(Document.category, func.count(Document.id))
            .where(Document.is_deleted == False)  # noqa: E712
            .group_by(Document.category)
        )
    ).all()
    category_counts: dict = {row[0].value: row[1] for row in cat_rows}
    for cat in ["Sales", "Purchase", "Inventory", "HR", "Finance", "Legal"]:
        category_counts.setdefault(cat, 0)

    storage_rows = (
        await db.execute(
            select(
                Document.category,
                func.sum(Document.pdf_size),
                func.count(Document.id),
            )
            .where(Document.is_deleted == False)  # noqa: E712
            .group_by(Document.category)
        )
    ).all()
    storage_by_category = [
        StorageItemOut(
            category=row[0].value,
            size=int(row[1] or 0),
            count=int(row[2] or 0),
        )
        for row in storage_rows
    ]

    total_storage: int = (
        await db.execute(
            select(func.sum(Document.pdf_size)).where(Document.is_deleted == False)  # noqa: E712
        )
    ).scalar_one() or 0

    activity_rows = (
        await db.execute(
            select(ActivityLog).order_by(ActivityLog.timestamp.desc()).limit(10)
        )
    ).scalars().all()
    recent_activity = [ActivityItemOut.from_orm_model(log) for log in activity_rows]

    return DashboardStatsOut(
        totalDocuments=total_docs,
        totalStorage=total_storage,
        documentsThisMonth=docs_this_month,
        categoryCounts=category_counts,
        recentActivity=recent_activity,
        storageByCategory=storage_by_category,
    )