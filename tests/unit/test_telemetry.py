"""Runtime observability in _meta."""

from __future__ import annotations

from tests.conftest import structured


async def test_spliceai_meta_reports_cache_miss_then_hit(mcp) -> None:
    first = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    second = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    assert first["_meta"]["cache"] == "miss"
    assert "upstream_elapsed_ms" in first["_meta"]
    assert second["_meta"]["cache"] == "hit"


async def test_combined_meta_cache_present(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert data["_meta"]["cache"] in {"hit", "miss", "partial"}
