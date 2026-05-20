import os
import uuid
import boto3
from datetime import datetime, timezone
from typing import Tuple
from botocore.config import Config
from anyio import to_thread
from app.core.config import settings
from app.core.exceptions import BlobUploadError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _get_s3_client():
    """Initializes s3 client for Cloudflare R2 using environment variables."""
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto"
    )


async def upload_pdf_to_blob(pdf_bytes: bytes, original_filename: str, category: str) -> Tuple[str, str]:
    """Uploads the generated PDF to Cloudflare R2."""
    month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    short_id = uuid.uuid4().hex[:12]
    stem = original_filename.rsplit(".", 1)[0].replace(" ", "_")[:60]
    blob_name = f"{category}/{month_prefix}/{short_id}_{stem}.pdf"

    if settings.STORAGE_MODE == "S3":
        try:
            s3 = _get_s3_client()

            def _upload():
                s3.put_object(
                    Bucket=settings.R2_BUCKET_NAME, Key=blob_name,
                    Body=pdf_bytes, ContentType="application/pdf"
                )
                return f"{settings.R2_ENDPOINT_URL}/{settings.R2_BUCKET_NAME}/{blob_name}"

            storage_url = await to_thread.run_sync(_upload)
            return storage_url, blob_name
        except Exception as e:
            logger.error("r2_upload_failed", error=str(e))
            raise BlobUploadError(detail=str(e), blob_name=blob_name)

    return "local_url_fallback", blob_name


async def delete_blob(blob_name: str) -> bool:
    if settings.STORAGE_MODE == "S3":
        s3 = _get_s3_client()
        await to_thread.run_sync(lambda: s3.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=blob_name))
    return True


async def check_blob_health() -> bool:
    """Restored for health endpoint compatibility."""
    try:
        if settings.STORAGE_MODE == "S3":
            s3 = _get_s3_client()
            await to_thread.run_sync(lambda: s3.head_bucket(Bucket=settings.R2_BUCKET_NAME))
            return True
        return True
    except Exception:
        return False


async def get_total_storage_bytes() -> int:
    """Restored for Dashboard stats compatibility."""
    try:
        if settings.STORAGE_MODE == "S3":
            s3 = _get_s3_client()

            def _get_size():
                response = s3.list_objects_v2(Bucket=settings.R2_BUCKET_NAME)
                return sum(obj['Size'] for obj in response.get('Contents', []))

            return await to_thread.run_sync(_get_size)
        return 0
    except Exception:
        return 0


def generate_download_sas_url(blob_name: str, expires_in_minutes: int = 60) -> str:
    """Returns a direct link to the document in R2."""
    return f"{settings.R2_ENDPOINT_URL}/{settings.R2_BUCKET_NAME}/{blob_name}"