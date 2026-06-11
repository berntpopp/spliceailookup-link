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
