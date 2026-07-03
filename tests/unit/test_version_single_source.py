"""Guard: pyproject -> installed metadata -> __version__ -> serverInfo -> FastAPI host are one value."""

from __future__ import annotations

import asyncio
import logging
import tomllib
from importlib.metadata import version
from pathlib import Path

from fastapi.testclient import TestClient

from spliceailookup_link import __version__
from spliceailookup_link.config import ServerConfig
from spliceailookup_link.mcp.facade import create_spliceai_mcp
from spliceailookup_link.server_manager import UnifiedServerManager

DIST = "spliceailookup-link"


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def test_pyproject_is_the_single_source() -> None:
    assert version(DIST) == _pyproject_version()


def test_dunder_version_is_metadata_derived() -> None:
    assert __version__ == version(DIST)


def test_mcp_server_info_version_matches_package() -> None:
    mcp = create_spliceai_mcp(service_factory=lambda: object())  # type: ignore[arg-type]
    assert mcp.version == version(DIST)


def test_fastapi_host_and_health_version_match_package() -> None:
    manager = UnifiedServerManager()
    manager.logger = logging.getLogger("test")
    manager._current_transport = "streamable-http-stateless"
    app = asyncio.run(manager._create_fastapi_app(ServerConfig(transport="unified")))
    assert app.version == version(DIST)
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["version"] == version(DIST)
