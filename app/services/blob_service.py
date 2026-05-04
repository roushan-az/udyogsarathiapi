# app/services/blob_service.py
"""
Azure Blob Storage service.

Responsibilities:
  • Upload a PDF (bytes) to Azure Blob Storage → returns (blob_url, blob_name).
  • Delete a blob by name — used by the FAIL-SAFE rollback when DB insert fails.
  • Generate a SAS download URL (time-limited, for secure downloads).
  • Check storage health.

Authentication priority:
  1. Managed Identity (production on Azure App Service)  USE_MANAGED_IDENTITY=true
  2. Connection string                                    AZURE_STORAGE_CONNECTION_STRING
  3. Account name + key                                   AZURE_STORAGE_ACCOUNT_KEY
"""

import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import (
    BlobClient,
    BlobSasPermissions,
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
)

from app.core.config import settings
from app.core.exceptions import BlobDeleteError, BlobStorageError, BlobUploadError
from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Client factory (singleton per process) ────────────────────────────────────

def _build_blob_service_client() -> BlobServiceClient:
    """Build BlobServiceClient using the best available credential."""
    account_name = settings.AZURE_STORAGE_ACCOUNT_NAME
    account_url = f"https://{account_name}.blob.core.windows.net"

    if settings.USE_MANAGED_IDENTITY:
        logger.info("blob_auth", method="managed_identity")
        credential = DefaultAzureCredential()
        return BlobServiceClient(account_url=account_url, credential=credential)

    if settings.AZURE_STORAGE_CONNECTION_STRING:
        logger.info("blob_auth", method="connection_string")
        return BlobServiceClient.from_connection_string(settings.AZURE_STORAGE_CONNECTION_STRING)

    if settings.AZURE_STORAGE_ACCOUNT_KEY:
        logger.info("blob_auth", method="account_key")
        credential = {
            "account_name": account_name,
            "account_key": settings.AZURE_STORAGE_ACCOUNT_KEY,
        }
        return BlobServiceClient(account_url=account_url, credential=credential)

    raise BlobStorageError(
        detail=(
            "No Azure Blob credentials configured. "
            "Set USE_MANAGED_IDENTITY, AZURE_STORAGE_CONNECTION_STRING, "
            "or AZURE_STORAGE_ACCOUNT_KEY."
        )
    )


# Lazy singleton — instantiated on first use
_client: Optional[BlobServiceClient] = None


def get_blob_service_client() -> BlobServiceClient:
    global _client
    if _client is None:
        _client = _build_blob_service_client()
    return _client


def _get_container_client():
    return get_blob_service_client().get_container_client(
        settings.AZURE_STORAGE_CONTAINER_NAME
    )


# ── Upload ────────────────────────────────────────────────────────────────────

def upload_pdf_to_blob(
    pdf_bytes: bytes,
    original_filename: str,
    category: str,
) -> Tuple[str, str]:
    """
    Upload PDF bytes to Azure Blob Storage.

    Args:
        pdf_bytes:         The in-memory PDF to store.
        original_filename: The user's original image filename (for naming hints).
        category:          Document category (used as a virtual folder prefix).

    Returns:
        (blob_url, blob_name)  — blob_url is the full https:// URL.

    Raises:
        BlobUploadError: On any Azure SDK error.
    """
    # Build a collision-resistant blob name:
    # e.g.  Sales/2024-03/a3f8c1d2.pdf
    month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    short_id = uuid.uuid4().hex[:12]
    # Strip extension from original, keep sanitised stem for readability
    stem = (
        original_filename.rsplit(".", 1)[0]
        .replace(" ", "_")
        .replace("/", "-")[:60]
    )
    blob_name = f"{category}/{month_prefix}/{short_id}_{stem}.pdf"

    logger.info("blob_upload_start", blob_name=blob_name, size=len(pdf_bytes))

    try:
        container_client = _get_container_client()
        blob_client: BlobClient = container_client.get_blob_client(blob_name)

        blob_client.upload_blob(
            data=io.BytesIO(pdf_bytes),
            overwrite=False,
            content_settings=ContentSettings(
                content_type="application/pdf",
                content_disposition=f'inline; filename="{short_id}_{stem}.pdf"',
            ),
        )

        blob_url = blob_client.url
        logger.info("blob_upload_success", blob_name=blob_name, blob_url=blob_url)
        return blob_url, blob_name

    except AzureError as exc:
        logger.error("blob_upload_failed", blob_name=blob_name, error=str(exc))
        raise BlobUploadError(
            detail=f"Failed to upload PDF to Azure Blob: {exc}",
            blob_name=blob_name,
        )


