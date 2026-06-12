"""Regression tests for the v0.7.0 assessment defects (D1-D5, C3-C5)."""

from __future__ import annotations

from tests.conftest import StubService, structured


# --- D1 + D2: pre-flight reference-base check ---------------------------------

async def test_preflight_ref_mismatch_skips_scoring(mcp, stub_service: StubService) -> None:
    # D2: a wrong REF is rejected as ref_mismatch BEFORE any scoring call.
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "C"}
    res = await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-A-G"})
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "ref_mismatch"
    assert stub_service.score_calls == []  # never dispatched to the scoring backend


async def test_preflight_ref_typo_matching_other_build_is_ref_mismatch(
    mcp, stub_service: StubService
) -> None:
    # D1: the exact assessment case chr8-140300616-C-A. REF matches GRCh37 base,
    # but it is reported as ref_mismatch (with a secondary hint), NOT build_mismatch.
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "C"}
    res = await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-C-A"})
    data = structured(res)
    assert data["error_code"] == "ref_mismatch"
    assert data["other_build_hint"]["build"] == "GRCh37"
    assert stub_service.score_calls == []


async def test_preflight_proceeds_when_ref_matches(mcp, stub_service: StubService) -> None:
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "T"}  # REF 'T' matches
    res = await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"})
    data = structured(res)
    assert data["success"] is True
    assert stub_service.score_calls  # scoring proceeded


async def test_preflight_proceeds_when_ensembl_unavailable(
    mcp, stub_service: StubService
) -> None:
    stub_service.ref_bases = {}  # reference_base returns None -> inconclusive
    res = await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-A-G"})
    data = structured(res)
    assert data["success"] is True  # never regress; scoring proceeds
    assert stub_service.score_calls


# --- D3: ambiguous resolve consistency ---------------------------------------

async def test_resolve_ambiguous_nulls_singular_id(mcp) -> None:
    res = await mcp.call_tool("resolve_variant", {"variant": "rs6025"})
    data = structured(res)
    assert data["ambiguous"] is True
    assert data["variant_id"] is None  # cannot silently pick one allele
    assert data["variant_ids"] == ["1-169549811-C-A", "1-169549811-C-T"]
    # The per-allele next_commands still guide the choice.
    tools = [c["tool"] for c in data["_meta"]["next_commands"]]
    assert tools and all(t == "predict_splicing" for t in tools)


# --- D4 + C4: lean _meta and served_warm -------------------------------------

async def test_meta_full_provenance_in_compact(mcp) -> None:
    res = await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"})
    meta = structured(res)["_meta"]
    assert "capabilities_version" in meta
    assert meta["unsafe_for_clinical_use"] is True
    assert "served_warm" in meta


async def test_meta_trimmed_when_hints_off(mcp) -> None:
    res = await mcp.call_tool(
        "predict_spliceai", {"variant": "8-140300616-T-G", "include_hints": False}
    )
    meta = structured(res)["_meta"]
    # Bulky/redundant provenance dropped on the lean path...
    assert "capabilities_version" not in meta
    assert "cache_ttl_s" not in meta
    assert "cache_age_s" not in meta
    assert "next_commands" not in meta
    # ...but request_id, timing, cache, served_warm, and the safety flag stay.
    assert "request_id" in meta
    assert "elapsed_ms" in meta["timing"]
    assert "cache" in meta
    assert "served_warm" in meta
    assert meta["unsafe_for_clinical_use"] is True


async def test_meta_trimmed_in_minimal_mode(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing", {"variant": "8-140300616-T-G", "response_mode": "minimal"}
    )
    meta = structured(res)["_meta"]
    assert "capabilities_version" not in meta
    assert "served_warm" in meta


async def test_served_warm_true_on_cache_hit(mcp, stub_service: StubService) -> None:
    await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"})  # warms cache
    res = await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"})
    assert structured(res)["_meta"]["served_warm"] is True
