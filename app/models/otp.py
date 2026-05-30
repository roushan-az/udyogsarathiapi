# app/models/otp.py
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Integer
from app.db.base import Base

class OTPStore(Base):
    __tablename__ = "otp_store"

    id = Column(Integer, primary_key=True, autoincrement=True) # <-- Fixed
    email = Column(String, index=True, nullable=False)
    otp_code = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at