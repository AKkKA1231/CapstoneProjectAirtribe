"""Validated application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEVELOPMENT_AUTH_PEPPER = "development-only-change-this-auth-pepper"
DEVELOPMENT_ADMIN_KEY = "development-only-change-this-admin-key"


class Settings(BaseSettings):
    """Prism settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        env_prefix="PRISM_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Prism LLM Gateway"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    expose_api_docs: bool = True
    cors_allowed_origins: list[str] = ["http://localhost:5173"]

    database_enabled: bool = True
    database_url: str = "postgresql+asyncpg://prism:prism@localhost:5432/prism"
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    database_statement_timeout_seconds: int = Field(default=15, ge=1, le=300)
    database_lock_timeout_ms: int = Field(default=2_000, ge=100, le=60_000)
    seed_config_dir: Path = Path("../config")

    provider_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    provider_read_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    provider_write_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    provider_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    provider_max_connections: int = Field(default=100, ge=1, le=1_000)
    provider_max_keepalive_connections: int = Field(default=20, ge=0, le=500)
    provider_max_retries: int = Field(default=1, ge=0, le=5)
    provider_backoff_base_ms: int = Field(default=50, ge=0, le=10_000)

    cache_enabled: bool = True
    cache_ttl_seconds: int = Field(default=86_400, gt=0, le=2_592_000)
    cache_similarity_threshold: float = Field(default=0.82, ge=0, le=1)
    cache_schema_version: int = Field(default=1, gt=0)
    cache_maximum_temperature: float = Field(default=0.2, ge=0, le=2)

    reconciliation_enabled: bool = True
    reconciliation_interval_seconds: int = Field(default=300, ge=10, le=86_400)
    reconciliation_stale_seconds: int = Field(default=900, ge=60, le=604_800)

    auth_pepper: SecretStr = SecretStr(DEVELOPMENT_AUTH_PEPPER)
    admin_api_key: SecretStr = SecretStr(DEVELOPMENT_ADMIN_KEY)

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Self:
        """Refuse known development credentials in production."""

        if self.environment != "production":
            return self

        if self.auth_pepper.get_secret_value() == DEVELOPMENT_AUTH_PEPPER:
            raise ValueError("PRISM_AUTH_PEPPER must be changed in production")
        if self.admin_api_key.get_secret_value() == DEVELOPMENT_ADMIN_KEY:
            raise ValueError("PRISM_ADMIN_API_KEY must be changed in production")
        if self.expose_api_docs:
            raise ValueError("PRISM_EXPOSE_API_DOCS must be false in production")
        if "*" in self.cors_allowed_origins:
            raise ValueError("PRISM_CORS_ALLOWED_ORIGINS must not contain '*' in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
