# app/api/v1/endpoints/health.py
"""
Health check endpoint — used by Azure App Service health probes
and the React Settings page "Azure Connected" indicator.

GET  /api/health   →  HealthResponse
"""

from fastapi import APIRouter

from app.core.config import settings
from app.db.base import check_db_health
from app.schemas.document import HealthResponse
from app.services.blob_service import check_blob_health

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    description="Returns database and Azure Blob connectivity status.",
)
async def health_check() -> HealthResponse:
    db_ok   = await check_db_health()
    blob_ok = check_blob_health()

    return HealthResponse(
        status="healthy" if (db_ok and blob_ok) else "degraded",
        version=settings.APP_VERSION,
        database=db_ok,
        storage=blob_ok,
    )