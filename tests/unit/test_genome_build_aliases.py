"""Issue #120: genome_build accepts UCSC aliases (hg19/hg38), case-insensitively."""

from __future__ import annotations

import pytest

from tests.conftest import StubService, expect_tool_error

_BUILD_TOOLS = {
    "resolve_variant": {"variant_id": "chr8-140300616-T-G"},
    "predict_spliceai": {"variant_id": "chr8-140300616-T-G"},
    "predict_pangolin": {"variant_id": "chr8-140300616-T-G"},
    "predict_splicing": {"variant_id": "chr8-140300616-T-G"},
    "predict_splicing_batch": {"variant_ids": ["chr8-140300616-T-G"]},
    "warmup": {},
}


async def test_every_build_tool_advertises_the_aliases(mcp) -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    with_build = {n for n, t in tools.items() if "genome_build" in t.parameters["properties"]}
    assert with_build == set(_BUILD_TOOLS)
    for name in with_build:
        prop = tools[name].parameters["properties"]["genome_build"]
        assert set(prop["enum"]) == {"GRCh37", "GRCh38", "hg19", "hg38"}, name
        assert "case-insensitive" in prop["description"], name


@pytest.mark.parametrize(
    ("given", "canonical"),
    [("hg38", "GRCh38"), ("HG19", "GRCh37"), ("grch38", "GRCh38"), ("GRCh37", "GRCh37")],
)
@pytest.mark.parametrize("tool", sorted(_BUILD_TOOLS))
async def test_alias_normalises_to_canonical(
    mcp, stub_service: StubService, tool: str, given: str, canonical: str
) -> None:
    await mcp.call_tool(tool, {**_BUILD_TOOLS[tool], "genome_build": given})
    builds = {c["build"] for c in stub_service.score_calls} | {
        c["build"] for c in stub_service.resolve_calls
    }
    builds |= set(stub_service.warmup_builds)
    assert builds == {canonical}, (tool, builds)


async def test_unknown_build_still_rejected(mcp) -> None:
    env = await expect_tool_error(
        mcp, "predict_splicing", {"variant_id": "chr8-140300616-T-G", "genome_build": "hg18"}
    )
    assert env["error_code"] == "invalid_input"
