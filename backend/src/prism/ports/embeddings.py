"""Embedding-provider port."""

from typing import Protocol


class EmbeddingProvider(Protocol):
    model: str
    version: str
    dimensions: int

    def embed(self, text: str) -> list[float]: ...
