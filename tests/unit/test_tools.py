"""End-to-end tool tests through FastMCP call_tool with a stubbed service."""

from __future__ import annotations

from spliceailookup_link.api import DataNotFoundError
from tests.conftest import StubService, structured


async def test_capabilities_lists_tools(mcp) -> None:
    res = await mcp.call_tool("get_server_capabilities", {})
    data = structured(res)
    assert data["server"] == "spliceailookup-link"
    assert "predict_splicing" in data["tools"]
    assert data["research_use_only"] is True


async def test_resolve_coordinate_no_upstream(mcp, stub_service: StubService) -> None:
    res = await mcp.call_tool("resolve_variant", {"variant": "chr8-140300616-T-G"})
    data = structured(res)
    assert data["variant_id"] == "8-140300616-T-G"
    assert data["input_kind"] == "coordinate"
    assert data["_meta"]["next_commands"][0]["tool"] == "predict_splicing"


async def test_resolve_hgvs(mcp) -> None:
    res = await mcp.call_tool("resolve_variant", {"variant": "NM_001089.3(ABCA3):c.875A>T"})
    data = structured(res)
    assert data["variant_id"] == "16-2317763-T-A"
    assert data["gene_symbol"] == "ABCA3"
    assert data["consequence"] == "missense_variant"


async def test_predict_spliceai_success(mcp, stub_service: StubService) -> None:
    res = await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"})
    data = structured(res)
    assert data["success"] is True
    assert data["max_delta_score"] == 0.83
    assert "TRAPPC9" in data["headline"]
    assert data["_meta"]["next_commands"][0]["tool"] == "predict_pangolin"
    assert {h["server"] for h in data["_meta"]["see_also"]} >= {"gnomad-link"}
    # The scoring call forwarded the right model + normalized variant.
    assert stub_service.score_calls[0]["model"] == "spliceai"
    assert stub_service.score_calls[0]["variant_id"] == "8-140300616-T-G"


async def test_predict_spliceai_auto_resolves_hgvs(mcp, stub_service: StubService) -> None:
    res = await mcp.call_tool("predict_spliceai", {"variant": "NM_001089.3(ABCA3):c.875A>T"})
    data = structured(res)
    assert data["success"] is True
    # HGVS was resolved before scoring.
    assert stub_service.resolve_calls
    assert stub_service.score_calls[0]["variant_id"] == "16-2317763-T-A"


async def test_predict_pangolin_success(mcp) -> None:
    res = await mcp.call_tool("predict_pangolin", {"variant": "8-140300616-T-G"})
    data = structured(res)
    assert data["model"] == "Pangolin"
    assert data["max_delta_score"] == 0.85


async def test_predict_splicing_runs_both_models(mcp, stub_service: StubService) -> None:
    res = await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"})
    data = structured(res)
    assert data["success"] is True
    assert "spliceai" in data and "pangolin" in data
    assert data["agreement"]["verdict"] == "concordant_high"
    assert data["consequence"]["aberrations"][0]["type"] == "exon_skipping"
    assert "models agree" in data["headline"]
    models = {c["model"] for c in stub_service.score_calls}
    assert models == {"spliceai", "pangolin"}


async def test_predict_splicing_partial_when_pangolin_fails(mcp, stub_service: StubService) -> None:
    stub_service.pangolin_error = DataNotFoundError("pangolin glitch")
    res = await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"})
    data = structured(res)
    # SpliceAI still succeeds; the failure is surfaced under _meta.partial.
    assert data["success"] is True
    assert "spliceai" in data
    assert "pangolin" not in data
    assert any("pangolin_failed" in p for p in data["_meta"]["partial"])


async def test_predict_splicing_both_fail_returns_error(mcp, stub_service: StubService) -> None:
    stub_service.score_error = DataNotFoundError("no overlapping transcript")
    res = await mcp.call_tool("predict_splicing", {"variant": "1-1-A-T"})
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "not_found"


async def test_predict_build_mismatch_short_circuits(mcp, stub_service: StubService) -> None:
    res = await mcp.call_tool(
        "predict_spliceai", {"variant": "8-145500000-A-T", "genome_build": "GRCh38"}
    )
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "build_mismatch"
    # No scoring call was made.
    assert stub_service.score_calls == []


async def test_warmup_pings_both_models(mcp, stub_service: StubService) -> None:
    data = structured(await mcp.call_tool("warmup", {"genome_build": "GRCh38"}))
    assert data["success"] is True
    assert data["warmed"] is True
    assert {"spliceai", "pangolin"} <= set(data["detail"])


async def test_invalid_variant_returns_invalid_input(mcp, stub_service: StubService) -> None:
    from spliceailookup_link.variant import VariantParseError

    stub_service.resolve_error = VariantParseError("bad")
    # predict_splicing with an unparseable coordinate-ish string -> parse error
    res = await mcp.call_tool("predict_spliceai", {"variant": "totally invalid"})
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "invalid_input"
