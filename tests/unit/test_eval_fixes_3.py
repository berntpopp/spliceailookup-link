"""Regression tests for docs/mcp-evaluation.md Part 7 (F11-F17 + #C1) and the
§8 durability invariants."""

from __future__ import annotations

import json

from spliceailookup_link.mcp.shaping import THRESHOLD_BASIS
from tests.conftest import structured


async def test_f13_threshold_basis_emitted_once_in_combined(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    # Exactly one threshold_basis in the whole combined payload (top-level only).
    assert json.dumps(data).count("threshold_basis") == 1
    assert data["interpretation"]["threshold_basis"] == THRESHOLD_BASIS
    # Each model sub-block keeps its decision-relevant band but drops the static string.
    assert "band" in data["spliceai"]["interpretation"]
    assert "threshold_basis" not in data["spliceai"]["interpretation"]
    assert "band" in data["pangolin"]["interpretation"]
    assert "threshold_basis" not in data["pangolin"]["interpretation"]


async def test_f13_single_model_still_has_one_threshold_basis(mcp) -> None:
    # Standalone single-model tools are self-contained: they keep their one copy.
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    assert data["interpretation"]["threshold_basis"] == THRESHOLD_BASIS
    assert json.dumps(data).count("threshold_basis") == 1
