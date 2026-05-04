# app/main.py
"""
Udyog Sarathi — FastAPI Application Entry Point

This file creates the FastAPI app, configures:
  • CORS (React Static Web App origin)
  • Lifespan (startup / shutdown hooks)
  • All API routers
  • Custom exception handlers
  • OpenAPI metadata
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.db.base import check_db_health, engine

logger = get_logger(__name__)


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # ── Startup ──────────────────────────────────────────────────────────────
    setup_logging()

    logger.info(
        "app_startup",
        name=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        debug=settings.DEBUG,
    )

    # Verify database connectivity
    db_ok = await check_db_health()
    if not db_ok:
        logger.error("startup_db_unavailable")
        # Don't crash — let health endpoint surface this
    else:
        logger.info("startup_db_ok")

    yield  # ← app is running

    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("app_shutdown")
    await engine.dispose()


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "**Udyog Sarathi** — Enterprise Document Management System\n\n"
            "Upload images → convert to PDF in memory → store in Azure Blob → "
            "record in PostgreSQL (with fail-safe rollback).\n\n"
            "Built with FastAPI · Azure Blob Storage · PostgreSQL · SQLAlchemy async"
        ),
        openapi_url=f"{settings.API_PREFIX}/openapi.json",
        docs_url=f"{settings.API_PREFIX}/docs",
        redoc_url=f"{settings.API_PREFIX}/redoc",
        lifespan=lifespan,
        # Disable automatic redirect so React router handles trailing slashes
        redirect_slashes=False,
    )

    # ── Middleware ────────────────────────────────────────────────────────────

    # CORS — allow React dev server and Azure Static Web App
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Total-Count", "X-Request-ID"],
    )

    # Gzip compression for JSON responses
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # ── Request ID middleware (simple) ────────────────────────────────────────
    import uuid
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request

    class RequestIDMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
            import structlog
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(request_id=request_id)
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response

    app.add_middleware(RequestIDMiddleware)

    # ── Exception handlers ────────────────────────────────────────────────────
    register_exception_handlers(app)

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(api_router, prefix=settings.API_PREFIX)

    # ── Root route ────────────────────────────────────────────────────────────
    @app.get("/", include_in_schema=False)
    async def root():
        return JSONResponse({
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "environment": settings.ENVIRONMENT,
            "docs": f"{settings.API_PREFIX}/docs",
            "health": f"{settings.API_PREFIX}/health",
        })

    return app


# ── Module-level app instance (used by uvicorn) ───────────────────────────────
app = create_app()