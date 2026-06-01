"""
OmniTrack AI — Async Database Engine
Enterprise-grade SQLAlchemy async pool + pgvector support.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def _engine_connect_args() -> dict:
    """asyncpg server settings for query safety and observability."""
    args: dict = {
        "server_settings": {
            "application_name": "omnitrack_api",
        }
    }
    if settings.DB_STATEMENT_TIMEOUT_MS > 0:
        args["server_settings"]["statement_timeout"] = str(settings.DB_STATEMENT_TIMEOUT_MS)
    return args


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,
    connect_args=_engine_connect_args(),
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """Create all tables (dev fallback only — prefer Alembic migrations)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
