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


# ---------------- P1#1: capabilities_version not duplicated ----------------

async def test_capabilities_version_not_duplicated_in_meta(mcp) -> None:
    data = structured(await mcp.call_tool("get_server_capabilities", {}))
    assert "capabilities_version" in data  # top-level (the document's own hash)
    assert "capabilities_version" not in data["_meta"], "must not duplicate in _meta"


async def test_prediction_still_carries_version_in_meta(mcp) -> None:
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    assert "capabilities_version" not in data  # no top-level on predictions
    assert "capabilities_version" in data["_meta"]  # provenance lives in _meta here


# ---------------- P1#2: proactive rate budget ----------------

async def test_success_carries_rate_budget(mcp) -> None:
    for tool in ("predict_spliceai", "predict_pangolin", "predict_splicing"):
        data = structured(await mcp.call_tool(tool, {"variant": "chr8-140300616-T-G"}))
        rb = data["_meta"]["rate_budget"]
        assert rb["limit"] == 2
        assert rb["unit"] == "concurrent_requests"
        assert rb["min_interval_ms"] == 12000
        assert "remaining" not in rb  # success: no fabricated remaining


async def test_rate_budget_present_on_minimal(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "chr8-140300616-T-G", "response_mode": "minimal"}
        )
    )
    assert data["_meta"]["rate_budget"]["min_interval_ms"] == 12000


async def test_rate_limited_error_carries_retry_after(mcp, stub_service: StubService) -> None:
    from spliceailookup_link.api import RateLimitedError

    stub_service.score_error = RateLimitedError("saturated")
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    assert data["error_code"] == "rate_limited"
    rb = data["_meta"]["rate_budget"]
    assert rb["limit"] == 2
    assert rb["remaining"] == 0
    assert rb["retry_after_s"] == 12


async def test_batch_envelope_carries_rate_budget(mcp) -> None:
    data = structured(
        await mcp.call_tool("predict_splicing_batch", {"variants": ["chr8-140300616-T-G"]})
    )
    assert data["_meta"]["rate_budget"]["min_interval_ms"] == 12000


# ---------------- F4: gtex see_also uses the gencode id ----------------

async def test_gtex_see_also_uses_gene_id_in_full(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
        )
    )
    gtex = next(h for h in data["_meta"]["see_also"] if h["server"] == "gtex-link")
    assert gtex["example"]["tool"] == "get_median_expression_levels"
    assert gtex["example"]["arguments"]["gencode_id"] == ["ENSG00000167632.19"]


async def test_gtex_see_also_uses_gene_id_combined_full(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
        )
    )
    gtex = next(h for h in data["_meta"]["see_also"] if h["server"] == "gtex-link")
    assert gtex["example"]["arguments"]["gencode_id"] == ["ENSG00000167632.19"]


# ---------------- F5a: symbol-less lncRNA headline ----------------

def test_gene_label_marks_ensembl_only_genes() -> None:
    from spliceailookup_link.mcp.shaping import _gene_label

    assert _gene_label("TRAPPC9") == "TRAPPC9"
    assert _gene_label("ENSG00000241860") == "ENSG00000241860 (no gene symbol)"
    assert _gene_label(None) == "unknown gene"


def test_spliceai_headline_uses_gene_label() -> None:
    from spliceailookup_link.mcp.shaping import spliceai_headline

    shaped = {
        "genome_build": "GRCh38",
        "variant_id": "1-100000-C-G",
        "transcripts": [
            {
                "gene": "ENSG00000241860",
                "delta_scores": {"acceptor_gain": {"score": 0.0, "position": 0}},
            }
        ],
    }
    assert "ENSG00000241860 (no gene symbol)" in spliceai_headline(shaped)
