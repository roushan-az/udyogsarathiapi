# app/models/otp.py
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base

class OTPStore(Base):
    __tablename__ = "otp_store"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, index=True, nullable=False)
    otp_code = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at