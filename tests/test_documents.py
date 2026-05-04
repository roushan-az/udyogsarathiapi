# tests/test_documents.py
"""
Async API tests for Udyog Sarathi document endpoints.

Run with:
    pytest tests/ -v --asyncio-mode=auto
"""

import io
import uuid
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base, get_db
from app.main import app

# ── In-memory SQLite for tests (no real PostgreSQL needed) ────────────────────
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def test_db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    TestSession = async_sessionmaker(engine, expire_on_commit=False)

    async with TestSession() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(test_db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP test client with DB dependency overridden."""
    async def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# ── Helper: minimal JPEG bytes ────────────────────────────────────────────────
def _make_jpeg_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color=(255, 128, 0))
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ── Health ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient) -> None:
    with (
        patch("app.api.v1.endpoints.health.check_db_health", return_value=True),
        patch("app.api.v1.endpoints.health.check_blob_health", return_value=True),
    ):
        response = await client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["database"] is True
    assert body["storage"] is True


@pytest.mark.asyncio
async def test_health_degraded(client: AsyncClient) -> None:
    with (
        patch("app.api.v1.endpoints.health.check_db_health", return_value=False),
        patch("app.api.v1.endpoints.health.check_blob_health", return_value=True),
    ):
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


# ── Upload ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_success(client: AsyncClient) -> None:
    """Happy path: image → PDF → blob → DB record."""
    jpeg = _make_jpeg_bytes()
    mock_blob_url = "https://udyogsarathi.blob.core.windows.net/documents/Sales/2024-03/abc123_invoice.pdf"
    mock_blob_name = "Sales/2024-03/abc123_invoice.pdf"

    with (
        patch(
            "app.services.document_service.blob_service.upload_pdf_to_blob",
            return_value=(mock_blob_url, mock_blob_name),
        ),
        patch("app.core.security.get_current_user_id", return_value="dev-user"),
    ):
        response = await client.post(
            "/api/documents/upload",
            files={"file": ("invoice.jpg", jpeg, "image/jpeg")},
            data={"category": "Sales", "tags": '["invoice","test"]'},
            headers={"Authorization": "Bearer dev"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["document"]["category"] == "Sales"
    assert body["document"]["status"] == "uploaded"
    assert "invoice.jpg" == body["document"]["originalName"]
    assert body["blobUrl"] == mock_blob_url


@pytest.mark.asyncio
async def test_upload_invalid_file_type(client: AsyncClient) -> None:
    """Non-image file should be rejected with 422."""
    with patch("app.core.security.get_current_user_id", return_value="dev-user"):
        response = await client.post(
            "/api/documents/upload",
            files={"file": ("document.pdf", b"%PDF-1.4...", "application/pdf")},
            data={"category": "Sales", "tags": "[]"},
            headers={"Authorization": "Bearer dev"},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert "UNSUPPORTED_FILE_TYPE" in body["error"]["code"]


@pytest.mark.asyncio
async def test_upload_file_too_large(client: AsyncClient) -> None:
    """File exceeding MAX_UPLOAD_SIZE_MB should be rejected."""
    large_bytes = b"x" * (11 * 1024 * 1024)  # 11 MB

    with patch("app.core.security.get_current_user_id", return_value="dev-user"):
        response = await client.post(
            "/api/documents/upload",
            files={"file": ("big.jpg", large_bytes, "image/jpeg")},
            data={"category": "Sales", "tags": "[]"},
            headers={"Authorization": "Bearer dev"},
        )

    assert response.status_code == 422
    assert "FILE_TOO_LARGE" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_upload_fail_safe_rollback(client: AsyncClient) -> None:
    """
    If DB insert fails after blob upload, the blob must be deleted.
    Verifies the fail-safe rollback behaviour.
    """
    jpeg = _make_jpeg_bytes()
    mock_blob_url = "https://udyogsarathi.blob.core.windows.net/documents/test.pdf"
    mock_blob_name = "Sales/2024-03/test.pdf"

    mock_delete = MagicMock(return_value=True)

    with (
        patch(
            "app.services.document_service.blob_service.upload_pdf_to_blob",
            return_value=(mock_blob_url, mock_blob_name),
        ),
        patch(
            "app.services.document_service.blob_service.delete_blob",
            mock_delete,
        ),
        # Force DB commit to raise
        patch.object(
            AsyncSession, "commit",
            side_effect=Exception("DB connection lost"),
        ),
        patch("app.core.security.get_current_user_id", return_value="dev-user"),
    ):
        response = await client.post(
            "/api/documents/upload",
            files={"file": ("invoice.jpg", jpeg, "image/jpeg")},
            data={"category": "Sales", "tags": "[]"},
            headers={"Authorization": "Bearer dev"},
        )

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert "TRANSACTION_ROLLBACK" in body["error"]["code"]
    # Most importantly — blob cleanup was called
    mock_delete.assert_called_once_with(mock_blob_name)


# ── List documents ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_documents_empty(client: AsyncClient) -> None:
    with patch("app.core.security.get_current_user_id", return_value="dev-user"):
        response = await client.get(
            "/api/documents/",
            headers={"Authorization": "Bearer dev"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["documents"] == []


@pytest.mark.asyncio
async def test_list_documents_pagination(client: AsyncClient) -> None:
    with patch("app.core.security.get_current_user_id", return_value="dev-user"):
        response = await client.get(
            "/api/documents/?page=1&page_size=5",
            headers={"Authorization": "Bearer dev"},
        )

    assert response.status_code == 200
    assert "page" in response.json()
    assert "pageSize" in response.json()


# ── Get single document ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_document_not_found(client: AsyncClient) -> None:
    non_existent = str(uuid.uuid4())
    with patch("app.core.security.get_current_user_id", return_value="dev-user"):
        response = await client.get(
            f"/api/documents/{non_existent}",
            headers={"Authorization": "Bearer dev"},
        )

    assert response.status_code == 404
    assert "DOCUMENT_NOT_FOUND" in response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_get_document_invalid_id(client: AsyncClient) -> None:
    with patch("app.core.security.get_current_user_id", return_value="dev-user"):
        response = await client.get(
            "/api/documents/not-a-uuid",
            headers={"Authorization": "Bearer dev"},
        )

    assert response.status_code == 404


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_and_login(client: AsyncClient) -> None:
    # Register
    reg_response = await client.post("/api/auth/register", json={
        "email": "test@example.com",
        "password": "SecurePass123",
        "full_name": "Test User",
    })
    assert reg_response.status_code == 201
    assert reg_response.json()["email"] == "test@example.com"

    # Login
    login_response = await client.post("/api/auth/login", json={
        "email": "test@example.com",
        "password": "SecurePass123",
    })
    assert login_response.status_code == 200
    body = login_response.json()
    assert "accessToken" in body
    assert "refreshToken" in body
    assert body["tokenType"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient) -> None:
    # Register first
    await client.post("/api/auth/register", json={
        "email": "user2@example.com",
        "password": "CorrectPass123",
        "full_name": "User Two",
    })

    # Wrong password
    response = await client.post("/api/auth/login", json={
        "email": "user2@example.com",
        "password": "WrongPass!",
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "password": "Pass1234!", "full_name": "Dup"}
    await client.post("/api/auth/register", json=payload)
    response = await client.post("/api/auth/register", json=payload)
    assert response.status_code == 409


# ── PDF conversion ────────────────────────────────────────────────────────────

def test_pdf_conversion_jpeg() -> None:
    from app.services.pdf_service import convert_image_to_pdf
    jpeg = _make_jpeg_bytes()
    pdf_bytes, page_count = convert_image_to_pdf(jpeg, "image/jpeg")
    assert pdf_bytes[:4] == b"%PDF"
    assert page_count == 1
    assert len(pdf_bytes) > 100


def test_pdf_conversion_png() -> None:
    from app.services.pdf_service import convert_image_to_pdf
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (200, 200), (0, 128, 255, 255)).save(buf, format="PNG")
    pdf_bytes, _ = convert_image_to_pdf(buf.getvalue(), "image/png")
    assert pdf_bytes[:4] == b"%PDF"


def test_pdf_conversion_corrupt_image() -> None:
    from app.core.exceptions import PDFConversionError
    from app.services.pdf_service import convert_image_to_pdf
    with pytest.raises(PDFConversionError):
        convert_image_to_pdf(b"not-an-image", "image/jpeg")