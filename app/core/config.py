# app/core/config.py
from functools import lru_cache
from typing import List
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
    DEBUG: bool = True
    API_PREFIX: str = "/api"

    # ── Security ─────────────────────────────────────────────────────────────
    SECRET_KEY: str = "your_actual_long_random_string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    ADMIN_SECRET_KEY: str = ""
    # ── CORS ─────────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = ""

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    # ── Database (Supabase) ──────────────────────────────────────────────────
    DATABASE_URL: str = ""
    DATABASE_SYNC_URL: str = ""
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30

    # ── Storage Configuration ─────────────────────────────────────────────────
    STORAGE_MODE: str = "S3"
    USE_LOCAL_STORAGE: bool = False
    LOCAL_UPLOAD_DIR: str = "local_storage"

    # Cloudflare R2 / S3 Settings (Fetched from .env)
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = ""
    R2_ENDPOINT_URL: str = ""

    # ── File Upload & PDF ─────────────────────────────────────────────────────
    MAX_UPLOAD_SIZE_MB: int = 10
    ALLOWED_IMAGE_TYPES: str = "image/jpeg,image/png,image/webp,image/tiff,image/bmp"
    PDF_IMAGE_QUALITY: int = 95
    PDF_DPI: int = 150

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def allowed_image_types_list(self) -> List[str]:
        return [t.strip() for t in self.ALLOWED_IMAGE_TYPES.split(",")]

    # ── Logging & Helpers ─────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "console"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()