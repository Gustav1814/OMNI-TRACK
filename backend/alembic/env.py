"""
OmniTrack AI — Alembic Environment Configuration
Async migration support for SQLAlchemy + PostgreSQL.

HOW TO USE:
  1. Create a new migration:
     alembic revision --autogenerate -m "add new column"
  
  2. Apply all pending migrations:
     alembic upgrade head
  
  3. Rollback one migration:
     alembic downgrade -1
  
  4. View migration history:
     alembic history
"""

import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

# Import your models so that Alembic can "see" them
from app.database import Base
from app.models.user import User
from app.models.camera import Camera
from app.models.detection import Detection
from app.models.embedding import Embedding
from app.models.audit_log import AuditLog
from app.models.analytics import (
    FootTraffic, CustomerJourney, DemographicSnapshot,
    StoreVibeScore, PeakHoursData,
)
from app.config import settings

# Alembic Config object
config = context.config

# Override sqlalchemy.url from settings
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Setup logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL without connecting)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
