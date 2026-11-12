"""Evaluate Prism's versioned routing classifier against the committed baseline."""

import json
from pathlib import Path
from typing import TypedDict

from prism.application.routing import RuleBasedRoutingClassifier, length_only_baseline


class EvalCase(TypedDict):
    id: str
    prompt: str
    expected: str
    trap: bool


def main() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    cases: list[EvalCase] = json.loads(
        (repository_root / "config" / "routing_eval_cases.json").read_text(
            encoding="utf-8"
        )
    )
    classifier = RuleBasedRoutingClassifier()
    results: list[dict[str, object]] = []
    classifier_correct = 0
    baseline_correct = 0
    trap_correct = 0
    traps = 0

    for case in cases:
        decision = classifier.classify(case["prompt"])
        baseline = length_only_baseline(case["prompt"])
        correct = decision.tier == case["expected"]
        classifier_correct += int(correct)
        baseline_correct += int(baseline == case["expected"])
        if case["trap"]:
            traps += 1
            trap_correct += int(correct)
        results.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "predicted": decision.tier,
                "reason": decision.reason_code,
                "confidence": decision.confidence,
                "baseline": baseline,
                "correct": correct,
            }
        )

    report = {
        "classifier_version": classifier.classify("").classifier_version,
        "cases": len(cases),
        "classifier_accuracy": classifier_correct / len(cases),
        "baseline_accuracy": baseline_correct / len(cases),
        "trap_accuracy": trap_correct / traps,
        "results": results,
    }
    print(json.dumps(report, indent=2))
    if classifier_correct <= baseline_correct:
        raise SystemExit("routing classifier did not beat the length-only baseline")


if __name__ == "__main__":
    main()
