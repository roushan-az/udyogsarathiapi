from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user_id
from app.db.base import get_db
from app.schemas.analytics import AnalyticsResponse
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["Analytics"])

@router.get(
    "",
    response_model=AnalyticsResponse,
    summary="Get full document intelligence and analytics",
    description="Fetches KPIs, weekly trends, storage growth, and uploader stats from PostgreSQL."
)
async def get_analytics(
    db: AsyncSession = Depends(get_db),
    # Protect this route — only logged-in users can see analytics
    user_id: str = Depends(get_current_user_id),
) -> AnalyticsResponse:
    # Call the service logic you already wrote
    return await analytics_service.get_analytics(db)