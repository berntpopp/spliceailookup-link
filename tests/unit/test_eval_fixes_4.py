"""Regression tests for docs/mcp-consumer-assessment-2026-06-12.md (F18-F24)."""

from __future__ import annotations

from spliceailookup_link.api import RateLimitedError, SpliceApiError
from tests.conftest import StubService, structured


# --- F19: unsupported contig fast-fails before any scoring call ---
async def test_f19_mt_fast_fails_unsupported_contig_no_scoring(
    mcp, stub_service: StubService
) -> None:
    res = await mcp.call_tool("predict_splicing", {"variant": "MT-3243-A-G"})
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "unsupported_contig"
    assert data["retryable"] is False
    # The whole point: no upstream scoring slot was ever consumed.
    assert stub_service.score_calls == []
    # Recovery points the caller at the right cross-server tool.
    assert "gnomad-link" in data["recovery"]


async def test_f19_mt_in_batch_is_per_item_unsupported_contig(
    mcp, stub_service: StubService
) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_splicing_batch",
            {"variants": ["chr8-140300616-T-G", "MT-3243-A-G"]},
        )
    )
    by_variant = {r["variant"]: r for r in data["results"]}
    assert by_variant["MT-3243-A-G"]["error_code"] == "unsupported_contig"
    assert "error_code" not in by_variant["chr8-140300616-T-G"]
    # MT consumed no scoring slot; only the valid item scored (spliceai + pangolin).
    assert all(c["variant_id"] != "MT-3243-A-G" for c in stub_service.score_calls)
