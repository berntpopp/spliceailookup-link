"""Regression tests for the findings in docs/mcp-evaluation.md (F1-F5)."""

from __future__ import annotations

import re

from tests.conftest import StubService, structured

_COORD = re.compile(r"^[\dXYM]+-\d+-[ACGT]+-[ACGT]+$")


async def test_f1_multiallelic_rsid_chains_cleanly(mcp) -> None:
    res = await mcp.call_tool("resolve_variant", {"variant": "rs6025"})
    data = structured(res)
    assert _COORD.match(data["variant_id"])
    assert data["ambiguous"] is True
    cmds = data["_meta"]["next_commands"]
    assert len(cmds) == 2
    for c in cmds:
        assert _COORD.match(c["arguments"]["variant"])


async def test_meta_has_request_id_and_timing(mcp) -> None:
    res = await mcp.call_tool("get_server_capabilities", {})
    meta = structured(res)["_meta"]
    assert isinstance(meta["request_id"], str) and len(meta["request_id"]) == 12
    assert isinstance(meta["timing"]["elapsed_ms"], int)


async def test_error_envelope_has_request_id(mcp, stub_service: StubService) -> None:
    from spliceailookup_link.variant import VariantParseError

    stub_service.resolve_error = VariantParseError("bad")
    res = await mcp.call_tool("predict_spliceai", {"variant": "totally invalid"})
    data = structured(res)
    assert data["success"] is False
    assert "request_id" in data["_meta"]


async def test_f3_predict_splicing_has_next_commands(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    cmds = data["_meta"]["next_commands"]
    assert cmds and cmds[0]["tool"] in {"predict_spliceai", "predict_pangolin"}
    assert cmds[0]["arguments"]["response_mode"] == "full"


async def test_f4_no_duplicate_consequence_or_identity(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert "consequence" in data  # top-level only
    assert "consequence" not in data["spliceai"]
    assert data["transcript"]["gene"] == "TRAPPC9"  # single lifted identity block
    # identity is lifted OUT of the per-model transcript rows
    assert "refseq_ids" not in data["spliceai"]["transcripts"][0]
    assert "gene_id" not in data["pangolin"]["transcripts"][0]


async def test_see_also_omitted_in_minimal(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "8-140300616-T-G", "response_mode": "minimal"}
        )
    )
    assert "see_also" not in data["_meta"]


async def test_see_also_collapsed_in_compact(mcp) -> None:
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    for hint in data["_meta"]["see_also"]:
        assert "example" not in hint and set(hint) == {"server", "hint"}


async def test_see_also_full_keeps_example(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "8-140300616-T-G", "response_mode": "full"}
        )
    )
    assert any("example" in h for h in data["_meta"]["see_also"])


async def test_f5_cross_build_probe_upgrades_to_build_mismatch(
    mcp, stub_service: StubService
) -> None:
    stub_service.only_build = "GRCh38"  # scores in 38, not in 37
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "8-140300616-T-G", "genome_build": "GRCh37"}
        )
    )
    assert data["success"] is False
    assert data["error_code"] == "build_mismatch"
    assert data["fallback_args"]["genome_build"] == "GRCh38"


async def test_f5_probe_can_be_disabled(mcp, stub_service: StubService) -> None:
    stub_service.only_build = "GRCh38"
    data = structured(
        await mcp.call_tool(
            "predict_spliceai",
            {"variant": "8-140300616-T-G", "genome_build": "GRCh37", "cross_build_check": False},
        )
    )
    assert data["error_code"] == "not_found"


async def test_capabilities_version_is_stable(mcp) -> None:
    a = structured(await mcp.call_tool("get_server_capabilities", {}))
    b = structured(await mcp.call_tool("get_server_capabilities", {}))
    assert a["capabilities_version"] == b["capabilities_version"]
    assert len(a["capabilities_version"]) == 12
    assert isinstance(a["descriptor_chars"], int) and a["descriptor_chars"] > 0


async def test_minimal_strictly_smaller_than_compact(mcp) -> None:
    import json

    c = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "8-140300616-T-G", "response_mode": "compact"}
        )
    )
    m = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "8-140300616-T-G", "response_mode": "minimal"}
        )
    )
    assert len(json.dumps(m)) < len(json.dumps(c))


async def test_out_of_range_max_distance_is_validation_failed(mcp) -> None:
    data = structured(
        await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G", "max_distance": 99999})
    )
    assert data["success"] is False
    assert data["error_code"] == "validation_failed"
