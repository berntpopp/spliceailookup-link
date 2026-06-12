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
