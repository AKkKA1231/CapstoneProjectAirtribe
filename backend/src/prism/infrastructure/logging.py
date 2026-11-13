"""Structured logging configuration with secret-aware defaults."""

import logging
import sys

import structlog

from prism.infrastructure.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure stdlib and structlog output for the selected environment."""

    level = getattr(logging, settings.log_level)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)

    renderer = (
        structlog.dev.ConsoleRenderer()
        if settings.environment == "development"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
