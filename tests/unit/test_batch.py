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
    assert "see_also" not in data["_meta"]  # batch-level see_also is misleading for a panel
    assert data["_meta"]["next_commands"][0]["tool"] == "predict_splicing"
    # F12: each success item now carries a slim per-item _meta (cache visibility).
    assert all(r["_meta"]["cache"] in ("hit", "miss") for r in data["results"])


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


async def test_f10_batch_summary_full_histogram(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing_batch",
        {"variants": ["chr8-140300616-T-G", "8-140300616-T-G"]},
    )
    data = structured(res)
    summary = data["summary"]
    for key in (
        "ok",
        "failed",
        "concordant_high",
        "concordant_moderate",
        "concordant_low",
        "discordant",
        "incomplete",
    ):
        assert key in summary
    verdict_total = (
        summary["concordant_high"]
        + summary["concordant_moderate"]
        + summary["concordant_low"]
        + summary["discordant"]
        + summary["incomplete"]
    )
    assert verdict_total == summary["ok"]
    assert data["summary_top_variant"]["variant"]


async def test_f10_batch_next_commands_targets_top_variant(mcp) -> None:
    res = await mcp.call_tool("predict_splicing_batch", {"variants": ["chr8-140300616-T-G"]})
    data = structured(res)
    nc = data["_meta"]["next_commands"][0]
    assert nc["tool"] == "predict_splicing"
    assert nc["arguments"]["response_mode"] == "full"


async def test_batch_flags_ambiguous_rsid_not_silently_scored(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_splicing_batch",
            {"variants": ["chr8-140300616-T-G", "rs6025"]},
        )
    )
    by_variant = {r["variant"]: r for r in data["results"]}
    amb = by_variant["rs6025"]
    assert amb["error_code"] == "ambiguous"
    assert amb["variant_ids"] == ["1-169549811-C-A", "1-169549811-C-T"]
    assert "error_code" not in by_variant["chr8-140300616-T-G"]
    assert data["summary"]["ok"] == 1
    assert data["summary"]["failed"] == 1
