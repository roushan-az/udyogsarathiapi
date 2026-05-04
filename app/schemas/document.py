# app/schemas/document.py
"""
Pydantic v2 schemas — the contract between the API and the React frontend.
Schema names intentionally mirror the TypeScript types in src/types/index.ts.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Enums (mirror TS types) ───────────────────────────────────────────────────

DocumentCategory = Literal["Sales", "Purchase", "Inventory", "HR", "Finance", "Legal"]
DocumentStatus   = Literal["processing", "uploaded", "failed", "queued"]
ActivityAction   = Literal["upload", "view", "download", "delete"]


# ── Base config ───────────────────────────────────────────────────────────────

class UdyogBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ── Document ──────────────────────────────────────────────────────────────────

class DocumentOut(UdyogBase):
    """
    Mirrors the TypeScript Document interface exactly.
    camelCase field names produced via alias so the React app works without
    any mapping layer.
    """
    id:           str = Field(description="UUID as string")
    fileName:     str = Field(alias="file_name")
    originalName: str = Field(alias="original_name")
    category:     DocumentCategory
    blobUrl:      str = Field(alias="blob_url")
    fileSize:     int = Field(alias="file_size")
    uploadedAt:   str = Field(alias="uploaded_at_str", description="ISO 8601 string")
    status:       DocumentStatus
    tags:         List[str] = []
    uploadedBy:   Optional[str] = Field(None, alias="uploaded_by_name")
    pageCount:    Optional[int] = Field(None, alias="page_count")

    @classmethod
    def from_orm_model(cls, doc: Any) -> "DocumentOut":
        """Build from SQLAlchemy model, handling UUID → str and datetime → ISO."""
        return cls(
            id=str(doc.id),
            file_name=doc.file_name,
            original_name=doc.original_name,
            category=doc.category.value,
            blob_url=doc.blob_url,
            file_size=doc.file_size,
            uploaded_at_str=doc.uploaded_at.isoformat(),
            status=doc.status.value,
            tags=doc.tags or [],
            uploaded_by_name=doc.uploaded_by_name,
            page_count=doc.page_count,
        )


class DocumentListResponse(UdyogBase):
    """Paginated list response — mirrors frontend PaginationState."""
    documents: List[DocumentOut]
    total:     int
    page:      int
    pageSize:  int = Field(alias="page_size")


# ── Upload ────────────────────────────────────────────────────────────────────

class UploadResponse(UdyogBase):
    """Mirrors TypeScript UploadResponse."""
    success:  bool
    message:  str
    document: Optional[DocumentOut] = None
    blobUrl:  Optional[str] = Field(None, alias="blob_url")


# ── Activity Log ──────────────────────────────────────────────────────────────

class ActivityItemOut(UdyogBase):
    """Mirrors TypeScript ActivityItem."""
    id:             str
    action:         ActivityAction
    documentName:   str = Field(alias="document_name")
    category:       DocumentCategory = Field(alias="document_category")
    timestamp:      str = Field(alias="timestamp_str")
    user:           Optional[str] = Field(None, alias="user_name")

    @classmethod
    def from_orm_model(cls, log: Any) -> "ActivityItemOut":
        return cls(
            id=str(log.id),
            action=log.action.value,
            document_name=log.document_name,
            document_category=log.document_category.value,
            timestamp_str=log.timestamp.isoformat(),
            user_name=log.user_name,
        )


# ── Dashboard Stats ───────────────────────────────────────────────────────────

class StorageItemOut(UdyogBase):
    """Mirrors TypeScript StorageItem."""
    category: DocumentCategory
    size:     int   # bytes
    count:    int


class DashboardStatsOut(UdyogBase):
    """Mirrors TypeScript DashboardStats — consumed by the Dashboard page."""
    totalDocuments:    int
    totalStorage:      int   # bytes
    documentsThisMonth: int
    categoryCounts:    Dict[str, int]
    recentActivity:    List[ActivityItemOut]
    storageByCategory: List[StorageItemOut]


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(UdyogBase):
    status:   str
    version:  str
    database: bool
    storage:  bool


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(UdyogBase):
    email:    str
    password: str


class TokenResponse(UdyogBase):
    accessToken:  str = Field(alias="access_token")
    refreshToken: str = Field(alias="refresh_token")
    tokenType:    str = Field(default="bearer", alias="token_type")


class UserOut(UdyogBase):
    id:          str
    email:       str
    fullName:    str = Field(alias="full_name")
    isActive:    bool = Field(alias="is_active")
    isSuperuser: bool = Field(alias="is_superuser")

    @classmethod
    def from_orm_model(cls, user: Any) -> "UserOut":
        return cls(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
        )


class RegisterRequest(UdyogBase):
    email:     str
    password:  str = Field(min_length=8)
    full_name: str = Field(min_length=2)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("Invalid email address")
        return v.lower().strip()