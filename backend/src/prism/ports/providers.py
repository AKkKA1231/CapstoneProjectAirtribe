"""Provider execution port."""

from typing import Protocol

from prism.domain.providers import (
    ChatCompletionRequest,
    ProviderResponse,
    ProviderStream,
    ProviderTarget,
)


class ProviderAdapter(Protocol):
    async def complete(
        self, target: ProviderTarget, request: ChatCompletionRequest
    ) -> ProviderResponse: ...

    async def stream(
        self, target: ProviderTarget, request: ChatCompletionRequest
    ) -> ProviderStream: ...
