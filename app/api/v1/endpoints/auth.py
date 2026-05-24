
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from app.schemas.document import LoginRequest, RegisterRequest, TokenResponse, UserOut

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user_id,
)
from app.db.base import get_db
from app.schemas.document import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.services.user_service import (
    AccountDisabledError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    UserNotFoundError,
    authenticate_user,
    create_user,
    get_user_by_id,
    update_user_password,
    update_user_profile,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])
bearer_scheme = HTTPBearer()


# ── Schemas for this router only ──────────────────────────────────────────────

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


class UpdateProfileRequest(BaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=255)

class RegisterRequestExtended(RegisterRequest):
    admin_secret: Optional[str] = None
# ── POST /auth/register ───────────────────────────────────────────────────────

@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account (with optional Admin key)",
)
async def register(
        body: RegisterRequestExtended,
        db: AsyncSession = Depends(get_db),
) -> UserOut:
    # A. Create a standard user payload (stripping out the secret)
    base_body = RegisterRequest(
        email=body.email,
        password=body.password,
        full_name=body.full_name
    )

    try:
        user = await create_user(db, base_body)
    except EmailAlreadyRegisteredError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=e.detail)

    # B. 🔒 THE ADMIN GATE 🔒
    # If they provided a secret, and it matches the .env file, flip the DB boolean!
    if body.admin_secret and settings.ADMIN_SECRET_KEY:
        if body.admin_secret == settings.ADMIN_SECRET_KEY:
            user.is_superuser = True

            # Save the admin status to PostgreSQL permanently
            db.add(user)
            await db.commit()
            await db.refresh(user)

    return UserOut.from_orm_model(user)


# ── POST /auth/login ──────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login — verify credentials and receive JWT tokens",
    description=(
        "Verifies email + password against the users table. "
        "Returns a short-lived access token and a long-lived refresh token. "
        "The access token is stored in localStorage by the React app."
    ),
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    try:
        user = await authenticate_user(db, body.email, body.password)
    except (InvalidCredentialsError, AccountDisabledError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    access_token  = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))

    logger.info("login_success", user_id=str(user.id), email=user.email)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
    )


# ── POST /auth/refresh ────────────────────────────────────────────────────────

@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token using a refresh token",
)
async def refresh_token(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> TokenResponse:
    payload = decode_token(credentials.credentials)

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type — provide a refresh token.",
        )

    subject: str = payload.get("sub", "")
    return TokenResponse(
        access_token=create_access_token(subject=subject),
        refresh_token=create_refresh_token(subject=subject),
        token_type="bearer",
    )


# ── GET /auth/me ──────────────────────────────────────────────────────────────

@router.get(
    "/me",
    response_model=UserOut,
    summary="Get the authenticated user's profile from DB",
)
async def get_me(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    # DEBUG mode: return a synthetic dev profile without hitting the DB
    if user_id == "dev-user":
        return UserOut(
            id="dev-user",
            email="dev@udyogsarathi.local",
            full_name="Dev Admin",
            is_active=True,
            is_superuser=True,
        )

    try:
        user = await get_user_by_id(db, user_id)
    except UserNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.detail)

    return UserOut.from_orm_model(user)


# ── PUT /auth/me ──────────────────────────────────────────────────────────────

@router.put(
    "/me",
    response_model=UserOut,
    summary="Update the authenticated user's display name",
)
async def update_me(
    body: UpdateProfileRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    if user_id == "dev-user":
        raise HTTPException(status_code=400, detail="Cannot update profile in dev mode.")

    try:
        user = await update_user_profile(db, user_id, full_name=body.full_name)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail)

    return UserOut.from_orm_model(user)


# ── POST /auth/change-password ────────────────────────────────────────────────

@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change password — requires the current password",
)
async def change_password(
    body: ChangePasswordRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> None:
    if user_id == "dev-user":
        raise HTTPException(status_code=400, detail="Cannot change password in dev mode.")

    try:
        await update_user_password(db, user_id, body.old_password, body.new_password)
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=400, detail=e.detail)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail)