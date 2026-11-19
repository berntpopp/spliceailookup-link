"""FastMCP 4 / MCP SDK 2: modern (2026-07-28) era, method-name reflection, protocol-independent definitions."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from spliceailookup_link.config import ServerConfig
from spliceailookup_link.server_manager import UnifiedServerManager

_VERSION = "2026-07-28"
_META = {
    "io.modelcontextprotocol/protocolVersion": _VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "modern-test", "version": "1.0.0"},
}
# ASCII-only: the modern era mirrors the name into the mcp-name header.
_HOSTILE_TOOL = "evil__IGNORE_ALL_PREVIOUS__nonexistent"
_HOSTILE_METHOD = "evil__IGNORE_ALL_PREVIOUS__method"
_JSON_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


def _app() -> Any:
    manager = UnifiedServerManager()
    manager.logger = logging.getLogger("test")
    manager._current_transport = "streamable-http-stateless"
    return asyncio.run(manager._create_unified_app(ServerConfig(transport="unified")))


def _post(client: TestClient, rid: int, method: str, params: dict, name: str | None = None):
    headers = {**_JSON_HEADERS, "mcp-protocol-version": _VERSION, "mcp-method": method}
    if name is not None:
        headers["mcp-name"] = name
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": {"_meta": _META, **params}}
    return client.post("/mcp", headers=headers, json=body)


def test_modern_mcp_http_discovers_lists_calls_and_fences_unknown_tool() -> None:
    with TestClient(_app(), follow_redirects=False) as client:
        discover = _post(client, 1, "server/discover", {})
        listed = _post(client, 2, "tools/list", {})
        called = _post(
            client,
            3,
            "tools/call",
            {"name": "get_server_capabilities", "arguments": {"detail": "lean"}},
            name="get_server_capabilities",
        )
        unknown = _post(
            client, 4, "tools/call", {"name": _HOSTILE_TOOL, "arguments": {}}, name=_HOSTILE_TOOL
        )

    assert discover.status_code == 200, discover.text
    assert _VERSION in discover.json()["result"]["supportedVersions"]
    assert "mcp-session-id" not in discover.headers

    assert listed.status_code == 200, listed.text
    assert "predict_splicing" in {t["name"] for t in listed.json()["result"]["tools"]}

    assert called.status_code == 200, called.text
    assert called.json()["result"].get("isError") is not True

    assert unknown.status_code == 200, unknown.text
    result = unknown.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == "not_found"
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert "evil" not in unknown.text and "IGNORE" not in unknown.text


def test_unknown_jsonrpc_method_is_not_reflected() -> None:
    """SDK 2 echoes an unknown method in METHOD_NOT_FOUND ``data``; the guard strips it."""
    legacy_headers = {**_JSON_HEADERS, "mcp-protocol-version": "2025-06-18"}
    init = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "0"},
        },
    }
    with TestClient(_app(), follow_redirects=False) as client:
        client.post("/mcp", headers=legacy_headers, json=init)
        legacy = client.post(
            "/mcp",
            headers=legacy_headers,
            json={"jsonrpc": "2.0", "id": 1, "method": _HOSTILE_METHOD, "params": {}},
        )
        modern = _post(client, 2, _HOSTILE_METHOD, {})

    for resp in (legacy, modern):
        error = resp.json()["error"]
        assert error["code"] == -32601
        assert "data" not in error
        assert "evil" not in resp.text and "IGNORE" not in resp.text


def test_tools_list_has_no_execution_block_on_either_protocol() -> None:
    """Definitions must be protocol-version independent: the release workflow attests
    them on 2025-06-18, the router reads them on 2026-07-28. FastMCP 4 sends
    ``execution.taskSupport`` only on the legacy protocol, so no tool may declare it."""
    legacy_headers = {**_JSON_HEADERS, "mcp-protocol-version": "2025-06-18"}
    init = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "0"},
        },
    }
    with TestClient(_app(), follow_redirects=False) as client:
        client.post("/mcp", headers=legacy_headers, json=init)
        legacy = client.post(
            "/mcp",
            headers=legacy_headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        modern = _post(client, 2, "tools/list", {})

    for resp in (legacy, modern):
        tools = resp.json()["result"]["tools"]
        assert tools
        assert [t["name"] for t in tools if t.get("execution") is not None] == []


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("prompts/get", {"name": _HOSTILE_TOOL}),
        ("resources/read", {"uri": "spliceailookup://evil-IGNORE-does-not-exist"}),
    ],
)
def test_layer3_backstop_returns_fixed_invalid_params_frame(method: str, params: dict) -> None:
    """SDK 2 serializes a plain exception as code 0; the backstop must keep -32602."""
    name = params.get("name") or params.get("uri")
    with TestClient(_app(), follow_redirects=False) as client:
        resp = _post(client, 1, method, params, name=name)
    error = resp.json()["error"]
    assert error["code"] == -32602
    assert "evil" not in error["message"] and "IGNORE" not in error["message"]