# ── Delete (Fail-Safe) ────────────────────────────────────────────────────────

def delete_blob(blob_name: str) -> bool:
    """
    Delete a blob by name.

    This is called by the FAIL-SAFE rollback in the upload endpoint:
    if the PostgreSQL INSERT fails after a successful Blob upload,
    we call this to clean up the orphaned file.

    Returns True if deleted, False if blob was not found (idempotent).
    Raises BlobDeleteError only on unexpected Azure errors.
    """
    logger.warning("blob_delete_start", blob_name=blob_name, reason="db_rollback_cleanup")
    try:
        container_client = _get_container_client()
        blob_client = container_client.get_blob_client(blob_name)
        blob_client.delete_blob(delete_snapshots="include")
        logger.info("blob_delete_success", blob_name=blob_name)
        return True

    except ResourceNotFoundError:
        # Already gone — treat as success (idempotent)
        logger.warning("blob_delete_not_found", blob_name=blob_name)
        return False

    except AzureError as exc:
        logger.error("blob_delete_failed", blob_name=blob_name, error=str(exc))
        raise BlobDeleteError(
            detail=f"Failed to delete orphaned blob '{blob_name}': {exc}",
            blob_name=blob_name,
        )


# ── Download URL ──────────────────────────────────────────────────────────────

def generate_download_sas_url(blob_name: str, expires_in_minutes: int = 60) -> str:
    """
    Generate a time-limited SAS URL for downloading a specific blob.
    The React frontend uses this for the "Download PDF" action.

    Note: Only works with account key auth — not Managed Identity.
    For Managed Identity, use user delegation keys (more complex).
    """
    if not settings.AZURE_STORAGE_ACCOUNT_KEY:
        # If using Managed Identity, just return the direct blob URL.
        # Azure RBAC / container ACL controls access in production.
        container = _get_container_client()
        return container.get_blob_client(blob_name).url

    expiry = datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)

    sas_token = generate_blob_sas(
        account_name=settings.AZURE_STORAGE_ACCOUNT_NAME,
        container_name=settings.AZURE_STORAGE_CONTAINER_NAME,
        blob_name=blob_name,
        account_key=settings.AZURE_STORAGE_ACCOUNT_KEY,
        permission=BlobSasPermissions(read=True),
        expiry=expiry,
    )

    return (
        f"https://{settings.AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
        f"/{settings.AZURE_STORAGE_CONTAINER_NAME}/{blob_name}?{sas_token}"
    )


# ── Health check ──────────────────────────────────────────────────────────────

def check_blob_health() -> bool:
    """Verify we can reach the Azure container.  Returns True if accessible."""
    try:
        container_client = _get_container_client()
        container_client.get_container_properties()
        return True
    except Exception as exc:
        logger.error("blob_health_check_failed", error=str(exc))
        return False


# ── Storage stats ─────────────────────────────────────────────────────────────

def get_total_storage_bytes() -> int:
    """
    Walk the container and sum blob sizes.
    Used by the dashboard stats endpoint.
    WARNING: Can be slow for large containers — consider caching.
    """
    try:
        container_client = _get_container_client()
        total = sum(
            b.size
            for b in container_client.list_blobs()
            if b.size is not None
        )
        return total
    except Exception as exc:
        logger.error("blob_list_failed", error=str(exc))
        return 0