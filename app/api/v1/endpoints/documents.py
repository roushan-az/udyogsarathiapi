
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import get_current_user_id
from app.db.base import get_db
from app.schemas.document import DocumentListResponse, DocumentOut, UploadResponse
from app.services import document_service
from app.services.user_service import resolve_user_display

logger = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["Documents"])


# ── POST /documents/upload ────────────────────────────────────────────────────

@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload image → PDF → Azure Blob → PostgreSQL",
    description=(
        "Full pipeline:\n"
        "1. Validate image type and size\n"
        "2. Convert image to PDF in memory (Pillow / img2pdf — no temp files)\n"
        "3. Upload PDF to Azure Blob Storage → receive secure URL\n"
        "4. PostgreSQL transaction: INSERT document + activity_log\n"
        "5. COMMIT on success · ROLLBACK + DELETE blob on DB failure (fail-safe)\n\n"
        "The authenticated user's name is fetched from the DB and stored on the record."
    ),
)
async def upload_document(
    file:     UploadFile = File(..., description="Image file: JPEG, PNG, WEBP, TIFF, BMP"),
    category: str        = Form(..., description="Sales | Purchase | Inventory | HR | Finance | Legal"),
    tags:     str        = Form(default="[]", description='JSON array: \'["invoice","Q1"]\''),
    db:       AsyncSession = Depends(get_db),
    user_id:  str          = Depends(get_current_user_id),
) -> UploadResponse:
    file_bytes   = await file.read()
    content_type = file.content_type or "application/octet-stream"
    filename     = file.filename or "upload"

    # Resolve real user name from DB (or "Dev Admin" in debug mode)
    uploader_uuid, uploader_name = await resolve_user_display(db, user_id)

    logger.info(
        "upload_request",
        filename=filename,
        content_type=content_type,
        size=len(file_bytes),
        category=category,
        user_id=user_id,
        uploader_name=uploader_name,
    )

    return await document_service.upload_document(
        db=db,
        file_bytes=file_bytes,
        filename=filename,
        content_type=content_type,
        category=category,
        tags_raw=tags,
        user_id=user_id,
        user_name=uploader_name,   # real name from DB, not hardcoded "Admin"
    )


# ── GET /documents/ ───────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=DocumentListResponse,
    summary="List documents with server-side filter + pagination",
)
async def list_documents(
    category:  Optional[str] = Query(None),
    status:    Optional[str] = Query(None),
    search:    Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    page:      int           = Query(1, ge=1),
    page_size: int           = Query(10, ge=1, le=100),
    db:        AsyncSession  = Depends(get_db),
    _:         str           = Depends(get_current_user_id),
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
    db:          AsyncSession = Depends(get_db),
    user_id:     str          = Depends(get_current_user_id),
) -> DocumentOut:
    # Log the view event
    doc = await document_service.get_document(db, document_id)
    await document_service.log_view(db, doc, user_id)
    return DocumentOut.from_orm_model(doc)


# ── DELETE /documents/{id} ────────────────────────────────────────────────────

@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete document — Azure Blob + soft-delete in PostgreSQL",
)
async def delete_document(
    document_id: str,
    db:          AsyncSession = Depends(get_db),
    user_id:     str          = Depends(get_current_user_id),
) -> None:
    _, user_name = await resolve_user_display(db, user_id)
    await document_service.delete_document(
        db=db,
        document_id=document_id,
        user_id=user_id,
        user_name=user_name,   # real name, not hardcoded
    )


## ── GET /documents/{id}/download ──────────────────────────────────────────────
from fastapi import Query

@router.get(
    "/{document_id}/download",
    summary="Get a time-limited Cloudflare R2 pre-signed download URL (60 min)",
)
async def download_document(
    document_id: str,
    # Explicitly set the default to 'attachment'
    disposition: str = Query(default="attachment", description="attachment or inline"),
    db:          AsyncSession = Depends(get_db),
    user_id:     str          = Depends(get_current_user_id),
) -> dict:
    _, user_name = await resolve_user_display(db, user_id)

    # Ensure disposition is passed down
    url = await document_service.get_download_url(
        db=db,
        document_id=document_id,
        user_id=user_id,
        user_name=user_name,
        disposition=disposition
    )
    return {"downloadUrl": url, "expiresInMinutes": 60}

