"""Configuration validation tests."""

import pytest
from pydantic import ValidationError

from prism.infrastructure.config import Settings


def test_development_defaults_are_safe_for_local_startup() -> None:
    settings = Settings(environment="development", database_enabled=False)

    assert settings.app_name == "Prism LLM Gateway"
    assert settings.database_enabled is False


def test_production_rejects_development_secrets() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", expose_api_docs=False)


def test_production_requires_api_docs_to_be_disabled() -> None:
    with pytest.raises(ValidationError, match="PRISM_EXPOSE_API_DOCS"):
        Settings(
            environment="production",
            auth_pepper="a-production-pepper",
            admin_api_key="a-production-admin-key",
            expose_api_docs=True,
        )
