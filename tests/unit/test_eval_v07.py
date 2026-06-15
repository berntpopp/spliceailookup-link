"""Regression tests for the v0.7.0 assessment defects (D1-D5, C3-C5)."""

from __future__ import annotations

from spliceailookup_link.api import RateLimitedError, SpliceApiError
from spliceailookup_link.mcp.shaping import shape_spliceai
from tests.conftest import StubService, structured
from tests.fixtures.api_responses import SPLICEAI_MASKED_EMPTY_ABERR, SPLICEAI_TRAPPC9

# --- D1 + D2: pre-flight reference-base check ---------------------------------


async def test_preflight_ref_mismatch_skips_scoring(mcp, stub_service: StubService) -> None:
    # D2: a wrong REF is rejected as ref_mismatch BEFORE any scoring call.
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "C"}
    res = await mcp.call_tool("predict_spliceai", {"variant_id": "8-140300616-A-G"})
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
    res = await mcp.call_tool("predict_spliceai", {"variant_id": "8-140300616-C-A"})
    data = structured(res)
    assert data["error_code"] == "ref_mismatch"
    assert data["other_build_hint"]["build"] == "GRCh37"
    assert stub_service.score_calls == []


async def test_preflight_proceeds_when_ref_matches(mcp, stub_service: StubService) -> None:
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "T"}  # REF 'T' matches
    res = await mcp.call_tool("predict_spliceai", {"variant_id": "8-140300616-T-G"})
    data = structured(res)
    assert data["success"] is True
    assert stub_service.score_calls  # scoring proceeded


async def test_preflight_proceeds_when_ensembl_unavailable(mcp, stub_service: StubService) -> None:
    stub_service.ref_bases = {}  # reference_base returns None -> inconclusive
    res = await mcp.call_tool("predict_spliceai", {"variant_id": "8-140300616-A-G"})
    data = structured(res)
    assert data["success"] is True  # never regress; scoring proceeds
    assert stub_service.score_calls


# --- D3: ambiguous resolve consistency ---------------------------------------


async def test_resolve_ambiguous_nulls_singular_id(mcp) -> None:
    res = await mcp.call_tool("resolve_variant", {"variant_id": "rs6025"})
    data = structured(res)
    assert data["ambiguous"] is True
    assert data["variant_id"] is None  # cannot silently pick one allele
    assert data["variant_ids"] == ["1-169549811-C-A", "1-169549811-C-T"]
    # The per-allele next_commands still guide the choice.
    tools = [c["tool"] for c in data["_meta"]["next_commands"]]
    assert tools and all(t == "predict_splicing" for t in tools)


# --- D4 + C4: lean _meta and served_warm -------------------------------------


async def test_meta_full_provenance_in_compact(mcp) -> None:
    res = await mcp.call_tool("predict_spliceai", {"variant_id": "8-140300616-T-G"})
    meta = structured(res)["_meta"]
    assert "capabilities_version" in meta
    assert meta["unsafe_for_clinical_use"] is True
    assert "served_warm" in meta


async def test_meta_trimmed_when_hints_off(mcp) -> None:
    res = await mcp.call_tool(
        "predict_spliceai", {"variant_id": "8-140300616-T-G", "include_hints": False}
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
        "predict_splicing", {"variant_id": "8-140300616-T-G", "response_mode": "minimal"}
    )
    meta = structured(res)["_meta"]
    assert "capabilities_version" not in meta
    assert "served_warm" in meta


async def test_served_warm_true_on_cache_hit(mcp, stub_service: StubService) -> None:
    await mcp.call_tool("predict_spliceai", {"variant_id": "8-140300616-T-G"})  # warms cache
    res = await mcp.call_tool("predict_spliceai", {"variant_id": "8-140300616-T-G"})
    assert structured(res)["_meta"]["served_warm"] is True


# --- D5: tx_start / tx_end ----------------------------------------------------


def test_exon_model_carries_tx_bounds() -> None:
    shaped = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="full")
    exon = shaped["transcripts"][0]["exon_model"]
    assert exon["tx_start"] == 139727725  # min(EXON_STARTS)
    assert exon["tx_end"] == 140300614  # max(EXON_ENDS)


def test_transcript_info_tx_bounds_filled_when_null() -> None:
    # SAI-10k transcript_info carries strand/exon_count but null tx bounds; fill
    # them from the exon arrays in the scored transcript.
    shaped = shape_spliceai(SPLICEAI_MASKED_EMPTY_ABERR, response_mode="full")
    ti = shaped["consequence"]["transcript_info"]
    assert ti["tx_start"] == 139727725
    assert ti["tx_end"] == 140300614
    assert ti["strand"] == "-"  # upstream fields preserved


# --- C3: batch size contract --------------------------------------------------


async def test_batch_envelope_self_describes_size_contract(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing_batch", {"variant_ids": ["8-140300616-T-G", "8-140300616-T-G"]}
    )
    meta = structured(res)["_meta"]
    assert meta["items_submitted"] == 2
    assert meta["max_items"] == 25


async def test_batch_rejects_over_cap(mcp) -> None:
    res = await mcp.call_tool("predict_splicing_batch", {"variant_ids": ["8-140300616-T-G"] * 26})
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "validation_failed"


async def test_batch_item_meta_has_served_warm(mcp) -> None:
    res = await mcp.call_tool("predict_splicing_batch", {"variant_ids": ["8-140300616-T-G"]})
    item = structured(res)["results"][0]
    assert "served_warm" in item["_meta"]


# --- C5: resources in lean capabilities --------------------------------------


async def test_lean_capabilities_lists_resources(mcp) -> None:
    res = await mcp.call_tool("get_server_capabilities", {"detail": "lean"})
    data = structured(res)
    assert "resources" in data
    assert "spliceailookup://reference" in data["resources"]


# --- Rec #5: error-mapping coverage (deterministic, no live calls) -----------


async def test_comprehensive_503_maps_to_upstream_unavailable(
    mcp, stub_service: StubService
) -> None:
    # A 5xx during a comprehensive gene_set call surfaces as retryable upstream_unavailable.
    stub_service.score_error = SpliceApiError("Upstream HTTP 503")
    res = await mcp.call_tool(
        "predict_splicing",
        {"variant_id": "8-140300616-T-G", "gene_set": "comprehensive"},
    )
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "upstream_unavailable"
    assert data["retryable"] is True


async def test_rate_limited_reports_rate_budget(mcp, stub_service: StubService) -> None:
    stub_service.score_error = RateLimitedError("Local concurrency limit saturated")
    res = await mcp.call_tool("predict_splicing", {"variant_id": "8-140300616-T-G"})
    data = structured(res)
    assert data["error_code"] == "rate_limited"
    budget = data["_meta"]["rate_budget"]
    assert budget["unit"] == "concurrent_requests"
    assert budget["remaining"] == 0
    assert "limit" in budget


async def test_batch_per_item_rate_budget(mcp, stub_service: StubService) -> None:
    stub_service.score_error = RateLimitedError("saturated")
    res = await mcp.call_tool("predict_splicing_batch", {"variant_ids": ["8-140300616-T-G"]})
    item = structured(res)["results"][0]
    assert item["error_code"] == "rate_limited"
    assert item["rate_budget"]["unit"] == "concurrent_requests"


# --- Documentation contract ---------------------------------------------------


def test_capabilities_documents_served_warm_and_batch_cap() -> None:
    from spliceailookup_link.mcp.resources import get_capabilities_resource

    doc = get_capabilities_resource("full")
    assert "served_warm" in doc["response_fields"]["observability"]
    assert "max_items" in doc["batch_semantics"] or "25" in doc["batch_semantics"]
