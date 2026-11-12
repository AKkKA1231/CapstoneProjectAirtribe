"""Liveness and readiness endpoints."""

from fastapi import APIRouter, Response, status

from prism.api.dependencies import DatabaseDependency, SettingsDependency
from prism.api.schemas.health import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def liveness(settings: SettingsDependency) -> HealthResponse:
    """Report whether the process can serve HTTP requests."""

    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        checks={"process": "ok"},
    )


@router.get("/ready", response_model=HealthResponse)
async def readiness(
    response: Response,
    settings: SettingsDependency,
    database: DatabaseDependency,
) -> HealthResponse:
    """Report whether required dependencies are available."""

    checks: dict[str, str] = {}
    ready = True

    if not settings.database_enabled:
        checks["database"] = "disabled"
    elif database is None:
        checks["database"] = "not_initialized"
        ready = False
    else:
        try:
            await database.ping()
            checks["database"] = "ok"
        except Exception:  # Dependency errors must not expose credentials or internals.
            checks["database"] = "unavailable"
            ready = False

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if ready else "unavailable",
        service=settings.app_name,
        version=settings.app_version,
        checks=checks,
    )
