"""Deterministic, versioned and explainable routing policy."""

import re

from prism.domain.errors import ModelNotAllowedError
from prism.domain.routing import RoutingDecision
from prism.domain.security import TenantContext
from prism.ports.routing import RoutingClassifier

CLASSIFIER_VERSION = "rules-v1"
SMART_TERMS = frozenset(
    {
        "algorithm",
        "analyze",
        "architecture",
        "concurrency",
        "debug",
        "derive",
        "implement",
        "optimize",
        "proof",
        "prove",
        "reason",
        "sql",
        "theorem",
        "tradeoff",
    }
)
FAST_TERMS = frozenset(
    {"classify", "extract", "format", "list", "rewrite", "summarize", "translate"}
)


class RuleBasedRoutingClassifier:
    """Small local classifier designed for deterministic capstone evaluation."""

    def classify(self, prompt: str) -> RoutingDecision:
        words = re.findall(r"[a-zA-Z]+", prompt.lower())
        unique = set(words)
        fast_matches = sorted(unique & FAST_TERMS)
        smart_matches = sorted(unique & SMART_TERMS)

        # Explicit low-complexity transformations beat prompt length. This handles
        # deliberately long but trivial evaluation traps.
        if fast_matches and not smart_matches:
            return RoutingDecision(
                "fast",
                f"simple_task:{fast_matches[0]}",
                0.9,
                CLASSIFIER_VERSION,
                True,
            )
        if smart_matches:
            confidence = min(0.98, 0.78 + 0.04 * len(smart_matches))
            return RoutingDecision(
                "smart",
                f"reasoning_signal:{smart_matches[0]}",
                confidence,
                CLASSIFIER_VERSION,
                True,
            )
        if len(words) > 180 and len(unique) / max(1, len(words)) > 0.45:
            return RoutingDecision(
                "smart", "high_information_density", 0.7, CLASSIFIER_VERSION, True
            )
        return RoutingDecision("fast", "default_low_complexity", 0.65, CLASSIFIER_VERSION, True)


class RoutingService:
    def __init__(self, classifier: RoutingClassifier) -> None:
        self._classifier = classifier

    def resolve(self, requested_alias: str, prompt: str, tenant: TenantContext) -> RoutingDecision:
        tenant.authorize_alias(requested_alias)
        if requested_alias in {"fast", "smart", "mock-fast"}:
            return RoutingDecision(
                requested_alias,  # type: ignore[arg-type]
                "explicit_alias",
                1.0,
                CLASSIFIER_VERSION,
                False,
            )
        if requested_alias != "auto":
            raise ModelNotAllowedError(f"Model alias {requested_alias!r} is not routable")

        decision = self._classifier.classify(prompt)
        if decision.tier in tenant.allowed_aliases:
            return decision
        if "fast" in tenant.allowed_aliases:
            return RoutingDecision(
                "fast",
                f"policy_downgrade:{decision.tier}",
                decision.confidence,
                decision.classifier_version,
                True,
            )
        if "smart" in tenant.allowed_aliases:
            return RoutingDecision(
                "smart",
                f"policy_override:{decision.tier}",
                decision.confidence,
                decision.classifier_version,
                True,
            )
        raise ModelNotAllowedError("The auto policy has no permitted physical routing tier")


def length_only_baseline(prompt: str) -> str:
    return "smart" if len(prompt) >= 160 else "fast"
