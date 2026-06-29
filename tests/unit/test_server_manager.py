"""Tests for the UnifiedServerManager (factories + FastAPI host), without uvicorn."""

from __future__ import annotations

import asyncio
import logging

from fastapi.testclient import TestClient

from spliceailookup_link.config import ServerConfig
from spliceailookup_link.server_manager import UnifiedServerManager
from spliceailookup_link.services import SpliceService


def test_create_service_returns_splice_service() -> None:
    manager = UnifiedServerManager()
    service = manager._create_service()
    assert isinstance(service, SpliceService)


def test_create_mcp_server_registers_tools() -> None:
    manager = UnifiedServerManager()
    manager.logger = logging.getLogger("test")
    stub = object()
    mcp = manager._create_mcp_server(lambda: stub)  # type: ignore[arg-type]

    async def _names() -> set[str]:
        return {t.name for t in await mcp.list_tools()}

    names = asyncio.run(_names())
    assert "predict_splicing" in names
    assert "get_server_capabilities" in names


def test_fastapi_host_health_endpoint() -> None:
    manager = UnifiedServerManager()
    manager.logger = logging.getLogger("test")
    manager._current_transport = "unified"
    config = ServerConfig(transport="unified")
    app = asyncio.run(manager._create_fastapi_app(config))
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["transport"] == "unified"


def _build_unified_app(manager: UnifiedServerManager, config: ServerConfig):
    """Replicate ``start_unified_server`` wiring without uvicorn.

    Mirrors the production mount: the MCP path is baked into the sub-app via
    ``http_app(path=config.mcp_path)`` and the sub-app is mounted at "/" so the
    endpoint is served at ``/mcp`` directly (no 307 redirect to ``/mcp/``).
    """
    app = asyncio.run(manager._create_fastapi_app(config))
    manager.app = app

    def service_factory() -> SpliceService:
        return manager.app.state.splice_service  # type: ignore[union-attr,return-value]

    manager.mcp = manager._create_mcp_server(service_factory)
    mcp_http_app = manager.mcp.http_app(
        path=config.mcp_path, stateless_http=True, json_response=True
    )
    manager._compose_lifespan(app, mcp_http_app)
    app.mount("/", mcp_http_app)
    return app


def test_post_mcp_is_not_redirect() -> None:
    """Regression: POST /mcp must reach the MCP app directly, not 307 to /mcp/."""
    manager = UnifiedServerManager()
    manager.logger = logging.getLogger("test")
    manager._current_transport = "unified"
    config = ServerConfig(transport="unified", mcp_path="/mcp")
    app = _build_unified_app(manager, config)

    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "s", "version": "1"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(app) as client:
        resp = client.post("/mcp", json=init, headers=headers, follow_redirects=False)
        assert resp.status_code != 307
        assert resp.status_code == 200
        # FastAPI's own routes still take precedence over the root mount.
        assert client.get("/health").status_code == 200
