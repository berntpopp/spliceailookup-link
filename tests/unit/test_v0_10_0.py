"""v0.10.0: close the v0.9.0 assessment to >9.5/10."""

from __future__ import annotations

from spliceailookup_link.mcp.facade import create_spliceai_mcp
from spliceailookup_link.mcp.resources import get_capabilities_resource
from tests.conftest import StubService, structured

_TOOLS = {
    "get_server_capabilities",
    "resolve_variant",
    "predict_spliceai",
    "predict_pangolin",
    "predict_splicing",
    "predict_splicing_batch",
    "warmup",
}


# ---------------- W1: tool annotations locked in + advertised ----------------


async def test_all_tools_serialize_readonly_annotations() -> None:
    mcp = create_spliceai_mcp(service_factory=lambda: StubService())
    tools = await mcp.list_tools()
    seen = {t.name for t in tools}
    assert _TOOLS <= seen, f"missing tools: {_TOOLS - seen}"
    for t in tools:
        ann = t.annotations
        assert ann is not None, f"{t.name} has no annotations"
        assert ann.readOnlyHint is True, f"{t.name} not readOnlyHint"
        assert ann.idempotentHint is True, f"{t.name} not idempotentHint"
        assert ann.openWorldHint is True, f"{t.name} not openWorldHint"
        assert ann.destructiveHint is False, f"{t.name} destructiveHint should be False"


def test_capabilities_advertises_tool_safety() -> None:
    doc = get_capabilities_resource()
    ts = doc["tool_safety"]
    assert ts["all_tools_read_only"] is True
    assert ts["idempotent"] is True
    assert ts["open_world"] is True
    assert "auto" in ts["note"].lower()


# ---------------- W5: basic gene_set scope clarified ----------------


def test_basic_gene_set_documents_noncoding() -> None:
    doc = get_capabilities_resource()
    text = doc["parameters"]["gene_set"].lower()
    assert "non-coding" in text or "noncoding" in text or "lncrna" in text
    assert "gencode" in text


# ---------------- W3: model/build provenance ----------------


def test_provenance_has_versioned_sources() -> None:
    from spliceailookup_link.mcp.provenance import prediction_provenance

    p = prediction_provenance("GRCh38")
    assert "SpliceAI" in p["spliceai"]
    assert "Pangolin" in p["pangolin"]
    assert "v44" in p["transcript_annotation"]
    assert "Ensembl" in p["resolver"]
    assert "documented" in p["note"].lower()
    p37 = prediction_provenance("GRCh37")
    assert "lift37" in p37["transcript_annotation"]


async def test_predict_splicing_carries_provenance(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert "v44" in data["provenance"]["transcript_annotation"]


async def test_minimal_omits_provenance(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "minimal"}
        )
    )
    assert "provenance" not in data


async def test_predict_spliceai_carries_provenance(mcp) -> None:
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    assert "v44" in data["provenance"]["transcript_annotation"]


async def test_predict_pangolin_carries_provenance(mcp) -> None:
    data = structured(await mcp.call_tool("predict_pangolin", {"variant": "chr8-140300616-T-G"}))
    assert "v44" in data["provenance"]["transcript_annotation"]


async def test_batch_envelope_carries_provenance(mcp) -> None:
    data = structured(
        await mcp.call_tool("predict_splicing_batch", {"variants": ["chr8-140300616-T-G"]})
    )
    assert "v44" in data["_meta"]["provenance"]["transcript_annotation"]


def test_capabilities_data_sources_versioned() -> None:
    ds = get_capabilities_resource()["data_sources"]
    assert "v44" in ds["transcript_annotation"]


# ---------------- W8: client-supplied correlation_id ----------------


async def test_correlation_id_echoed_on_success(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "correlation_id": "trace-123"}
        )
    )
    assert data["_meta"]["correlation_id"] == "trace-123"


async def test_correlation_id_echoed_on_error(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-zzz-T-G", "correlation_id": "trace-err"}
        )
    )
    assert data["error_code"] == "invalid_input"
    assert data["_meta"]["correlation_id"] == "trace-err"


