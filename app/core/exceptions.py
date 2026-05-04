# app/core/exceptions.py
"""
Custom exception classes and FastAPI exception handlers.
All exceptions produce consistent JSON error bodies.
"""

from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Custom exception hierarchy ───────────────────────────────────────────────

class UdyogBaseException(Exception):
    """Base class for all Udyog Sarathi exceptions."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, detail: str = "An unexpected error occurred", **context: Any):
        self.detail = detail
        self.context = context
        super().__init__(detail)


class FileValidationError(UdyogBaseException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    error_code = "FILE_VALIDATION_ERROR"


class FileTooLargeError(FileValidationError):
    error_code = "FILE_TOO_LARGE"


class UnsupportedFileTypeError(FileValidationError):
    error_code = "UNSUPPORTED_FILE_TYPE"


class PDFConversionError(UdyogBaseException):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code = "PDF_CONVERSION_ERROR"


class BlobStorageError(UdyogBaseException):
    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "BLOB_STORAGE_ERROR"


class BlobUploadError(BlobStorageError):
    error_code = "BLOB_UPLOAD_ERROR"


class BlobDeleteError(BlobStorageError):
    error_code = "BLOB_DELETE_ERROR"


class DatabaseError(UdyogBaseException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "DATABASE_ERROR"


class DocumentNotFoundError(UdyogBaseException):
    status_code = status.HTTP_404_NOT_FOUND
    error_code = "DOCUMENT_NOT_FOUND"


class TransactionRollbackError(UdyogBaseException):
    """Raised when DB insert fails — triggers blob cleanup."""
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code = "TRANSACTION_ROLLBACK"


# ── Error response builder ────────────────────────────────────────────────────

def _error_response(
    status_code: int,
    error_code: str,
    detail: str,
    extra: Optional[dict] = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "success": False,
        "error": {
            "code": error_code,
            "message": detail,
        },
    }
    if extra:
        body["error"].update(extra)
    return JSONResponse(status_code=status_code, content=body)


# ── FastAPI exception handlers ────────────────────────────────────────────────

def register_exception_handlers(app: FastAPI) -> None:
    """Attach all custom handlers to the FastAPI app instance."""

    @app.exception_handler(UdyogBaseException)
    async def udyog_exception_handler(
        request: Request, exc: UdyogBaseException
    ) -> JSONResponse:
        logger.error(
            "udyog_exception",
            error_code=exc.error_code,
            detail=exc.detail,
            path=request.url.path,
            **exc.context,
        )
        return _error_response(exc.status_code, exc.error_code, exc.detail)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        return _error_response(
            exc.status_code,
            f"HTTP_{exc.status_code}",
            str(exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        logger.warning("validation_error", errors=errors, path=request.url.path)
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "VALIDATION_ERROR",
            "Request validation failed",
            {"errors": errors},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("unhandled_exception", path=request.url.path, exc_info=exc)
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "An internal server error occurred",
        )