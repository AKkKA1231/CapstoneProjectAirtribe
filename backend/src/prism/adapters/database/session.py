"""Asynchronous SQLAlchemy engine and session management."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from prism.infrastructure.config import Settings


class DatabaseManager:
    """Own the application's database engine and connection pool."""

    def __init__(self, settings: Settings) -> None:
        self._engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_pre_ping=True,
            connect_args={
                "command_timeout": settings.database_statement_timeout_seconds,
                "server_settings": {
                    "application_name": settings.app_name,
                    "lock_timeout": f"{settings.database_lock_timeout_ms}ms",
                    "statement_timeout": (
                        f"{settings.database_statement_timeout_seconds * 1000}ms"
                    ),
                },
            },
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def ping(self) -> None:
        """Verify that PostgreSQL accepts a trivial query."""

        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session whose transaction boundaries are owned by the caller."""

        async with self._session_factory() as session:
            yield session

    async def close(self) -> None:
        """Dispose of all pooled connections."""

        await self._engine.dispose()
