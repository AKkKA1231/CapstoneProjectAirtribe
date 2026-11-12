"""Application factory, dependency, lifecycle, and logging tests."""

from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Response

from prism.api.dependencies import get_app_settings, get_database
from prism.api.routes.health import readiness
from prism.infrastructure.config import Settings
from prism.infrastructure.lifecycle import build_lifespan
from prism.infrastructure.logging import configure_logging
from prism.main import create_app


def complete_no_io_coroutine[Result](coroutine: Coroutine[Any, Any, Result]) -> Result:
    """Complete a coroutine whose test doubles guarantee no suspended I/O."""

    with pytest.raises(StopIteration) as completed:
        coroutine.send(None)
    return completed.value.value  # type: ignore[no-any-return]


class AvailableDatabase:
    async def ping(self) -> None:
        return None


class UnavailableDatabase:
    async def ping(self) -> None:
        raise ConnectionError("database unavailable")


class FakeHttpClient:
    def __init__(self, **_: object) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeDatabase:
    def __init__(self, _: Settings) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_application_factory_registers_health_routes_without_docs() -> None:
    settings = Settings(environment="test", database_enabled=False, expose_api_docs=False)

    application = create_app(settings)
    route_paths = {route.path for route in application.routes}

    assert application.title == "Prism LLM Gateway"
    assert application.openapi_url is None
    assert {"/health/live", "/health/ready"} <= route_paths


def test_dependencies_read_values_bound_to_application_state() -> None:
    settings = Settings(environment="test", database_enabled=False)
    database = object()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings, database=database))
    )

    assert complete_no_io_coroutine(get_app_settings(request)) is settings  # type: ignore[arg-type]
    assert complete_no_io_coroutine(get_database(request)) is database  # type: ignore[arg-type]


def test_database_dependency_returns_none_before_lifespan_initializes() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    assert complete_no_io_coroutine(get_database(request)) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("database", "expected_status", "expected_check"),
    [
        (None, 503, "not_initialized"),
        (AvailableDatabase(), 200, "ok"),
        (UnavailableDatabase(), 503, "unavailable"),
    ],
)
def test_readiness_reports_database_states(
    database: object,
    expected_status: int,
    expected_check: str,
) -> None:
    settings = Settings(environment="test", database_enabled=True, reconciliation_enabled=False)
    http_response = Response()

    result = complete_no_io_coroutine(
        readiness(http_response, settings, database)  # type: ignore[arg-type]
    )

    assert http_response.status_code == expected_status
    assert result.checks["database"] == expected_check


def test_lifespan_binds_and_closes_shared_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    import prism.infrastructure.lifecycle as lifecycle_module

    monkeypatch.setattr(lifecycle_module.httpx, "AsyncClient", FakeHttpClient)
    monkeypatch.setattr(lifecycle_module, "DatabaseManager", FakeDatabase)
    settings = Settings(environment="test", database_enabled=True, reconciliation_enabled=False)
    application = FastAPI()
    manager = build_lifespan(settings)(application)

    complete_no_io_coroutine(manager.__aenter__())
    database = application.state.database
    client = application.state.provider_http_client

    assert isinstance(database, FakeDatabase)
    assert isinstance(client, FakeHttpClient)
    assert database.closed is False
    assert client.closed is False

    complete_no_io_coroutine(manager.__aexit__(None, None, None))

    assert database.closed is True
    assert client.closed is True


def test_logging_supports_development_and_json_modes() -> None:
    configure_logging(Settings(environment="development", database_enabled=False))
    configure_logging(Settings(environment="test", database_enabled=False))
