"""End-to-end tests for the v0.8.0 UX >9.5 changes."""

from __future__ import annotations

from tests.conftest import StubService, structured

_ECHO_KEYS = ("variant_id", "genome_build", "gene_set", "max_distance", "mask")


async def test_combined_subblocks_drop_request_echo(mcp) -> None:
    res = await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"})
    data = structured(res)
    for key in _ECHO_KEYS:
        assert key in data, f"envelope must keep {key}"
    for sub in ("spliceai", "pangolin"):
        block = data[sub]
        for key in _ECHO_KEYS:
            assert key not in block, f"{sub}.{key} should be hoisted to the envelope"
        assert "max_delta_score" in block, "model-level max kept"
        assert "headline" not in block, "compact drops per-model headlines"
        assert "max_delta_score" not in block["transcripts"][0], (
            "single-transcript per-transcript max is redundant with model-level"
        )


async def test_combined_full_keeps_per_model_headline_but_not_echo(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
    )
    data = structured(res)
    assert "headline" in data["spliceai"]
    assert "variant_id" not in data["spliceai"]


async def test_standalone_single_model_keeps_request_echo(mcp) -> None:
    res = await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"})
    data = structured(res)
    assert data["variant_id"] == "8-140300616-T-G"
    assert data["genome_build"] == "GRCh38"
    assert data["max_distance"] == 500
