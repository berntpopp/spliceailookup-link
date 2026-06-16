"""End-to-end tests for the v0.8.0 UX >9.5 changes."""

from __future__ import annotations

from tests.conftest import StubService, expect_tool_error, structured

_ECHO_KEYS = ("variant_id", "genome_build", "gene_set", "max_distance", "mask")


async def test_combined_subblocks_drop_request_echo(mcp) -> None:
    res = await mcp.call_tool("predict_splicing", {"variant_id": "chr8-140300616-T-G"})
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
        "predict_splicing", {"variant_id": "chr8-140300616-T-G", "response_mode": "full"}
    )
    data = structured(res)
    assert "headline" in data["spliceai"]
    assert "variant_id" not in data["spliceai"]


async def test_standalone_single_model_keeps_request_echo(mcp) -> None:
    res = await mcp.call_tool("predict_spliceai", {"variant_id": "chr8-140300616-T-G"})
    data = structured(res)
    assert data["variant_id"] == "8-140300616-T-G"
    assert data["genome_build"] == "GRCh38"
    assert data["max_distance"] == 500


async def test_include_see_also_false_keeps_next_commands(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing", {"variant_id": "chr8-140300616-T-G", "include_see_also": False}
    )
    data = structured(res)
    assert "headline" in data, "must be a success envelope, not validation_failed"
    meta = data["_meta"]
    assert "next_commands" in meta
    assert "see_also" not in meta


async def test_default_keeps_see_also(mcp) -> None:
    meta = structured(
        await mcp.call_tool("predict_splicing", {"variant_id": "chr8-140300616-T-G"})
    )["_meta"]
    assert "see_also" in meta and "next_commands" in meta


async def test_include_hints_false_drops_both(mcp) -> None:
    meta = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant_id": "chr8-140300616-T-G", "include_hints": False}
        )
    )["_meta"]
    assert "next_commands" not in meta
    assert "see_also" not in meta


async def test_spliceai_include_see_also_false(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant_id": "chr8-140300616-T-G", "include_see_also": False}
        )
    )
    assert "headline" in data, "must be a success envelope, not validation_failed"
    meta = data["_meta"]
    assert "next_commands" in meta
    assert "see_also" not in meta


async def test_resolve_wrong_ref_warns_but_still_returns_id(mcp, stub_service: StubService) -> None:
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "T"}
    data = structured(await mcp.call_tool("resolve_variant", {"variant_id": "chr8-140300616-A-G"}))
    assert data["variant_id"] == "8-140300616-A-G"  # still normalized
    assert data["ref_validated"] is False
    assert "does not match" in data["ref_warning"]


async def test_resolve_correct_ref_is_validated(mcp, stub_service: StubService) -> None:
    stub_service.ref_bases = {"GRCh38": "T"}
    data = structured(await mcp.call_tool("resolve_variant", {"variant_id": "chr8-140300616-T-G"}))
    assert data["ref_validated"] is True
    assert "ref_warning" not in data


async def test_resolve_check_ref_false_makes_no_ensembl_call(
    mcp, stub_service: StubService
) -> None:
    stub_service.ref_bases = {"GRCh38": "T"}
    data = structured(
        await mcp.call_tool(
            "resolve_variant", {"variant_id": "chr8-140300616-A-G", "check_ref": False}
        )
    )
    assert data["variant_id"] == "8-140300616-A-G", "must be a success envelope"
    assert "ref_validated" not in data
    assert "ref_warning" not in data
    assert stub_service.refbase_calls == []


async def test_not_found_fast_fails_on_zero_overlap(mcp, stub_service: StubService) -> None:
    stub_service.overlap_count = 0
    data = await expect_tool_error(mcp, "predict_spliceai", {"variant_id": "chr8-140300616-T-G"})
    assert data["error_code"] == "not_found"
    assert stub_service.score_calls == []  # never dispatched to scoring


async def test_overlap_present_proceeds_to_scoring(mcp, stub_service: StubService) -> None:
    stub_service.overlap_count = 1
    data = structured(await mcp.call_tool("predict_spliceai", {"variant_id": "chr8-140300616-T-G"}))
    assert "headline" in data
    assert len(stub_service.score_calls) == 1


async def test_overlap_inconclusive_proceeds(mcp, stub_service: StubService) -> None:
    stub_service.overlap_count = None  # Ensembl unavailable -> never a false fast-fail
    data = structured(await mcp.call_tool("predict_spliceai", {"variant_id": "chr8-140300616-T-G"}))
    assert "headline" in data
    assert len(stub_service.score_calls) == 1


def test_capabilities_document_new_fields() -> None:
    from spliceailookup_link.mcp.resources import get_capabilities_resource

    full = get_capabilities_resource(detail="full")
    blob = str(full).lower()
    assert "include_see_also" in blob
    assert "ref_validated" in blob or "ref_warning" in blob
