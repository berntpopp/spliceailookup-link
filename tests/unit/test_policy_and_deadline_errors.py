"""#122 (URL-policy rejection is named + logged) and #123 (deadline message names the timeout)."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
import respx

from spliceailookup_link.api import ScoringClient
from spliceailookup_link.api.url_guard import DisallowedURLError, ResponseTooLargeError
from spliceailookup_link.config import settings
from spliceailookup_link.mcp.errors import McpErrorContext, mcp_tool_error, run_mcp_tool
from spliceailookup_link.mcp.tools._common import run_with_deadline
from tests.conftest import StubService, structured

_SAI38 = "https://spliceai-38-xwkwwwxdwq-uc.a.run.app/spliceai/"


async def _score_via_tool() -> dict:
    client = ScoringClient()

    async def call() -> dict:
        return await client.score(
            model="spliceai", build="GRCh38", variant="8-140300616-T-G", distance=50, mask=0
        )

    try:
        result = await run_mcp_tool("predict_splicing", call)
    finally:
        await client.close()
    assert result.is_error is True
    return result.structured_content


@respx.mock
async def test_off_host_redirect_is_named_policy_error_and_logs_host(caplog) -> None:
    route = respx.get(_SAI38).mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://evil.example:8443/x?token=secret"}
        )
    )
    with caplog.at_level(logging.WARNING):
        payload = await _score_via_tool()
    assert route.call_count == 1  # never retried
    assert payload["error_code"] == "upstream_unavailable"
    assert payload["error_subtype"] == "upstream_policy"
    assert payload["retryable"] is False
    assert "not allowlisted" in payload["message"]
    assert "evil.example" not in payload["message"]  # host only in the server log
    logged = caplog.text
    assert "evil.example" in logged and "8443" in logged
    assert "secret" not in logged and "/x" not in logged


@respx.mock
async def test_redirect_exhaustion_has_its_own_message() -> None:
    respx.get(url__startswith=_SAI38).mock(
        return_value=httpx.Response(302, headers={"Location": _SAI38 + "loop"})
    )
    payload = await _score_via_tool()
    assert payload["error_subtype"] == "upstream_policy"
    assert "redirect limit" in payload["message"]


def test_response_too_large_is_policy_not_internal() -> None:
    exc = ResponseTooLargeError("upstream response exceeds HTTP policy byte limit")
    p = mcp_tool_error(exc, McpErrorContext(tool_name="predict_splicing")).payload
    assert p["error_code"] == "upstream_unavailable"
    assert p["retryable"] is False
    assert "byte limit" in p["message"]


async def test_batch_does_not_retry_policy_error(mcp, stub_service: StubService) -> None:
    stub_service.score_error = DisallowedURLError("outbound request blocked by HTTP policy")
    res = await mcp.call_tool("predict_splicing_batch", {"variant_ids": ["8-140300616-T-G"]})
    data = structured(res)
    assert len(stub_service.score_calls) == 2  # one per model, no in-batch retry
    assert data["results"][0]["retryable"] is False


async def test_outer_deadline_message_names_the_timeout() -> None:
    async def call() -> dict:
        await asyncio.sleep(5)
        return {}

    result = await run_mcp_tool("predict_splicing", call, deadline=0.01)
    payload = result.structured_content
    assert payload["error_code"] == "upstream_unavailable"
    assert payload["message"] != "TimeoutError"
    assert "0.01s deadline" in payload["message"]


async def test_inner_deadline_wins_over_outer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PREDICT_SOFT_DEADLINE_SECONDS", 0.5)

    async def call() -> dict:
        await asyncio.sleep(0.05)  # prep work the outer timer also counts
        return await run_with_deadline(asyncio.sleep(5))

    result = await run_mcp_tool("predict_splicing", call, deadline=0.5)
    assert "Scoring exceeded" in result.structured_content["message"]
