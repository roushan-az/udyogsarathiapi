
import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UdyogBaseException
from app.core.logging import get_logger
from app.core.security import hash_password, verify_password
from app.models.document import User
from app.schemas.document import RegisterRequest, UserOut

logger = get_logger(__name__)


class UserNotFoundError(UdyogBaseException):
    status_code = 404
    error_code = "USER_NOT_FOUND"


class EmailAlreadyRegisteredError(UdyogBaseException):
    status_code = 409
    error_code = "EMAIL_ALREADY_REGISTERED"


class InvalidCredentialsError(UdyogBaseException):
    status_code = 401
    error_code = "INVALID_CREDENTIALS"


class AccountDisabledError(UdyogBaseException):
    status_code = 403
    error_code = "ACCOUNT_DISABLED"


async def get_user_by_id(db: AsyncSession, user_id: str) -> User:
    """Fetch a user by UUID string. Raises UserNotFoundError if missing."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise UserNotFoundError(detail=f"Invalid user ID: {user_id}")

    result = await db.execute(select(User).where(User.id == uid, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        raise UserNotFoundError(detail=f"User '{user_id}' not found or inactive.")
    return user


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    """Return User or None — does NOT raise."""
    result = await db.execute(
        select(User).where(User.email == email.lower().strip())
    )
    return result.scalar_one_or_none()


async def create_user(db: AsyncSession, body: RegisterRequest) -> User:
    """
    Create a new user. Raises EmailAlreadyRegisteredError if duplicate.
    Commits the transaction before returning.
    """
    existing = await get_user_by_email(db, body.email)
    if existing:
        raise EmailAlreadyRegisteredError(
            detail=f"Email '{body.email}' is already registered."
        )

    user = User(
        id=uuid.uuid4(),
        email=body.email.lower().strip(),
        full_name=body.full_name.strip(),
        hashed_password=hash_password(body.password),
        is_active=True,
        is_superuser=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info("user_created", user_id=str(user.id), email=user.email)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    """
    Verify email + password against DB.
    Raises InvalidCredentialsError or AccountDisabledError on failure.
    """
    user = await get_user_by_email(db, email)

    if not user or not verify_password(password, user.hashed_password):
        logger.warning("auth_failed_bad_credentials", email=email)
        raise InvalidCredentialsError(detail="Incorrect email or password.")

    if not user.is_active:
        logger.warning("auth_failed_inactive", user_id=str(user.id))
        raise AccountDisabledError(detail="Your account has been disabled.")

    logger.info("auth_success", user_id=str(user.id))
    return user


async def update_user_password(
    db: AsyncSession, user_id: str, old_password: str, new_password: str
) -> User:
    """Change password — verifies old password first."""
    user = await get_user_by_id(db, user_id)

    if not verify_password(old_password, user.hashed_password):
        raise InvalidCredentialsError(detail="Current password is incorrect.")

    user.hashed_password = hash_password(new_password)
    await db.commit()
    await db.refresh(user)

    logger.info("password_changed", user_id=str(user.id))
    return user


async def update_user_profile(
    db: AsyncSession,
    user_id: str,
    full_name: Optional[str] = None,
) -> User:
    """Update display name."""
    user = await get_user_by_id(db, user_id)
    if full_name:
        user.full_name = full_name.strip()
    await db.commit()
    await db.refresh(user)
    return user


async def deactivate_user(db: AsyncSession, user_id: str) -> None:
    """Soft-disable an account (admin action)."""
    user = await get_user_by_id(db, user_id)
    user.is_active = False
    await db.commit()
    logger.info("user_deactivated", user_id=str(user.id))


async def get_all_users(db: AsyncSession) -> list[User]:
    """List all active users — superuser only."""
    result = await db.execute(
        select(User).where(User.is_active == True).order_by(User.created_at.desc())
    )
    return list(result.scalars().all())


async def resolve_user_display(db: AsyncSession, user_id: Optional[str]) -> tuple[Optional[uuid.UUID], str]:
    """
    Given a user_id string (or None for dev-user), return:
      (uuid_or_none, display_name_string)

    Used by document endpoints to populate uploaded_by_id and uploaded_by_name.
    """
    if user_id is None or user_id == "dev-user":
        return None, "Dev Admin"

    try:
        user = await get_user_by_id(db, user_id)
        return user.id, user.full_name
    except UserNotFoundError:
        return None, "Unknown"