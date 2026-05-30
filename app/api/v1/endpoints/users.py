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


async def get_user_by_id(db: AsyncSession, user_id: str, include_inactive: bool = False) -> User:
    """Fetch a user by UUID string. Raises UserNotFoundError if missing."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise UserNotFoundError(detail=f"Invalid user ID: {user_id}")

    # Build the query dynamically
    query = select(User).where(User.id == uid)

    if not include_inactive:
        # Standard behavior: only fetch active users
        query = query.where(User.is_active == True)

    result = await db.execute(query)
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
    # Use include_inactive=True so we don't crash if they are already inactive
    user = await get_user_by_id(db, user_id, include_inactive=True)
    user.is_active = False
    await db.commit()
    logger.info("user_deactivated", user_id=str(user.id))


async def get_all_users(db: AsyncSession) -> list[User]:
    """List all users (active and inactive) for the admin panel."""
    # Removed User.is_active == True so the React Admin Panel shows everyone
    result = await db.execute(
        select(User).order_by(User.created_at.desc())
    )
    return list(result.scalars().all())


async def resolve_user_display(db: AsyncSession, user_id: Optional[str]) -> tuple[Optional[uuid.UUID], str]:
    """
    Given a user_id string (or None for dev-user), return:
      (uuid_or_none, display_name_string)
    """
    if user_id is None or user_id == "dev-user":
        return None, "Dev Admin"

    try:
        user = await get_user_by_id(db, user_id)
        return user.id, user.full_name
    except UserNotFoundError:
        return None, "Unknown"


# -------------------------------------------------------------------------
# NEW ADMIN-SPECIFIC FUNCTIONS REQUIRED BY REACT FRONTEND
# -------------------------------------------------------------------------

async def update_user_role(db: AsyncSession, user_id: str, is_superuser: bool) -> User:
    """Admin action: Promote to Admin or Demote to User."""
    user = await get_user_by_id(db, user_id, include_inactive=True)
    user.is_superuser = is_superuser
    await db.commit()
    await db.refresh(user)
    logger.info("user_role_updated", user_id=str(user.id), is_superuser=is_superuser)
    return user


async def update_user_status(db: AsyncSession, user_id: str, is_active: bool) -> User:
    """Admin action: Activate or Deactivate a user account."""
    user = await get_user_by_id(db, user_id, include_inactive=True)
    user.is_active = is_active
    await db.commit()
    await db.refresh(user)
    logger.info("user_status_updated", user_id=str(user.id), is_active=is_active)
    return user


async def admin_reset_user_password(db: AsyncSession, user_id: str, new_password: str) -> User:
    """Admin action: Force reset a user's password without needing the old password."""
    user = await get_user_by_id(db, user_id, include_inactive=True)
    user.hashed_password = hash_password(new_password)
    await db.commit()
    logger.info("admin_forced_password_reset", user_id=str(user.id))
    return user


async def delete_user(db: AsyncSession, user_id: str) -> None:
    """Admin action: Permanently delete a user from the database."""
    user = await get_user_by_id(db, user_id, include_inactive=True)
    await db.delete(user)
    await db.commit()
    logger.info("user_deleted", user_id=str(user_id))