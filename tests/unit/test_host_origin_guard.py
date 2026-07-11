"""Host/Origin boundary contracts for spliceailookup-link HTTP applications."""

from __future__ import annotations

import asyncio
import inspect
import logging
from importlib.metadata import version
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from packaging.version import Version

from spliceailookup_link.config import ServerConfig, Settings, settings
from spliceailookup_link.server_manager import UnifiedServerManager

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        settings,
        "ALLOWED_HOSTS",
        ["localhost", "127.0.0.1", "::1", "spliceailookup-link.genefoundry.org"],
    )
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", ["https://genefoundry.org"])
    manager = UnifiedServerManager()
    manager.logger = logging.getLogger("test-host-origin")
    application = asyncio.run(manager._create_unified_app(ServerConfig()))
    return TestClient(application, raise_server_exceptions=False)


def test_fastmcp_344_guard_api_is_installed() -> None:
    assert Version(version("fastmcp")) >= Version("3.4.4")
    parameters = inspect.signature(FastMCP.http_app).parameters
    assert "host_origin_protection" in parameters
    assert "allowed_hosts" in parameters
    assert "allowed_origins" in parameters


@pytest.mark.parametrize(
    "host",
    ["spliceailookup-link.genefoundry.org", "spliceailookup-link.genefoundry.org:443"],
)
def test_configured_public_host_is_allowed(client: TestClient, host: str) -> None:
    assert client.get("/mcp", headers={"Host": host}).status_code not in {403, 421}


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_loopback_hosts_are_allowed(client: TestClient, host: str) -> None:
    assert client.get("/mcp", headers={"Host": host}).status_code not in {403, 421}


@pytest.mark.parametrize("path", ["/", "/health", "/docs", "/mcp"])
def test_untrusted_host_is_rejected_on_every_route(client: TestClient, path: str) -> None:
    assert client.get(path, headers={"Host": "evil.example"}).status_code == 421


def test_absent_and_configured_origins_are_allowed(client: TestClient) -> None:
    no_origin = client.get("/mcp", headers={"Host": "spliceailookup-link.genefoundry.org"})
    configured = client.get(
        "/mcp",
        headers={
            "Host": "spliceailookup-link.genefoundry.org",
            "Origin": "https://genefoundry.org",
        },
    )
    assert no_origin.status_code not in {403, 421}
    assert configured.status_code not in {403, 421}


@pytest.mark.parametrize("path", ["/", "/health", "/docs", "/mcp"])
def test_untrusted_origin_is_rejected_on_every_route(client: TestClient, path: str) -> None:
    response = client.get(
        path,
        headers={
            "Host": "spliceailookup-link.genefoundry.org",
            "Origin": "https://evil.example",
        },
    )
    assert response.status_code == 403


def test_untrusted_preflight_is_rejected_by_outer_guard(client: TestClient) -> None:
    response = client.options(
        "/health",
        headers={
            "Host": "evil.example",
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 421


@pytest.mark.parametrize("wildcard", ["*", "*.example.org", "host?.example.org", "host[0]"])
def test_wildcard_host_is_rejected(wildcard: str) -> None:
    with pytest.raises(ValueError, match="wildcard"):
        Settings(_env_file=None, ALLOWED_HOSTS=[wildcard])


@pytest.mark.parametrize("wildcard", ["*", "https://*.example.org"])
def test_wildcard_origin_is_rejected(wildcard: str) -> None:
    with pytest.raises(ValueError, match="wildcard"):
        Settings(_env_file=None, ALLOWED_ORIGINS=[wildcard])


def test_json_environment_allowlists_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SPLICEAILOOKUP_LINK_ALLOWED_HOSTS",
        '["localhost","spliceailookup-link.genefoundry.org"]',
    )
    monkeypatch.setenv(
        "SPLICEAILOOKUP_LINK_ALLOWED_ORIGINS",
        '["https://genefoundry.org"]',
    )

    configured = Settings(_env_file=None)

    assert configured.ALLOWED_HOSTS == ["localhost", "spliceailookup-link.genefoundry.org"]
    assert configured.ALLOWED_ORIGINS == ["https://genefoundry.org"]


def test_image_default_uses_guarded_unified_server_and_explicit_health_host() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["spliceailookup-link", "serve", "--transport", "unified"' in dockerfile
    assert "-H 'Host: localhost'" in dockerfile
