"""Regression tests for docs/mcp-evaluation.md Part 4 (F6-F10 + #2/#4/#5)."""

from __future__ import annotations

import json

from tests.conftest import structured


async def test_f6_headline_matches_verdict_concordant_high(mcp) -> None:
    # Stub returns SpliceAI 0.83 / Pangolin 0.85 -> concordant_high.
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert data["agreement"]["verdict"] == "concordant_high"
    assert "models agree" in data["headline"]
    assert "models disagree" not in data["headline"]


async def test_interpretation_band_on_combined(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert data["interpretation"]["band"] == "high"
    assert "threshold_basis" in data["interpretation"]


async def test_cache_ttl_and_age_in_meta(mcp) -> None:
    first = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    assert first["_meta"]["cache"] == "miss"
    assert first["_meta"]["cache_ttl_s"] == 86400
    assert "cache_age_s" not in first["_meta"]
    second = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    assert second["_meta"]["cache"] == "hit"
    assert second["_meta"]["cache_age_s"] == 0
    assert second["_meta"]["cache_ttl_s"] == 86400


async def test_f8_combined_minimal_is_headline_tier(mcp) -> None:
    full = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "compact"}
        )
    )
    minimal = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "minimal"}
        )
    )
    assert len(json.dumps(minimal)) < len(json.dumps(full))
    assert "spliceai" not in minimal and "pangolin" not in minimal
    assert minimal["agreement"]["verdict"] == "concordant_high"
    assert minimal["spliceai_max"] == 0.83
    assert minimal["pangolin_max"] == 0.85
    assert minimal["interpretation"]["band"] == "high"
    assert "TRAPPC9" in minimal["headline"]
