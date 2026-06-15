"""Live integration tests against the real SpliceAI / Pangolin / Ensembl APIs.

Marked `integration` and excluded from the default `make test`. These hit the
rate-limited upstream and can be slow (10-40s each) or 503 under load. Run with
`make test-integration`. They double as the reverse-engineering cross-check:
the captured website results must match the tool output.
"""

from __future__ import annotations

import pytest

from spliceailookup_link.mcp.facade import create_spliceai_mcp
from spliceailookup_link.services import SpliceService
from tests.conftest import structured

pytestmark = pytest.mark.integration


@pytest.fixture
async def live_mcp():
    service = SpliceService()
    mcp = create_spliceai_mcp(service_factory=lambda: service)
    yield mcp
    await service.close()


async def test_resolve_hgvs_abca3(live_mcp) -> None:
    res = await live_mcp.call_tool(
        "resolve_variant", {"variant_id": "NM_001089.3(ABCA3):c.875A>T", "genome_build": "GRCh38"}
    )
    data = structured(res)
    assert data["variant_id"] == "16-2317763-T-A"
    assert data["gene_symbol"] == "ABCA3"


async def test_predict_splicing_trappc9(live_mcp) -> None:
    res = await live_mcp.call_tool(
        "predict_splicing", {"variant_id": "chr8-140300616-T-G", "genome_build": "GRCh38"}
    )
    data = structured(res)
    assert data["success"] is True
    # Matches the website: SpliceAI acceptor loss ~0.83, Pangolin splice loss ~0.85.
    assert data["spliceai"]["max_delta_score"] >= 0.7
    assert data["pangolin"]["max_delta_score"] >= 0.7
    assert data["agreement"]["verdict"] == "concordant_high"


async def test_whitespace_delimited_example(live_mcp) -> None:
    res = await live_mcp.call_tool(
        "predict_spliceai", {"variant_id": "6   31740453   G   T", "genome_build": "GRCh38"}
    )
    data = structured(res)
    assert data["success"] is True
    assert data["variant_id"] == "6-31740453-G-T"
