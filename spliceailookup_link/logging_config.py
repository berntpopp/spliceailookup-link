"""Structured logging configuration for spliceailookup-link.

GeneFoundry Logging & CLI Standard v1: ``structlog`` on the canonical processor
chain (``merge_contextvars → add_log_level → TimeStamper(iso) →
StackInfoRenderer → format_exc_info → static fields``) rendered as JSON in
production or via ``ConsoleRenderer`` in development (selected by ``LOG_FORMAT``,
default ``json``). The ``asgi-correlation-id`` request id is bound onto every
log event through ``merge_contextvars``.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

import structlog
from asgi_correlation_id import CorrelationIdMiddleware
from asgi_correlation_id.context import correlation_id

from . import __version__
from .config import settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from structlog.typing import FilteringBoundLogger

__all__ = ["bind_correlation_id_middleware", "configure_logging"]


def _add_static_fields(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Attach ``service`` and ``version`` to every log event."""
    event_dict.setdefault("service", "spliceailookup-link")
    event_dict.setdefault("version", __version__)
    return event_dict


def _add_correlation_id(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Attach the asgi-correlation-id request id (when one is in scope)."""
    request_id = correlation_id.get()
    if request_id is not None:
        event_dict.setdefault("request_id", request_id)
    return event_dict


def _configure_stdlib_logging(level: str) -> None:
    """Route stdlib logging to stderr and tame noisy third-party loggers."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level))
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(getattr(logging, level))
    root_logger.addHandler(handler)

    is_debug = level == "DEBUG"
    for name, lvl in {
        "httpx": "WARNING",
        "httpcore": "WARNING",
        "uvicorn.access": "INFO" if is_debug else "WARNING",
        "uvicorn.error": "INFO",
        "fastmcp": "INFO" if is_debug else "WARNING",
        "mcp": "INFO" if is_debug else "WARNING",
    }.items():
        logging.getLogger(name).setLevel(getattr(logging, lvl))


def _configure_structlog(level: str) -> None:
    """Configure structlog with a JSON (prod) or console (dev) renderer."""
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _add_correlation_id,
        _add_static_fields,
    ]

    if settings.LOG_FORMAT == "json":
        processors = [*shared_processors, structlog.processors.JSONRenderer()]
    else:
        colors = level == "DEBUG"
        processors = [*shared_processors, structlog.dev.ConsoleRenderer(colors=colors)]

    structlog.configure(
        processors=processors,  # type: ignore[arg-type]
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def configure_logging(level: str | None = None) -> FilteringBoundLogger:
    """Configure stdlib + structlog and return the package logger."""
    resolved = (level or settings.LOG_LEVEL).upper()
    _configure_stdlib_logging(resolved)
    _configure_structlog(resolved)
    return structlog.get_logger("spliceailookup_link")  # type: ignore[no-any-return]


def bind_correlation_id_middleware(app: FastAPI) -> None:
    """Install the asgi-correlation-id middleware on the FastAPI host.

    Each request gets a ``X-Request-ID`` correlation id that ``merge_contextvars``
    surfaces on every structlog event emitted while handling that request.
    """
    app.add_middleware(CorrelationIdMiddleware)
