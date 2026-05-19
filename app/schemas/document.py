import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Shared literals ───────────────────────────────────────────────────────────

DocumentCategory = Literal["Sales", "Purchase", "Inventory", "HR", "Finance", "Legal"]
DocumentStatus   = Literal["processing", "uploaded", "failed", "queued"]
ActivityAction   = Literal["upload", "view", "download", "delete"]


class UdyogBase(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        # Set to False so Python variable names (camelCase) are used for JSON output
        serialize_by_alias=False,
    )


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(UdyogBase):
    email:    str
    password: str


class RegisterRequest(UdyogBase):
    email:     str = Field(min_length=5, max_length=255)
    password:  str = Field(min_length=8, max_length=128)
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
    # Removed alias to ensure React sees "accessToken" instead of "access_token"
    accessToken:  str = Field(validation_alias="access_token")
    refreshToken: str = Field(validation_alias="refresh_token")
    tokenType:    str = Field(default="bearer", validation_alias="token_type")


class UserOut(UdyogBase):
    id:          str
    email:       str
    # validation_alias: Reads from DB 'full_name'
    # Field name: Exports to JSON as 'fullName'
    fullName:    str  = Field(validation_alias="full_name")
    isActive:    bool = Field(validation_alias="is_active")
    isSuperuser: bool = Field(validation_alias="is_superuser")

    @classmethod
    def from_orm_model(cls, user: Any) -> "UserOut":
        return cls(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
        )


# ── Document (Generic Storage Updates) ────────────────────────────────────────

class DocumentOut(UdyogBase):
    """Mirrors TypeScript Document interface — generic storage fields."""
    id:           str
    fileName:     str          = Field(validation_alias="file_name")
    originalName: str          = Field(validation_alias="original_name")
    category:     DocumentCategory
    # Maps SQLAlchemy 'blob_url' to Pydantic 'storageUrl'
    storageUrl:   str          = Field(validation_alias="blob_url")
    fileSize:     int          = Field(validation_alias="file_size")
    uploadedAt:   str          = Field(validation_alias="uploaded_at_str")
    status:       DocumentStatus
    tags:         List[str]    = []
    uploadedBy:   Optional[str]= Field(None, validation_alias="uploaded_by_name")
    pageCount:    Optional[int]= Field(None, validation_alias="page_count")

    @classmethod
    def from_orm_model(cls, doc: Any) -> "DocumentOut":
        return cls(
            id=str(doc.id),
            file_name=doc.file_name,
            original_name=doc.original_name,
            category=doc.category.value if hasattr(doc.category, 'value') else doc.category,
            blob_url=getattr(doc, 'blob_url', ""),
            file_size=doc.file_size,
            uploaded_at_str=doc.uploaded_at.isoformat(),
            status=doc.status.value if hasattr(doc.status, 'value') else doc.status,
            tags=doc.tags or [],
            uploaded_by_name=doc.uploaded_by_name,
            page_count=doc.page_count,
        )


class DocumentListResponse(UdyogBase):
    documents: List[DocumentOut]
    total:     int
    page:      int
    pageSize:  int = Field(validation_alias="page_size")


class UploadResponse(UdyogBase):
    success:    bool
    message:    str
    document:   Optional[DocumentOut] = None
    storageUrl: Optional[str]         = Field(None, validation_alias="blob_url")


# ── Activity ──────────────────────────────────────────────────────────────────

class ActivityItemOut(UdyogBase):
    id:           str
    action:       ActivityAction
    documentName: str            = Field(validation_alias="document_name")
    category:     DocumentCategory = Field(validation_alias="document_category")
    timestamp:    str            = Field(validation_alias="timestamp_str")
    user:         Optional[str]  = Field(None, validation_alias="user_name")

    @classmethod
    def from_orm_model(cls, log: Any) -> "ActivityItemOut":
        return cls(
            id=str(log.id),
            action=log.action.value if hasattr(log.action, 'value') else log.action,
            document_name=log.document_name,
            document_category=log.document_category.value if hasattr(log.document_category, 'value') else log.document_category,
            timestamp_str=log.timestamp.isoformat(),
            user_name=log.user_name,
        )


# ── Dashboard ─────────────────────────────────────────────────────────────────

class StorageItemOut(UdyogBase):
    category: DocumentCategory
    size:     int
    count:    int


class DashboardStatsOut(UdyogBase):
    totalDocuments:     int
    totalStorage:       int
    documentsThisMonth: int
    categoryCounts:     Dict[str, int]
    recentActivity:     List[ActivityItemOut]
    storageByCategory:  List[StorageItemOut]


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(UdyogBase):
    status:   str
    version:  str
    database: bool
    storage:  bool