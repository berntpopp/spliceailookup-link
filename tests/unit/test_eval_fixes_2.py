"""Regression tests for docs/mcp-evaluation.md Part 4 (F6-F10 + #2/#4/#5)."""

from __future__ import annotations

from tests.conftest import structured


async def test_f6_headline_matches_verdict_concordant_high(mcp) -> None:
    # Stub returns SpliceAI 0.83 / Pangolin 0.85 -> concordant_high.
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert data["agreement"]["verdict"] == "concordant_high"
    assert "models agree" in data["headline"]
    assert "models disagree" not in data["headline"]
