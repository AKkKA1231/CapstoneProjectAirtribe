"""Small dependency-free Prometheus metrics registry for one gateway process."""

from collections import defaultdict
from time import perf_counter

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = structlog.get_logger(__name__)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MetricsRegistry:
    def __init__(self) -> None:
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)

    def increment(self, name: str, **labels: str) -> None:
        self._counters[(name, tuple(sorted(labels.items())))] += 1

    def observe(self, name: str, value: float, **labels: str) -> None:
        self._counters[(f"{name}_count", tuple(sorted(labels.items())))] += 1
        self._counters[(f"{name}_sum", tuple(sorted(labels.items())))] += value

    def render(self) -> str:
        lines = []
        for (name, labels), value in sorted(self._counters.items()):
            suffix = ""
            if labels:
                suffix = "{" + ",".join(f'{key}="{_escape(item)}"' for key, item in labels) + "}"
            lines.append(f"prism_{name}{suffix} {value}")
        return "\n".join(lines) + "\n"


class RequestMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = perf_counter()
        status = 500
        response: Response | None = None
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            elapsed = perf_counter() - started
            registry: MetricsRegistry = request.app.state.metrics
            registry.increment(
                "http_requests_total",
                method=request.method,
                path=request.url.path,
                status=str(status),
            )
            registry.observe(
                "http_request_duration_seconds",
                elapsed,
                method=request.method,
                path=request.url.path,
            )
            if response is not None:
                cache = response.headers.get("x-prism-cache")
                if cache in {"hit", "miss"}:
                    registry.increment("cache_requests_total", result=cache)
                if response.headers.get("x-prism-fallback") == "true":
                    registry.increment("fallbacks_total")
            logger.info(
                "request_metric",
                method=request.method,
                path=request.url.path,
                status=status,
                latency_ms=round(elapsed * 1000, 3),
            )
