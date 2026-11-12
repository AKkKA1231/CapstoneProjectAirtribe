"""Provider-neutral chat, usage, target, and attempt models."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from prism.domain.accounting import Pricing


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=100)
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    stream: bool = False
    temperature: float = Field(default=1.0, ge=0, le=2)
    top_p: float = Field(default=1.0, gt=0, le=1)
    max_tokens: int = Field(default=256, ge=1, le=8192)
    seed: int | None = None
    stop: str | list[str] | None = None


class TokenUsage(BaseModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class ProviderResponse(BaseModel):
    body: dict[str, Any]
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class ProviderTarget:
    provider_id: UUID
    provider_name: str
    base_url: str
    api_key_env_var: str | None
    timeout_ms: int
    model: str
    pricing: Pricing


@dataclass(frozen=True, slots=True)
class ProviderAttemptResult:
    attempt_number: int
    retry_number: int
    target: ProviderTarget
    outcome: str
    started_at: datetime
    ended_at: datetime
    backoff_ms: int | None = None
    usage: TokenUsage | None = None
    cost_usd: Decimal | None = None
    http_status: int | None = None
    error_code: str | None = None
    sanitized_error: str | None = None


@dataclass(frozen=True, slots=True)
class StreamEvent:
    data: dict[str, Any]
    content_bearing: bool
    usage: TokenUsage | None = None


@dataclass(slots=True)
class ProviderStream:
    events: AsyncIterator[StreamEvent]
    close: Any


class ProviderCallError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        outcome: str,
        http_status: int | None = None,
        code: str = "provider_error",
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.outcome = outcome
        self.http_status = http_status
        self.code = code
