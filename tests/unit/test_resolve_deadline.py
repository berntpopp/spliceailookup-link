"""Resolution must return an actionable error before an MCP client times out."""

from __future__ import annotations

import asyncio

from spliceailookup_link.config import settings
from spliceailookup_link.mcp.facade import create_spliceai_mcp
from tests.conftest import StubService, expect_tool_error


class SlowResolveService(StubService):
    async def resolve(self, text: str, build: str) -> dict:
        await asyncio.sleep(0.05)
        return await super().resolve(text, build)


async def test_resolve_timeout_is_an_actionable_upstream_error(monkeypatch) -> None:
    """An unavailable resolver must not consume the behavior probe's client timeout."""
    monkeypatch.setattr(settings, "RESOLVE_SOFT_DEADLINE_SECONDS", 0.01, raising=False)
    mcp = create_spliceai_mcp(service_factory=SlowResolveService)

    error = await expect_tool_error(mcp, "resolve_variant", {"variant_id": "rs6025"})

    assert error["error_code"] == "upstream_unavailable"
    assert error["retryable"] is True
