# app/core/config.py
"""
Central application configuration loaded from environment variables / .env file.
All settings are validated at startup — misconfigured apps fail fast.
"""

from functools import lru_cache
from typing import List

from pydantic import AnyHttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    APP_NAME: str = "Udyog Sarathi"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    API_PREFIX: str = "/api"

    # ── Security ─────────────────────────────────────────────────────────────
    SECRET_KEY: str = "CHANGE_ME"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── CORS ─────────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    # ── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://localhost/udyog_sarathi"
    DATABASE_SYNC_URL: str = "postgresql+psycopg2://localhost/udyog_sarathi"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30

    # ── Storage Configuration ─────────────────────────────────────────────────
    USE_LOCAL_STORAGE: bool = True
    STORAGE_MODE: str = "S3"  # Options: LOCAL, AZURE, S3

    # Cloudflare R2 / S3 Settings
    R2_ACCOUNT_ID: str = "74a3ad8b6d42ad00c97803ed2a068ebb"
    R2_ACCESS_KEY_ID: str = "576077631e019ff430761074f788f464"
    R2_SECRET_ACCESS_KEY: str = "a0f0cbdc3116832678d619889d6141d9967800757d93279eecfa2379501b780e"
    R2_BUCKET_NAME: str = "udyog-sarathi-docs"
    R2_ENDPOINT_URL: str = "https://74a3ad8b6d42ad00c97803ed2a068ebb.r2.cloudflarestorage.com"

    # ── Azure Blob Storage ────────────────────────────────────────────────────
    AZURE_STORAGE_ACCOUNT_NAME: str = "udyogsarathi"
    AZURE_STORAGE_CONTAINER_NAME: str = "documents"
    AZURE_STORAGE_CONNECTION_STRING: str = ""
    AZURE_STORAGE_ACCOUNT_KEY: str = ""
    USE_MANAGED_IDENTITY: bool = False

    # ── Local Storage Mock (ADD THIS) ─────────────────────────────────────────
    USE_LOCAL_STORAGE: bool = True
    LOCAL_UPLOAD_DIR: str = "local_uploads"

    # ── File Upload ───────────────────────────────────────────────────────────
    MAX_UPLOAD_SIZE_MB: int = 10
    ALLOWED_IMAGE_TYPES: str = "image/jpeg,image/png,image/webp,image/tiff,image/bmp"

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def allowed_image_types_list(self) -> List[str]:
        return [t.strip() for t in self.ALLOWED_IMAGE_TYPES.split(",")]

    # ── PDF ───────────────────────────────────────────────────────────────────
    PDF_IMAGE_QUALITY: int = 95
    PDF_DPI: int = 150

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    @model_validator(mode="after")
    def validate_secret_key(self) -> "Settings":
        if self.ENVIRONMENT == "production" and self.SECRET_KEY == "CHANGE_ME":
            raise ValueError("SECRET_KEY must be changed in production!")
        return self

    @field_validator("DB_POOL_SIZE", "DB_MAX_OVERFLOW")
    @classmethod
    def validate_positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Pool size must be at least 1")
        return v

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def azure_blob_base_url(self) -> str:
        return f"https://{self.AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net/{self.AZURE_STORAGE_CONTAINER_NAME}"


@lru_cache
def get_settings() -> Settings:
    """Returns a cached singleton Settings instance."""
    return Settings()


# Convenience alias used throughout the app
settings = get_settings()