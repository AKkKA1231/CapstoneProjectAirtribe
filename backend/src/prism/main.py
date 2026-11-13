"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from prism.api.errors import prism_error_handler
from prism.api.routes.chat import router as chat_router
from prism.api.routes.health import router as health_router
from prism.api.routes.metrics import router as metrics_router
from prism.api.routes.models import router as models_router
from prism.api.routes.operations import router as operations_router
from prism.domain.errors import PrismError
from prism.infrastructure.config import Settings, get_settings
from prism.infrastructure.lifecycle import build_lifespan
from prism.infrastructure.logging import configure_logging
from prism.infrastructure.metrics import MetricsRegistry, RequestMetricsMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create a configured Prism application."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)

    application = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        docs_url="/docs" if resolved_settings.expose_api_docs else None,
        redoc_url="/redoc" if resolved_settings.expose_api_docs else None,
        openapi_url="/openapi.json" if resolved_settings.expose_api_docs else None,
        lifespan=build_lifespan(resolved_settings),
    )
    application.state.settings = resolved_settings
    application.state.metrics = MetricsRegistry()
    application.add_middleware(RequestMetricsMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    application.add_exception_handler(PrismError, prism_error_handler)  # type: ignore[arg-type]
    application.include_router(health_router)
    application.include_router(models_router)
    application.include_router(chat_router)
    application.include_router(operations_router)
    application.include_router(metrics_router)
    return application


app = create_app()
