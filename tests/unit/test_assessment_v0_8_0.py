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


# ---------------- F2: ref_mismatch fallback is actionable, never a loop ----------------

async def test_ref_mismatch_wrong_ref_falls_back_to_capabilities(
    mcp, stub_service: StubService
) -> None:
    # REF 'A' wrong in both builds; not a swap (ALT 'G' != ref base 'T').
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "T"}
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-A-G"}))
    assert data["error_code"] == "ref_mismatch"
    assert data["fallback_tool"] == "get_server_capabilities"
    assert data["fallback_args"] is None
    # the dead-end resolve_variant echo must be gone
    assert not (
        data["fallback_tool"] == "resolve_variant"
        and (data.get("fallback_args") or {}).get("variant") == "8-140300616-A-G"
    )


async def test_ref_mismatch_other_build_redirects_to_same_tool_other_build(
    mcp, stub_service: StubService
) -> None:
    # REF 'A' matches the GRCh37 base -> re-run predict on GRCh37.
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "A"}
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-A-G"}))
    assert data["error_code"] == "ref_mismatch"
    assert data["fallback_tool"] == "predict_spliceai"
    assert data["fallback_args"] == {"variant": "8-140300616-A-G", "genome_build": "GRCh37"}
    assert data["other_build_hint"]["build"] == "GRCh37"


async def test_ref_mismatch_swap_suggests_swapped_variant(mcp, stub_service: StubService) -> None:
    # ALT 'T' equals the reference base 'T' at this locus -> likely REF/ALT swap.
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "C"}
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-A-T"}))
    assert data["error_code"] == "ref_mismatch"
    assert data["fallback_tool"] == "predict_spliceai"
    assert data["fallback_args"] == {"variant": "8-140300616-T-A", "genome_build": "GRCh38"}
    assert "swap" in data["recovery"].lower()


# ---------------- F3: stable summary keys across modes ----------------

async def test_spliceai_top_present_in_all_modes(mcp) -> None:
    for mode in ("minimal", "compact", "full"):
        data = structured(
            await mcp.call_tool(
                "predict_spliceai", {"variant": "chr8-140300616-T-G", "response_mode": mode}
            )
        )
        assert data["top"] == {"class": "acceptor_loss", "score": 0.83, "position": -2}, mode
        assert data["max_delta_score"] == 0.83, mode


async def test_pangolin_top_present_in_all_modes(mcp) -> None:
    for mode in ("minimal", "compact", "full"):
        data = structured(
            await mcp.call_tool(
                "predict_pangolin", {"variant": "chr8-140300616-T-G", "response_mode": mode}
            )
        )
        assert data["top"]["class"] == "splice_loss", mode
        assert data["top"]["score"] == 0.85, mode
        assert data["max_delta_score"] == 0.85, mode


async def test_combined_maxes_in_agreement_all_modes(mcp) -> None:
    for mode in ("minimal", "compact", "full"):
        data = structured(
            await mcp.call_tool(
                "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": mode}
            )
        )
        ag = data["agreement"]
        assert ag["verdict"] == "concordant_high", mode
        assert ag["spliceai_max_delta"] == 0.83, mode
        assert ag["pangolin_max_delta"] == 0.85, mode
        # the divergent minimal-only names are gone
        assert "spliceai_max" not in data, mode
        assert "pangolin_max" not in data, mode


# ---------------- F6: threshold_basis only in full ----------------

async def test_threshold_basis_only_in_full_single_model(mcp) -> None:
    compact = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    assert "threshold_basis" not in compact["interpretation"]
    assert compact["interpretation"]["band"] == "high"
    full = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
        )
    )
    assert "threshold_basis" in full["interpretation"]


async def test_threshold_basis_only_in_full_combined(mcp) -> None:
    compact = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert "threshold_basis" not in compact["interpretation"]
    full = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
        )
    )
    assert "threshold_basis" in full["interpretation"]
