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


class SlowPreflightService(StubService):
    async def reference_base(self, chrom: str, pos: int, length: int, build: str):
        await asyncio.sleep(0.05)
        return await super().reference_base(chrom, pos, length, build)


async def test_resolve_timeout_is_an_actionable_upstream_error(monkeypatch) -> None:
    """An unavailable resolver must not consume the behavior probe's client timeout."""
    monkeypatch.setattr(settings, "RESOLVE_SOFT_DEADLINE_SECONDS", 0.01, raising=False)
    mcp = create_spliceai_mcp(service_factory=SlowResolveService)

    error = await expect_tool_error(mcp, "resolve_variant", {"variant_id": "rs6025"})

    assert error["error_code"] == "upstream_unavailable"
    assert error["retryable"] is True


async def test_prediction_deadline_covers_variant_preflight(monkeypatch) -> None:
    """A slow Ensembl preflight must not run outside the foreground tool budget."""
    monkeypatch.setattr(settings, "PREDICT_SOFT_DEADLINE_SECONDS", 0.01)
    mcp = create_spliceai_mcp(service_factory=SlowPreflightService)

    error = await expect_tool_error(mcp, "predict_spliceai", {"variant_id": "1-100-A-T"})

    assert error["error_code"] == "upstream_unavailable"
    assert error["retryable"] is True


async def test_batch_deadline_covers_variant_preflight(monkeypatch) -> None:
    """The foreground batch entry point shares the same totality guarantee."""
    monkeypatch.setattr(settings, "PREDICT_SOFT_DEADLINE_SECONDS", 0.01)
    mcp = create_spliceai_mcp(service_factory=SlowPreflightService)

    error = await expect_tool_error(mcp, "predict_splicing_batch", {"variant_ids": ["1-100-A-T"]})

    assert error["error_code"] == "upstream_unavailable"
    assert error["retryable"] is True
