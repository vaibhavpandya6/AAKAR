"""Async SQLAlchemy engine and session management."""

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.pool import NullPool

from config import settings
from db.models import Base

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database engine and session lifecycle."""

    def __init__(self):
        """Initialize database manager."""
        self.engine = None
        self.async_session_factory = None

    async def init(self):
        """Initialize async engine and session factory."""
        self.engine = create_async_engine(
            settings.postgres_url,
            echo=settings.environment == "development",
            poolclass=NullPool,
            pool_pre_ping=True,
        )
        self.async_session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
        logger.info("Database engine initialized", database_url=settings.postgres_url)

    async def close(self):
        """Close all connections and dispose of engine."""
        if self.engine:
            await self.engine.dispose()
            logger.info("Database engine disposed")

    async def create_tables(self):
        """Create all tables defined in models."""
        if not self.engine:
            await self.init()
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created")

    async def drop_tables(self):
        """Drop all tables (use with caution in development only)."""
        if not self.engine:
            await self.init()
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        logger.warning("Database tables dropped")

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get database session as async generator for dependency injection."""
        if not self.async_session_factory:
            await self.init()
        async with self.async_session_factory() as session:
            try:
                yield session
            except Exception as e:
                await session.rollback()
                logger.error("Database session error", error=str(e))
                raise
            finally:
                await session.close()


# Global database manager instance
db_manager = DatabaseManager()


# ============================================================================
# FastAPI Dependencies
# ============================================================================


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database sessions.

    Usage:
        @app.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Item))
            return result.scalars().all()
    """
    async for session in db_manager.get_session():
        yield session


# ============================================================================
# Initialization Functions
# ============================================================================


async def init_db():
    """Initialize database on application startup."""
    try:
        await db_manager.init()
        await db_manager.create_tables()
        logger.info("Database initialization complete")
    except Exception as e:
        logger.error("Database initialization failed", error=str(e))
        raise


async def close_db():
    """Close database connections on application shutdown."""
    try:
        await db_manager.close()
        logger.info("Database shutdown complete")
    except Exception as e:
        logger.error("Database shutdown error", error=str(e))
        raise
