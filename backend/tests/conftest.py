"""Shared pytest fixtures."""

import pytest

from prism.infrastructure.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    return Settings(environment="test", database_enabled=False, expose_api_docs=False)
