# app/api/v1/endpoints/documents.py
"""
Documents router — all endpoints consumed by the React frontend.

Routes (prefixed with /api/documents):
  POST   /upload              upload image → PDF → Blob → DB
  GET    /                    list documents (filter + paginate)
  GET    /{id}                get single document
  DELETE /{id}                delete document (blob + soft-delete DB)
  GET    /{id}/download       get SAS download URL for PDF
"""

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user_id
from app.db.base import get_db
from app.schemas.document import DocumentListResponse, DocumentOut, UploadResponse
from app.services import document_service
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["Documents"])


# ── POST /documents/upload ────────────────────────────────────────────────────

@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload image and convert to PDF",
    description=(
        "Accepts a multipart image upload. "
        "The server converts it to PDF in memory, uploads the PDF to Azure Blob Storage, "
        "then records the document in PostgreSQL. "
        "If the database insert fails, the blob is deleted automatically (fail-safe)."
    ),
)
async def upload_document(
    file: UploadFile = File(..., description="Image file (JPEG, PNG, WEBP, TIFF, BMP)"),
    category: str = Form(..., description="Document category: Sales | Purchase | Inventory | HR | Finance | Legal"),
    tags: str = Form(default="[]", description='JSON array of tags, e.g. \'["invoice","Q1"]\''),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> UploadResponse:
    """
    Full upload pipeline:
      1. Read image bytes from multipart form
      2. Validate file type and size
      3. Convert image → PDF in memory (no temp files)
      4. Upload PDF to Azure Blob Storage → receive secure URL
      5. Open PostgreSQL transaction → INSERT document + activity_log
      6. COMMIT on success, ROLLBACK + DELETE blob on failure
    """
    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"
    filename = file.filename or "upload"

    logger.info(
        "upload_request",
        filename=filename,
        content_type=content_type,
        size=len(file_bytes),
        category=category,
        user_id=user_id,
    )

    return await document_service.upload_document(
        db=db,
        file_bytes=file_bytes,
        filename=filename,
        content_type=content_type,
        category=category,
        tags_raw=tags,
        user_id=user_id,
        user_name="Admin",  # Replace with real user lookup when auth is wired
    )


# ── GET /documents ────────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=DocumentListResponse,
    summary="List documents with filtering and pagination",
)
async def list_documents(
    category:  Optional[str] = Query(None, description="Filter by category"),
    status:    Optional[str] = Query(None, description="Filter by status: uploaded | processing | failed | queued"),
    search:    Optional[str] = Query(None, description="Search by filename or tags"),
    date_from: Optional[str] = Query(None, description="ISO date lower bound (uploadedAt)"),
    date_to:   Optional[str] = Query(None, description="ISO date upper bound (uploadedAt)"),
    page:      int           = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int           = Query(10, ge=1, le=100, description="Results per page"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> DocumentListResponse:
    return await document_service.list_documents(
        db=db,
        category=category,
        status=status,
        search=search,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


# ── GET /documents/{id} ───────────────────────────────────────────────────────

@router.get(
    "/{document_id}",
    response_model=DocumentOut,
    summary="Get a single document by ID",
)
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> DocumentOut:
    doc = await document_service.get_document(db, document_id)
    return DocumentOut.from_orm_model(doc)


# ── DELETE /documents/{id} ────────────────────────────────────────────────────

@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document (blob + DB record)",
    description=(
        "Deletes the PDF from Azure Blob Storage, then soft-deletes the "
        "database record and writes an audit log entry."
    ),
)
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> None:
    await document_service.delete_document(
        db=db,
        document_id=document_id,
        user_id=user_id,
        user_name="Admin",
    )


# ── GET /documents/{id}/download ──────────────────────────────────────────────

@router.get(
    "/{document_id}/download",
    summary="Get a time-limited SAS download URL",
    description="Returns a pre-signed Azure Blob SAS URL valid for 60 minutes.",
)
async def download_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    url = await document_service.get_download_url(
        db=db,
        document_id=document_id,
        user_id=user_id,
        user_name="Admin",
    )
    return {"downloadUrl": url, "expiresInMinutes": 60}