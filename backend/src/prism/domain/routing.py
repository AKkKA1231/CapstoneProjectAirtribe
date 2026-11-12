"""Explainable routing decisions."""

from dataclasses import dataclass
from typing import Literal

RoutingTier = Literal["fast", "smart", "mock-fast"]


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    tier: RoutingTier
    reason_code: str
    confidence: float
    classifier_version: str
    auto_routed: bool
