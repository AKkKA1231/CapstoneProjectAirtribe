"""Phase 4 routing policy and committed evaluation checks."""

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from prism.application.routing import (
    RoutingService,
    RuleBasedRoutingClassifier,
    length_only_baseline,
)
from prism.domain.routing import RoutingDecision
from prism.domain.security import TenantContext


def tenant(*aliases: str) -> TenantContext:
    return TenantContext(uuid4(), "Routing tenant", 60, Decimal("10"), frozenset(aliases))


def test_explicit_aliases_are_deterministic_and_not_auto_routed() -> None:
    service = RoutingService(RuleBasedRoutingClassifier())

    for alias in ("fast", "smart", "mock-fast"):
        decision = service.resolve(alias, "Prove a theorem", tenant(alias))
        assert decision == RoutingDecision(alias, "explicit_alias", 1.0, "rules-v1", False)


def test_auto_detects_short_reasoning_and_long_trivial_work() -> None:
    service = RoutingService(RuleBasedRoutingClassifier())
    permitted = tenant("auto", "fast", "smart")

    hard = service.resolve("auto", "Prove that square root of two is irrational", permitted)
    easy = service.resolve(
        "auto",
        "Summarize this routine paragraph: " + "ordinary words " * 100,
        permitted,
    )

    assert hard.tier == "smart"
    assert hard.reason_code == "reasoning_signal:prove"
    assert easy.tier == "fast"
    assert easy.reason_code == "simple_task:summarize"
    assert service.resolve("auto", "Prove that square root of two is irrational", permitted) == hard


def test_auto_respects_tenant_physical_tier_policy() -> None:
    decision = RoutingService(RuleBasedRoutingClassifier()).resolve(
        "auto", "Analyze this concurrency architecture", tenant("auto", "fast")
    )

    assert decision.tier == "fast"
    assert decision.reason_code == "policy_downgrade:smart"
    assert decision.auto_routed is True


def test_committed_routing_evaluation_beats_length_baseline() -> None:
    cases_path = Path(__file__).parents[3] / "config" / "routing_eval_cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    classifier = RuleBasedRoutingClassifier()

    predictions = [classifier.classify(case["prompt"]).tier for case in cases]
    baseline = [length_only_baseline(case["prompt"]) for case in cases]
    expected = [case["expected"] for case in cases]
    accuracy = sum(left == right for left, right in zip(predictions, expected, strict=True))
    baseline_accuracy = sum(left == right for left, right in zip(baseline, expected, strict=True))
    traps = [
        prediction == case["expected"]
        for prediction, case in zip(predictions, cases, strict=True)
        if case["trap"]
    ]

    assert accuracy / len(cases) == pytest.approx(1.0)
    assert baseline_accuracy / len(cases) == pytest.approx(0.4)
    assert all(traps)
    assert accuracy > baseline_accuracy