async def test_no_correlation_id_means_no_field(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert "correlation_id" not in data["_meta"]


async def test_correlation_id_on_resolve_and_batch(mcp) -> None:
    res = structured(
        await mcp.call_tool("resolve_variant", {"variant": "chr8-140300616-T-G", "correlation_id": "c1"})
    )
    assert res["_meta"]["correlation_id"] == "c1"
    batch = structured(
        await mcp.call_tool(
            "predict_splicing_batch",
            {"variants": ["chr8-140300616-T-G"], "correlation_id": "c2"},
        )
    )
    assert batch["_meta"]["correlation_id"] == "c2"


# ---------------- W7: warmup both masks + stay-warm estimate ----------------


async def test_warmup_default_reports_stay_warm_estimate(mcp) -> None:
    data = structured(await mcp.call_tool("warmup", {}))
    assert data["stay_warm_estimate_s"] >= 1
    assert data["coverage"]["mask"] == "raw"


async def test_warmup_both_masks(mcp) -> None:
    data = structured(await mcp.call_tool("warmup", {"mask": "both"}))
    assert data["coverage"]["mask"] == "both"
    assert "spliceai_raw" in data["detail"] and "spliceai_masked" in data["detail"]
    assert "pangolin_raw" in data["detail"] and "pangolin_masked" in data["detail"]


# ---------------- W2: predict_splicing_batch dedup by resolved variant_id ----


async def test_batch_dedups_coordinate_and_hgvs(stub_service) -> None:
    from spliceailookup_link.mcp.tools._batch_runner import run_batch

    # The ABCA3 HGVS resolves (in StubService) to 16-2317763-T-A.
    variants = ["16-2317763-T-A", "NM_001089.3(ABCA3):c.875A>T"]
    out = await run_batch(
        stub_service,
        variants=variants,
        genome_build="GRCh38",
        params={
            "max_distance": 500,
            "mask": "raw",
            "gene_set": "basic",
            "transcripts": "mane",
            "response_mode": "compact",
            "cross_build_check": True,
            "enforce_deadline": True,
        },
        max_items=25,
    )
    assert out["count"] == 2
    assert len(out["results"]) == 2
    # 2 models x 1 unique variant = 2 upstream score calls (not 4).
    assert len(stub_service.score_calls) == 2
    assert out["summary"]["unique_variants"] == 1
    assert out["summary"]["upstream_calls_saved"] == 2
    assert out["_meta"]["deduped"] == {"unique": 1, "duplicates": 1}
    # both positions carry a full per-item result; the copy is marked deduped.
    for item in out["results"]:
        assert "agreement" in item or "headline" in item
    duped = [r for r in out["results"] if (r.get("_meta") or {}).get("cache") == "deduped"]
    assert len(duped) == 1
    assert duped[0]["_meta"]["served_from"] == "16-2317763-T-A"


async def test_batch_distinct_variants_not_deduped(stub_service) -> None:
    from spliceailookup_link.mcp.tools._batch_runner import run_batch

    variants = ["chr8-140300616-T-G", "16-2317763-T-A"]
    out = await run_batch(
        stub_service,
        variants=variants,
        genome_build="GRCh38",
        params={
            "max_distance": 500,
            "mask": "raw",
            "gene_set": "basic",
            "transcripts": "mane",
            "response_mode": "compact",
            "cross_build_check": True,
            "enforce_deadline": True,
        },
        max_items=25,
    )
    # 2 distinct variants x 2 models = 4 upstream calls, nothing saved.
    assert len(stub_service.score_calls) == 4
    assert out["summary"]["upstream_calls_saved"] == 0
    assert out["summary"]["unique_variants"] == 2


# ---------------- W4: nearest-transcript distance on not_found ----------------


async def test_not_found_includes_nearest_transcript(mcp, stub_service) -> None:
    stub_service.overlap_count = 0  # force the not_found fast-fail
    stub_service.nearest = {"distance_nt": 4200, "gene": "FOO", "transcript_id": "ENST123"}
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "1-50000-A-G"}))
    assert data["error_code"] == "not_found"
    nt = data["nearest_transcript"]
    assert nt["distance_nt"] == 4200 and nt["gene"] == "FOO"
    assert "4,200" in data["recovery"] or "4200" in data["recovery"]


async def test_not_found_far_transcript_advises_intergenic(mcp, stub_service) -> None:
    stub_service.overlap_count = 0
    stub_service.nearest = {"distance_nt": 55000, "gene": "BAR", "transcript_id": "ENST9"}
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "1-50000-A-G"}))
    assert data["error_code"] == "not_found"
    assert data["nearest_transcript"]["distance_nt"] == 55000
    assert "intergenic" in data["recovery"].lower()


async def test_not_found_without_nearest_is_unchanged(mcp, stub_service) -> None:
    stub_service.overlap_count = 0
    stub_service.nearest = None  # Ensembl could not determine a nearest transcript
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "1-50000-A-G"}))
    assert data["error_code"] == "not_found"
    assert "nearest_transcript" not in data
