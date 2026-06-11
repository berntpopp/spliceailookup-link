"""predict_splicing_batch fan-out."""

from __future__ import annotations

from spliceailookup_link.api import DataNotFoundError
from tests.conftest import StubService, structured


async def test_batch_scores_each_variant_once_envelope(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing_batch",
        {"variants": ["chr8-140300616-T-G", "8-140300616-T-G"]},
    )
    data = structured(res)
    assert data["success"] is True
    assert data["count"] == 2
    assert len(data["results"]) == 2
    assert "see_also" in data["_meta"]  # one block for the batch
    assert all("_meta" not in r for r in data["results"])  # per-item _meta suppressed


async def test_batch_partial_failure_does_not_fail_batch(mcp, stub_service: StubService) -> None:
    stub_service.score_error = DataNotFoundError("no overlap")
    res = await mcp.call_tool("predict_splicing_batch", {"variants": ["1-1-A-T"]})
    data = structured(res)
    assert data["success"] is True
    assert data["summary"]["failed"] == 1
    assert data["results"][0]["error_code"] == "not_found"


async def test_batch_over_cap_validation_failed(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing_batch", {"variants": [f"1-{i}-A-T" for i in range(26)]}
    )
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "validation_failed"
