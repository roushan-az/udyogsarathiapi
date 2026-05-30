
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.security import get_current_user_id
from app.db.base import get_db
from app.schemas.document import UserOut
from app.services.user_service import (
    UserNotFoundError,
    deactivate_user,
    get_all_users,
    get_user_by_id,
)
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/users", tags=["Users"])


async def _require_superuser(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> str:
    """Dependency that rejects non-superusers (or allows dev-user in DEBUG mode)."""
    if user_id == "dev-user":
        return user_id  # DEBUG mode — allow everything
    try:
        user = await get_user_by_id(db, user_id)
        if not user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Superuser access required.",
            )
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail)
    return user_id


@router.get(
    "/",
    response_model=List[UserOut],
    summary="List all users [superuser only]",
)
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_require_superuser),
) -> List[UserOut]:
    users = await get_all_users(db)
    return [UserOut.from_orm_model(u) for u in users]


@router.get(
    "/{user_id}",
    response_model=UserOut,
    summary="Get any user by ID [superuser only]",
)
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(_require_superuser),
) -> UserOut:
    try:
        user = await get_user_by_id(db, user_id)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail)
    user_dict = user.__dict__
    user_dict["id"] = str(user.id)
    return UserOut.model_validate(user_dict)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a user [superuser only]",
)
async def deactivate(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    caller_id: str = Depends(_require_superuser),
) -> None:
    if user_id == caller_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself.")
    try:
        await deactivate_user(db, user_id)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail)