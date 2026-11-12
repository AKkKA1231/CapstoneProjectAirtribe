"""Admin-protected Prometheus text exposition."""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from prism.api.dependencies import AdminDependency, MetricsDependency

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(_: AdminDependency, registry: MetricsDependency) -> PlainTextResponse:
    return PlainTextResponse(registry.render(), media_type="text/plain; version=0.0.4")
