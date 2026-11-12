"""Phase 2 API error-contract tests."""

import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from prism.api.errors import prism_error_handler
from prism.api.routes.models import list_models
from prism.domain.errors import InvalidApiKeyError, RateLimitExceededError
from prism.domain.security import TenantContext


async def _response_for(error: Exception):
    return await prism_error_handler(SimpleNamespace(), error)  # type: ignore[arg-type]


async def test_authentication_error_is_openai_compatible() -> None:
    response = await _response_for(InvalidApiKeyError("invalid"))
    payload = json.loads(response.body)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert payload == {
        "error": {
            "message": "invalid",
            "type": "authentication_error",
            "param": None,
            "code": "invalid_api_key",
        }
    }


async def test_rate_limit_error_has_stable_code() -> None:
    response = await _response_for(RateLimitExceededError("too many"))
    payload = json.loads(response.body)

    assert response.status_code == 429
    assert payload["error"]["code"] == "rate_limit_exceeded"


async def test_model_list_contains_only_tenant_allowlist() -> None:
    tenant = TenantContext(
        uuid4(),
        "Tenant",
        10,
        Decimal("1"),
        frozenset({"smart", "fast"}),
    )

    response = await list_models(tenant)

    assert [model.id for model in response.data] == ["fast", "smart"]
