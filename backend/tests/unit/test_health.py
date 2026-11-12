"""Health endpoint tests."""

from collections.abc import Coroutine
from typing import Any

import pytest
from fastapi import Response

from prism.api.routes.health import liveness, readiness
from prism.infrastructure.config import Settings


def complete_no_io_coroutine[Result](coroutine: Coroutine[Any, Any, Result]) -> Result:
    """Complete a handler branch that must not perform asynchronous I/O."""

    with pytest.raises(StopIteration) as completed:
        coroutine.send(None)
    return completed.value.value  # type: ignore[no-any-return]


def test_liveness_reports_process_health(test_settings: Settings) -> None:
    response = complete_no_io_coroutine(liveness(test_settings))

    assert response.model_dump() == {
        "status": "ok",
        "service": "Prism LLM Gateway",
        "version": "0.1.0",
        "checks": {"process": "ok"},
    }


def test_readiness_supports_database_disabled_test_mode(test_settings: Settings) -> None:
    http_response = Response()
    response = complete_no_io_coroutine(readiness(http_response, test_settings, None))

    assert http_response.status_code == 200
    assert response.checks == {"database": "disabled"}
