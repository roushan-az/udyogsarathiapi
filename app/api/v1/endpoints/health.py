"""
Health check endpoint — used by Azure App Service health probes
and the React Settings page "Azure Connected" indicator.

GET  /api/health   →  HealthResponse
"""

from fastapi import APIRouter
from app.core.config import settings
from app.db.base import check_db_health
from app.schemas.document import HealthResponse

from app.services import blob_service

# 1. Use an empty string for the route path so it doesn't duplicate "/health/health"
router = APIRouter()

@router.get(
    "/health", # This will now be "/api/health" if your router is mounted at "/api"
    response_model=HealthResponse,
    summary="Service health check",
    description="Returns database and Storage connectivity status.",
)
async def health_check() -> HealthResponse:
    # 2. Both calls must be awaited because they are asynchronous
    db_ok   = await check_db_health()
    blob_ok = await blob_service.check_blob_health() # 👈 ADD 'await' HERE

    return HealthResponse(
        status="healthy" if (db_ok and blob_ok) else "degraded",
        version=settings.APP_VERSION,
        database=db_ok,
        storage=blob_ok,
    )