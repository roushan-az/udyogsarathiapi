import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Shared literals ───────────────────────────────────────────────────────────

DocumentCategory = Literal["Sales", "Purchase", "Inventory", "HR", "Finance", "Legal"]
DocumentStatus = Literal["processing", "uploaded", "failed", "queued"]
ActivityAction = Literal["upload", "view", "download", "delete"]


class UdyogBase(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        # Set to True if you want React to always receive camelCase
        serialize_by_alias=False,
    )


# ── Auth (Unchanged) ──────────────────────────────────────────────────────────

class LoginRequest(UdyogBase):
    email: str
    password: str


class RegisterRequest(UdyogBase):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Enter a valid email address")
        return v.lower().strip()

    @field_validator("full_name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return v.strip()


class TokenResponse(UdyogBase):
    accessToken: str = Field(alias="access_token")
    refreshToken: str = Field(alias="refresh_token")
    tokenType: str = Field(default="bearer", alias="token_type")


class UserOut(UdyogBase):
    id: str
    email: str
    fullName: str = Field(validation_alias="full_name", alias="full_name")
    isActive: bool = Field(validation_alias="is_active", alias="is_active")
    isSuperuser: bool = Field(validation_alias="is_superuser", alias="is_superuser")

    @classmethod
    def from_orm_model(cls, user: Any) -> "UserOut":
        return cls(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
        )


# ── Document (Updated for Generic Storage) ───────────────────────────────────

class DocumentOut(UdyogBase):
    """Mirrors TypeScript Document interface — generic storage fields."""
    id: str
    fileName: str = Field(alias="file_name")
    originalName: str = Field(alias="original_name")
    category: DocumentCategory

    # Changed from blobUrl to storageUrl to support R2/S3
    storageUrl: str = Field(alias="storage_url", validation_alias="blob_url")

    fileSize: int = Field(alias="file_size")
    uploadedAt: str = Field(alias="uploaded_at_str")
    status: DocumentStatus
    tags: List[str] = []
    uploadedBy: Optional[str] = Field(None, alias="uploaded_by_name")
    pageCount: Optional[int] = Field(None, alias="page_count")

    @classmethod
    def from_orm_model(cls, doc: Any) -> "DocumentOut":
        """
        Maps SQLAlchemy model to Pydantic.
        Note: uses .storage_url instead of .blob_url.
        """
        return cls(
            id=str(doc.id),
            file_name=doc.file_name,
            original_name=doc.original_name,
            category=doc.category.value if hasattr(doc.category, 'value') else doc.category,
            # Link this to your updated SQLAlchemy model field
            storage_url=getattr(doc, 'storage_url', getattr(doc, 'blob_url', "")),
            file_size=doc.file_size,
            uploaded_at_str=doc.uploaded_at.isoformat(),
            status=doc.status.value if hasattr(doc.status, 'value') else doc.status,
            tags=doc.tags or [],
            uploaded_by_name=doc.uploaded_by_name,
            page_count=doc.page_count,
        )


class DocumentListResponse(UdyogBase):
    documents: List[DocumentOut]
    total: int
    page: int
    pageSize: int = Field(alias="page_size")


class UploadResponse(UdyogBase):
    success: bool
    message: str
    document: Optional[DocumentOut] = None
    storageUrl: Optional[str] = Field(None, alias="storage_url")


# ── Activity ──────────────────────────────────────────────────────────────────

class ActivityItemOut(UdyogBase):
    id: str
    action: ActivityAction
    documentName: str = Field(alias="document_name")
    category: DocumentCategory = Field(alias="document_category")
    timestamp: str = Field(alias="timestamp_str")
    user: Optional[str] = Field(None, alias="user_name")

    @classmethod
    def from_orm_model(cls, log: Any) -> "ActivityItemOut":
        return cls(
            id=str(log.id),
            action=log.action.value if hasattr(log.action, 'value') else log.action,
            document_name=log.document_name,
            document_category=log.document_category.value if hasattr(log.document_category,
                                                                     'value') else log.document_category,
            timestamp_str=log.timestamp.isoformat(),
            user_name=log.user_name,
        )


# ── Dashboard (Updated) ──────────────────────────────────────────────────────

class StorageItemOut(UdyogBase):
    category: DocumentCategory
    size: int
    count: int


class DashboardStatsOut(UdyogBase):
    totalDocuments: int
    totalStorage: int
    documentsThisMonth: int
    categoryCounts: Dict[str, int]
    recentActivity: List[ActivityItemOut]
    storageByCategory: List[StorageItemOut]


# ── Health (Updated) ─────────────────────────────────────────────────────────

class HealthResponse(UdyogBase):
    status: str
    version: str
    database: bool
    # Generic 'storage' instead of 'blob_storage'
    storage: bool