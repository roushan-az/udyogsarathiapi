import io
import os
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

# Ensure local directory exists immediately if offline mode is active
if settings.USE_LOCAL_STORAGE:
    os.makedirs(settings.LOCAL_UPLOAD_DIR, exist_ok=True)


# ── Client factory ────────────────────────────────────────────────────────────

def _build_blob_service_client() -> BlobServiceClient:
    # (Keep your existing _build_blob_service_client logic here...)
    account_name = settings.AZURE_STORAGE_ACCOUNT_NAME
    account_url = f"https://{account_name}.blob.core.windows.net"

    if settings.USE_MANAGED_IDENTITY:
        return BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
    if settings.AZURE_STORAGE_CONNECTION_STRING:
        return BlobServiceClient.from_connection_string(settings.AZURE_STORAGE_CONNECTION_STRING)
    if settings.AZURE_STORAGE_ACCOUNT_KEY:
        credential = {"account_name": account_name, "account_key": settings.AZURE_STORAGE_ACCOUNT_KEY}
        return BlobServiceClient(account_url=account_url, credential=credential)

    raise BlobStorageError(detail="No Azure Blob credentials configured.")


_client: Optional[BlobServiceClient] = None


def get_blob_service_client() -> BlobServiceClient:
    global _client
    if _client is None and not settings.USE_LOCAL_STORAGE:
        _client = _build_blob_service_client()
    return _client


def _get_container_client():
    return get_blob_service_client().get_container_client(settings.AZURE_STORAGE_CONTAINER_NAME)


# ── Upload ────────────────────────────────────────────────────────────────────

def upload_pdf_to_blob(pdf_bytes: bytes, original_filename: str, category: str) -> Tuple[str, str]:
    month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    short_id = uuid.uuid4().hex[:12]
    stem = original_filename.rsplit(".", 1)[0].replace(" ", "_").replace("/", "-")[:60]
    blob_name = f"{category}/{month_prefix}/{short_id}_{stem}.pdf"

    # ── OFFLINE MODE INTERCEPT ──
    if settings.USE_LOCAL_STORAGE:
        local_path = os.path.join(settings.LOCAL_UPLOAD_DIR, blob_name)
        # Create subfolders (e.g., Sales/2024-03/)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        with open(local_path, "wb") as f:
            f.write(pdf_bytes)

        blob_url = f"http://localhost:8000/api/local-files/{blob_name}"
        logger.info("local_upload_success", blob_name=blob_name)
        return blob_url, blob_name

    # ── AZURE MODE ──
    try:
        container_client = _get_container_client()
        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(
            data=io.BytesIO(pdf_bytes), overwrite=False,
            content_settings=ContentSettings(content_type="application/pdf")
        )
        return blob_client.url, blob_name
    except AzureError as exc:
        raise BlobUploadError(detail=str(exc), blob_name=blob_name)


# ── Delete (Fail-Safe) ────────────────────────────────────────────────────────

def delete_blob(blob_name: str) -> bool:
    # ── OFFLINE MODE INTERCEPT ──
    if settings.USE_LOCAL_STORAGE:
        local_path = os.path.join(settings.LOCAL_UPLOAD_DIR, blob_name)
        if os.path.exists(local_path):
            os.remove(local_path)
            return True
        return False

    # ── AZURE MODE ──
    try:
        _get_container_client().get_blob_client(blob_name).delete_blob(delete_snapshots="include")
        return True
    except ResourceNotFoundError:
        return False
    except AzureError as exc:
        raise BlobDeleteError(detail=str(exc), blob_name=blob_name)


# ── Download URL ──────────────────────────────────────────────────────────────

def generate_download_sas_url(blob_name: str, expires_in_minutes: int = 60) -> str:
    # ── OFFLINE MODE INTERCEPT ──
    if settings.USE_LOCAL_STORAGE:
        return f"http://localhost:8000/api/local-files/{blob_name}"

    # ── AZURE MODE ──
    expiry = datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)
    sas_token = generate_blob_sas(
        account_name=settings.AZURE_STORAGE_ACCOUNT_NAME,
        container_name=settings.AZURE_STORAGE_CONTAINER_NAME,
        blob_name=blob_name,
        account_key=settings.AZURE_STORAGE_ACCOUNT_KEY,
        permission=BlobSasPermissions(read=True),
        expiry=expiry,
    )
    return f"https://{settings.AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net/{settings.AZURE_STORAGE_CONTAINER_NAME}/{blob_name}?{sas_token}"


# ── Health check ──────────────────────────────────────────────────────────────

def check_blob_health() -> bool:
    if settings.USE_LOCAL_STORAGE:
        return os.path.exists(settings.LOCAL_UPLOAD_DIR)

    try:
        _get_container_client().get_container_properties()
        return True
    except Exception:
        return False


# ── Storage stats ─────────────────────────────────────────────────────────────

def get_total_storage_bytes() -> int:
    # ── OFFLINE MODE INTERCEPT ──
    if settings.USE_LOCAL_STORAGE:
        total = 0
        for dirpath, _, filenames in os.walk(settings.LOCAL_UPLOAD_DIR):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
        return total

    # ── AZURE MODE ──
    try:
        return sum(b.size for b in _get_container_client().list_blobs() if b.size)
    except Exception:
        return 0