# app/api/v1/endpoints/auth.py
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_mail import ConnectionConfig
from pydantic import BaseModel

# ── Core & DB ────────────────────────────────────────────────────────────────
from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_current_user_id,
    hash_password
)

# ── Schemas ──────────────────────────────────────────────────────────────────
from app.schemas.document import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.schemas.auth import ForgotPasswordRequest, ResetPasswordOTPRequest


# Define local schemas for requests to prevent any "Unresolved Reference" errors
class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class RefreshRequest(BaseModel):
    refresh_token: str


# ── Services ─────────────────────────────────────────────────────────────────
from app.services.user_service import (
    create_user,
    authenticate_user,
    get_user_by_id,
    update_user_password,
    InvalidCredentialsError,
    UserNotFoundError,
    get_user_by_email
)
from app.services.otp_service import (
    EmailOTPProvider,
    generate_and_store_otp,
    verify_and_delete_otp
)

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])

# ── Mail Configuration for OTP ───────────────────────────────────────────────
mail_config = ConnectionConfig(
    MAIL_USERNAME=settings.MAIL_USERNAME,
    MAIL_PASSWORD=settings.MAIL_PASSWORD,
    MAIL_FROM=settings.MAIL_FROM,
    MAIL_PORT=settings.MAIL_PORT,
    MAIL_SERVER=settings.MAIL_SERVER,
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True
)
email_provider = EmailOTPProvider(mail_config)


# ── Standard Authentication Routes ───────────────────────────────────────────

@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Registers a new user."""
    user = await create_user(db, body)
    return UserOut.from_orm_model(user)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticates a user and returns access & refresh tokens."""
    user = await authenticate_user(db, body.email, body.password)

    # <--- Reverted back to your original 'subject' parameter
    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))

    logger.info("login_success", user_id=str(user.id), email=user.email)

    return TokenResponse(
        accessToken=access_token,  # <--- Changed to camelCase
        refreshToken=refresh_token,  # <--- Changed to camelCase
        tokenType="bearer",  # <--- Changed to camelCase
    )
@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Refreshes an access token."""
    # Note: Keep your existing refresh logic here if you had custom behavior
    raise HTTPException(status_code=501, detail="Refresh logic to be implemented")


@router.get("/me", response_model=UserOut, summary="Get the authenticated user's profile from DB")
async def get_me(
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
) -> UserOut:
    """Fetches the profile of the currently authenticated user."""
    if user_id == "dev-user":
        return UserOut(
            id="dev-user",
            email="dev@udyogsarathi.local",
            fullName="Dev Admin",
            isActive=True,
            isSuperuser=True,
        )

    try:
        user = await get_user_by_id(db, user_id)
    except UserNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.detail)

    user_dict = user.__dict__
    user_dict["id"] = str(user.id)
    return UserOut.model_validate(user_dict)


@router.post("/change-password")
async def change_password(
        body: ChangePasswordRequest,
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db)
):
    """Changes the password for an authenticated user."""
    if user_id == "dev-user":
        raise HTTPException(status_code=400, detail="Cannot change password in dev mode.")

    try:
        await update_user_password(db, user_id, body.old_password, body.new_password)
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=400, detail=e.detail)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail)

    logger.info("password_changed_successfully", user_id=user_id)
    return {"message": "Password updated successfully."}


# ── OTP Password Reset Routes ────────────────────────────────────────────────

@router.post("/forgot-password")
async def forgot_password(
        request: ForgotPasswordRequest,
        background_tasks: BackgroundTasks,
        db: AsyncSession = Depends(get_db)
):
    """Generates an OTP and sends it via email without blocking the API."""
    user = await get_user_by_email(db, request.email)

    # We silently ignore invalid emails to prevent email enumeration fishing
    if user:
        otp = await generate_and_store_otp(db, user.email)
        await email_provider.send_otp(user.email, otp, background_tasks)
        logger.info("otp_sent", email=user.email)

    return {"message": "If an account with that email exists, an OTP has been sent."}


@router.post("/reset-password")
async def reset_password_with_otp(
        request: ResetPasswordOTPRequest,
        db: AsyncSession = Depends(get_db)
):
    """Verifies the 6-digit OTP and resets the password."""
    user = await get_user_by_email(db, request.email)
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request.")

    # Validate OTP against PostgreSQL
    is_valid = await verify_and_delete_otp(db, request.email, request.otp)
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OTP.")

    # Hash the new password and update the user record
    user.hashed_password = hash_password(request.new_password)
    await db.commit()

    logger.info("password_reset_success", email=user.email)
    return {"message": "Password has been successfully reset. You can now log in."}