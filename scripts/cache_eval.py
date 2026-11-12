"""Evaluate deterministic cache similarity against committed hit/no-hit cases."""

import json
from pathlib import Path
from typing import TypedDict

from prism.adapters.embeddings.local import DeterministicHashEmbedding


class EvalCase(TypedDict):
    id: str
    text: str
    expected_hit: bool


class EvalSet(TypedDict):
    source: str
    threshold: float
    cases: list[EvalCase]


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    evaluation: EvalSet = json.loads(
        (repository_root / "config" / "cache_eval_cases.json").read_text(
            encoding="utf-8"
        )
    )
    embedder = DeterministicHashEmbedding()
    source = embedder.embed(evaluation["source"])
    results: list[dict[str, object]] = []

    for case in evaluation["cases"]:
        similarity = cosine(source, embedder.embed(case["text"]))
        predicted_hit = similarity >= evaluation["threshold"]
        results.append(
            {
                "id": case["id"],
                "similarity": similarity,
                "threshold": evaluation["threshold"],
                "expected_hit": case["expected_hit"],
                "predicted_hit": predicted_hit,
                "correct": predicted_hit == case["expected_hit"],
            }
        )

    report = {
        "embedding_model": embedder.model,
        "embedding_version": embedder.version,
        "dimensions": embedder.dimensions,
        "results": results,
    }
    print(json.dumps(report, indent=2))
    if not all(result["correct"] for result in results):
        raise SystemExit("cache similarity evaluation did not satisfy committed cases")


if __name__ == "__main__":
    main()
