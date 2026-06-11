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
