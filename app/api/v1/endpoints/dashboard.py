# app/api/v1/endpoints/dashboard.py
"""
Dashboard router — aggregated stats consumed by the React Dashboard page.

Routes (prefixed with /api/dashboard):
  GET  /stats    →  DashboardStatsOut
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user_id
from app.db.base import get_db
from app.schemas.document import DashboardStatsOut
from app.services import document_service

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get(
    "/stats",
    response_model=DashboardStatsOut,
    summary="Aggregated dashboard statistics",
    description=(
        "Returns total document count, storage usage, per-category breakdowns, "
        "and the 10 most recent activity log entries. "
        "Consumed by the React Dashboard page."
    ),
)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_user_id),
) -> DashboardStatsOut:
    return await document_service.get_dashboard_stats(db)