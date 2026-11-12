"""Routing-classifier port."""

from typing import Protocol

from prism.domain.routing import RoutingDecision


class RoutingClassifier(Protocol):
    def classify(self, prompt: str) -> RoutingDecision: ...
