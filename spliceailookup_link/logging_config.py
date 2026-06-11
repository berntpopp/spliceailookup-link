"""Logging configuration for the spliceailookup-link server."""

from __future__ import annotations

import logging
import sys

from .config import settings


def configure_logging(transport: str, level: str | None = None) -> None:
    """Configure root logging for a given transport.

    stdio must keep stdout pristine (JSON-RPC framing), so its handler writes to
    stderr and library loggers are quieted.
    """
    if level is None:
        level = settings.STDIO_LOG_LEVEL if transport == "stdio" else settings.MCP_LOG_LEVEL

    for existing in logging.root.handlers[:]:
        logging.root.removeHandler(existing)

    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler: logging.Handler
    if transport == "stdio":
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.WARNING)
        for noisy in ("fastmcp", "uvicorn", "fastapi", "httpx", "httpcore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, level.upper()))

    handler.setFormatter(formatter)
    logging.root.setLevel(getattr(logging, level.upper()))
    logging.root.addHandler(handler)


def get_server_logger(transport: str) -> logging.Logger:
    """Return a logger tagged with the transport name."""

    logger = logging.getLogger("spliceailookup_link.server")

    class _Adapter(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            return f"[{transport}] {msg}", kwargs

    return _Adapter(logger, {})
