from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

# Define the router FIRST so it registers immediately
router = APIRouter(prefix="/users", tags=["Users"])

from app.core.security import get_current_user_id
from app.db.base import get_db
from app.schemas.document import UserOut
from app.services.user_service import (
    UserNotFoundError,
    deactivate_user,
    get_all_users,
    get_user_by_id,
    update_user_role,
    update_user_status,
    admin_reset_user_password,
    delete_user as hard_delete_user,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

async def _require_superuser(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> str:
    if user_id == "dev-user":
        return user_id
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

@router.get("/", response_model=List[UserOut])
async def list_users(db: AsyncSession = Depends(get_db), _: str = Depends(_require_superuser)) -> List[UserOut]:
    users = await get_all_users(db)
    return [UserOut.from_orm_model(u) for u in users]

@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: str, db: AsyncSession = Depends(get_db), _: str = Depends(_require_superuser)) -> UserOut:
    try:
        user = await get_user_by_id(db, user_id)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail)
    user_dict = user.__dict__
    user_dict["id"] = str(user.id)
    return UserOut.model_validate(user_dict)

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate(user_id: str, db: AsyncSession = Depends(get_db), caller_id: str = Depends(_require_superuser)) -> None:
    if user_id == caller_id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself.")
    try:
        await deactivate_user(db, user_id)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail)

# --- Admin Endpoints ---

class RoleUpdate(BaseModel):
    is_superuser: bool

class StatusUpdate(BaseModel):
    is_active: bool

class PasswordReset(BaseModel):
    new_password: str

@router.put("/{user_id}/role", response_model=UserOut)
async def change_user_role(user_id: str, body: RoleUpdate, db: AsyncSession = Depends(get_db), _: str = Depends(_require_superuser)) -> UserOut:
    try:
        user = await update_user_role(db, user_id, body.is_superuser)
        return UserOut.from_orm_model(user)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail)

@router.put("/{user_id}/status", response_model=UserOut)
async def change_user_status(user_id: str, body: StatusUpdate, db: AsyncSession = Depends(get_db), caller_id: str = Depends(_require_superuser)) -> UserOut:
    if user_id == caller_id and not body.is_active:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself.")
    try:
        user = await update_user_status(db, user_id, body.is_active)
        return UserOut.from_orm_model(user)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail)

@router.post("/{user_id}/reset-password", response_model=UserOut)
async def admin_reset_password(user_id: str, body: PasswordReset, db: AsyncSession = Depends(get_db), _: str = Depends(_require_superuser)) -> UserOut:
    try:
        user = await admin_reset_user_password(db, user_id, body.new_password)
        return UserOut.from_orm_model(user)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail)

@router.delete("/{user_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
async def permanently_delete_user(user_id: str, db: AsyncSession = Depends(get_db), caller_id: str = Depends(_require_superuser)) -> None:
    if user_id == caller_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself.")
    try:
        await hard_delete_user(db, user_id)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail)