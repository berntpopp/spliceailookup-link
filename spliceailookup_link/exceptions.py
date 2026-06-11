"""Custom exceptions for the spliceailookup-link server (transport layer)."""

from __future__ import annotations


class SpliceServerError(Exception):
    """Base exception for server-level errors."""

    def __init__(self, message: str, transport: str | None = None):
        super().__init__(message)
        self.transport = transport


class ConfigurationError(SpliceServerError):
    """Configuration validation error."""


class StartupError(SpliceServerError):
    """Server startup error."""


class MCPIntegrationError(SpliceServerError):
    """MCP integration error."""
