"""Application startup, background recovery, and shutdown lifecycle."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

import httpx
import structlog
from fastapi import FastAPI

from prism.adapters.database.operations import PostgresReconciliationRepository
from prism.adapters.database.session import DatabaseManager
from prism.application.operations import ReconciliationService
from prism.infrastructure.config import Settings


async def _reconciliation_loop(
    database: DatabaseManager, settings: Settings, stop: asyncio.Event
) -> None:
    logger = structlog.get_logger(__name__)
    service = ReconciliationService(
        PostgresReconciliationRepository(database), settings.reconciliation_stale_seconds
    )
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.reconciliation_interval_seconds)
            continue
        except TimeoutError:
            pass
        try:
            result = await service.run(datetime.now(UTC))
            logger.info(
                "reservation_reconciliation",
                lock_acquired=result.lock_acquired,
                examined=result.examined,
                released=result.released,
                flagged=result.flagged,
            )
        except Exception as error:
            logger.error("reservation_reconciliation_failed", error_type=type(error).__name__)


def build_lifespan(settings: Settings):  # type: ignore[no-untyped-def]
    """Build a lifespan function closed over immutable application settings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger = structlog.get_logger(__name__)
        database = DatabaseManager(settings) if settings.database_enabled else None
        timeout = httpx.Timeout(
            connect=settings.provider_connect_timeout_seconds,
            read=settings.provider_read_timeout_seconds,
            write=settings.provider_write_timeout_seconds,
            pool=settings.provider_pool_timeout_seconds,
        )
        limits = httpx.Limits(
            max_connections=settings.provider_max_connections,
            max_keepalive_connections=settings.provider_max_keepalive_connections,
        )
        provider_http_client = httpx.AsyncClient(timeout=timeout, limits=limits)

        app.state.database = database
        app.state.provider_http_client = provider_http_client
        reconciliation_stop = asyncio.Event()
        reconciliation_task = (
            asyncio.create_task(_reconciliation_loop(database, settings, reconciliation_stop))
            if database is not None and settings.reconciliation_enabled
            else None
        )
        logger.info("application_started", environment=settings.environment)

        try:
            yield
        finally:
            reconciliation_stop.set()
            if reconciliation_task is not None:
                reconciliation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reconciliation_task
            await provider_http_client.aclose()
            if database is not None:
                await database.close()
            logger.info("application_stopped")

    return lifespan
