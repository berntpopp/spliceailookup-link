"""End-to-end tests for the v0.9.0 assessment fixes (F1-F6 + Part 1)."""

from __future__ import annotations

from spliceailookup_link.mcp.build_check import out_of_range
from tests.conftest import StubService, structured


# ---------------- F1: out-of-range coordinate ----------------

def test_out_of_range_helper_detects_beyond_both_builds() -> None:
    assert out_of_range("chr1", 260_000_000) == (248_956_422, 249_250_621)
    assert out_of_range("1", 260_000_000) == (248_956_422, 249_250_621)
    # in-range in at least one build -> not out of range (build_mismatch territory)
    assert out_of_range("1", 249_000_000) is None
    # ordinary in-range -> None
    assert out_of_range("8", 140_300_616) is None
    # MT / non-standard -> None (handled elsewhere)
    assert out_of_range("chrM", 8993) is None
    assert out_of_range("chr99", 100) is None


async def test_out_of_range_returns_invalid_input_without_scoring(
    mcp, stub_service: StubService
) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr1-260000000-A-G"}))
    assert data["success"] is False
    assert data["error_code"] == "invalid_input"
    assert "248,956,422" in data["message"] and "249,250,621" in data["message"]
    assert data["fallback_tool"] == "get_server_capabilities"
    # ZERO upstream / Ensembl traffic: arithmetic-only rejection
    assert stub_service.score_calls == []
    assert stub_service.refbase_calls == []
    assert stub_service.overlap_calls == []
