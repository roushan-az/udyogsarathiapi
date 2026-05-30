# app/services/otp_service.py
import random
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import BackgroundTasks
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig

from app.models.otp import OTPStore


# ── 1. Email Sending Logic ───────────────────────────────────────────────────
class EmailOTPProvider:
    def __init__(self, conf: ConnectionConfig):
        self.fastmail = FastMail(conf)

    async def send_otp(self, email: str, otp: str, background_tasks: BackgroundTasks) -> None:
        message = MessageSchema(
            subject="Udyog Sarathi - Password Reset Code",
            recipients=[email],
            body=f"""
            <div style="font-family: sans-serif; color: #333;">
                <h2>Password Reset Request</h2>
                <p>Your one-time password (OTP) is: <strong style="font-size: 24px; color: #f97316;">{otp}</strong></p>
                <p>This code will expire in 5 minutes.</p>
                <p>If you did not request this, please ignore this email and your password will remain unchanged.</p>
            </div>
            """,
            subtype="html"
        )
        background_tasks.add_task(self.fastmail.send_message, message)


# ── 2. PostgreSQL Storage Logic ──────────────────────────────────────────────
async def generate_and_store_otp(db: AsyncSession, email: str, expiry_minutes: int = 5) -> str:
    """Generates a 6-digit OTP and stores it in PostgreSQL."""
    await db.execute(delete(OTPStore).where(OTPStore.email == email))

    otp_code = str(random.randint(100000, 999999))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)

    new_otp = OTPStore(email=email, otp_code=otp_code, expires_at=expires_at)
    db.add(new_otp)
    await db.commit()

    return otp_code


async def verify_and_delete_otp(db: AsyncSession, email: str, submitted_otp: str) -> bool:
    """Validates the OTP from PostgreSQL and deletes it if valid."""
    result = await db.execute(
        select(OTPStore).where(OTPStore.email == email, OTPStore.otp_code == submitted_otp)
    )
    otp_record = result.scalar_one_or_none()

    if not otp_record:
        return False

    if otp_record.is_expired:
        await db.execute(delete(OTPStore).where(OTPStore.id == otp_record.id))
        await db.commit()
        return False

    await db.execute(delete(OTPStore).where(OTPStore.id == otp_record.id))
    await db.commit()
    return True