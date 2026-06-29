"""Unified server manager for spliceailookup-link (Streamable HTTP only).

Runs a thin FastAPI host that exposes ``/health`` and mounts the MCP HTTP app at
``/mcp``. There is no stdio transport — the fleet standard is Streamable HTTP.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, cast

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import FastMCP

from spliceailookup_link.config import ServerConfig, settings
from spliceailookup_link.exceptions import ConfigurationError, MCPIntegrationError, StartupError
from spliceailookup_link.logging_config import bind_correlation_id_middleware, configure_logging
from spliceailookup_link.mcp.facade import create_spliceai_mcp
from spliceailookup_link.services import SpliceService


class UnifiedServerManager:
    def __init__(self) -> None:
        self.app: FastAPI | None = None
        self.mcp: FastMCP | None = None
        self.shutdown_event = asyncio.Event()
        self.logger: Any = None
        self._current_transport = "unknown"

    # ---------------- service factory ----------------

    def _create_service(self) -> SpliceService:
        return SpliceService(
            cache_size=settings.CACHE_SIZE,
            cache_ttl_minutes=settings.CACHE_TTL_MINUTES,
        )

    # ---------------- FastAPI host (health only) ----------------

    async def _create_fastapi_app(self, config: ServerConfig) -> FastAPI:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            self.logger.info("Starting spliceailookup-link host application...")
            service = self._create_service()
            app.state.splice_service = service
            self.logger.info("Service ready")
            try:
                yield
            finally:
                self.logger.info("Shutting down host application...")
                try:
                    await service.close()
                except Exception:
                    self.logger.debug("service close raised during shutdown", exc_info=True)

        app = FastAPI(
            title="spliceailookup-link MCP Host",
            description="Thin FastAPI host exposing /health and mounting the MCP HTTP app at /mcp.",
            version="2.0.0",
            lifespan=lifespan,
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        bind_correlation_id_middleware(app)
        cors_origins = settings.cors_origins_list
        # Never pair wildcard origins with credentials: browsers reject that
        # combination and it is a CORS anti-pattern (reflected-origin credential
        # exposure). Allow credentials only when an explicit allowlist is set.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=cors_origins != ["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "healthy", "transport": self._current_transport}

        return app

    # ---------------- MCP creation ----------------

    def _create_mcp_server(self, service_factory: Callable[[], SpliceService]) -> FastMCP:
        try:
            mcp = create_spliceai_mcp(service_factory=service_factory)
            self.logger.info("MCP facade created")
            return mcp
        except Exception as e:
            raise MCPIntegrationError(f"Failed to create MCP server: {e}", "mcp") from e

    @staticmethod
    def _compose_lifespan(app: FastAPI, mcp_app: Any) -> None:
        fastapi_lifespan = app.router.lifespan_context
        mcp_lifespan = mcp_app.lifespan

        @asynccontextmanager
        async def combined(parent_app: FastAPI):
            async with fastapi_lifespan(parent_app):
                async with mcp_lifespan(mcp_app):
                    yield

        app.router.lifespan_context = combined

    def _setup_signal_handlers(self) -> None:
        def handler(signum, _frame) -> None:
            self.logger.info(f"Received signal {signum}; shutting down...")
            self.shutdown_event.set()

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)

    # ---------------- entry points ----------------

    async def start_unified_server(self, config: ServerConfig) -> None:
        try:
            self._current_transport = "unified"
            self.logger = configure_logging(config.log_level)

            self.app = await self._create_fastapi_app(config)

            def service_factory() -> SpliceService:
                if self.app is None:
                    raise RuntimeError("FastAPI host not initialized")
                return cast(SpliceService, self.app.state.splice_service)

            self.mcp = self._create_mcp_server(service_factory)
            # Bake the MCP path ("/mcp") into the StarletteWithLifespan routes
            # returned by http_app(path=...), then mount that sub-app at the
            # project root. This serves the MCP endpoint at "/mcp" directly
            # rather than "/mcp/", avoiding a 307 redirect on POST /mcp and
            # matching the rest of the fleet (canonical gtex-link pattern).
            # FastAPI's own routes (/health, /api/...) are registered before
            # this mount, so they continue to take precedence.
            mcp_http_app = self.mcp.http_app(
                path=config.mcp_path, stateless_http=True, json_response=True
            )
            self._compose_lifespan(self.app, mcp_http_app)
            self.app.mount("/", mcp_http_app)

            self.logger.info(f"MCP HTTP at http://{config.host}:{config.port}{config.mcp_path}")
            self.logger.info(f"Health at http://{config.host}:{config.port}/health")

            self._setup_signal_handlers()

            uvicorn_config = uvicorn.Config(
                app=self.app,
                host=config.host,
                port=config.port,
                log_level=config.log_level.lower(),
                access_log=True,
            )
            await uvicorn.Server(uvicorn_config).serve()
        except Exception as e:
            raise StartupError(f"Failed to start unified server: {e}", "unified") from e

    async def start_server(self, config: ServerConfig) -> None:
        if config.transport in {"unified", "http"}:
            await self.start_unified_server(config)
        else:
            raise ConfigurationError(f"Unknown transport: {config.transport}")
