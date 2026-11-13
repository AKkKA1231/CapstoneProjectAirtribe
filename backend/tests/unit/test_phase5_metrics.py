"""Phase 5 metrics and configuration safety tests."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from prism.application.accounting import AccountingService
from prism.infrastructure.config import Settings
from prism.infrastructure.metrics import MetricsRegistry


class FailingAccountingRepository:
    async def settle(self, *_: object, **__: object) -> object:
        raise RuntimeError("database down")

    async def release(self, *_: object, **__: object) -> object:
        raise RuntimeError("database down")


def test_metrics_render_bounded_labels_and_values() -> None:
    metrics = MetricsRegistry()
    metrics.increment("cache_requests_total", result="hit")
    metrics.observe("http_request_duration_seconds", 0.25, method="GET", path="/health")

    rendered = metrics.render()

    assert 'prism_cache_requests_total{result="hit"} 1.0' in rendered
    assert 'prism_http_request_duration_seconds_count{method="GET",path="/health"} 1.0' in rendered
    assert 'prism_http_request_duration_seconds_sum{method="GET",path="/health"} 0.25' in rendered


@pytest.mark.asyncio
async def test_settlement_failures_are_counted() -> None:
    metrics = MetricsRegistry()
    service = AccountingService(FailingAccountingRepository(), metrics)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        await service.settle(uuid4(), Decimal("0.1"), None)  # type: ignore[arg-type]

    assert 'prism_settlement_failures_total{operation="settle"} 1.0' in metrics.render()


def test_production_rejects_wildcard_cors() -> None:
    with pytest.raises(ValidationError, match="must not contain"):
        Settings(
            environment="production",
            auth_pepper=SecretStr("safe-pepper"),
            admin_api_key=SecretStr("safe-admin"),
            expose_api_docs=False,
            cors_allowed_origins=["*"],
        )
