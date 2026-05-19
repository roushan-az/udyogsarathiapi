from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool # <-- 1. ADD THIS IMPORT

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Declarative base ─────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """All ORM models inherit from this class."""
    pass


# ── Engine ───────────────────────────────────────────────────────────────────

# app/db/base.py
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,

    # 2. USE NULLPOOL: Let Supabase handle all pooling!
    poolclass=NullPool,

    # 3. REMOVE pool_size, max_overflow, pool_timeout, and pool_recycle.
    # 4. ADD the statement_cache_size bypass
    connect_args={
        "statement_cache_size": 0
    }
)

# ── Session factory ───────────────────────────────────────────────────────────

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yields an AsyncSession and ensures it is closed after the request.
    Use as a FastAPI Depends().
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Context manager version (for service layer) ───────────────────────────────

@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for use outside of FastAPI request scope.
    e.g. background tasks, startup hooks.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Health check ─────────────────────────────────────────────────────────────

async def check_db_health() -> bool:
    """Ping the database. Returns True if reachable."""
    from sqlalchemy import text
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("db_health_check_failed", error=str(exc))
        return False