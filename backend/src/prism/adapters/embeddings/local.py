"""Deterministic dependency-free hashing embedder for tests and local demos."""

import hashlib
import itertools
import math
import re

SYNONYMS = {
    "automobile": "car",
    "caching": "cache",
    "describe": "explain",
    "hi": "hello",
    "purchase": "buy",
    "rapid": "fast",
    "quick": "fast",
    "summary": "summarize",
}
STOP_WORDS = frozenset(
    {"a", "an", "and", "are", "for", "in", "is", "of", "on", "please", "the", "to"}
)


class DeterministicHashEmbedding:
    model = "prism-local-hash"
    version = "v1"
    dimensions = 1536

    def embed(self, text: str) -> list[float]:
        tokens = [
            SYNONYMS.get(token, token)
            for token in re.findall(r"[a-z0-9]+", text.lower())
            if token not in STOP_WORDS
        ]
        vector = [0.0] * self.dimensions
        features = tokens + [f"{left}_{right}" for left, right in itertools.pairwise(tokens)]
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]
