"""#115: an unreachable Ensembl must not stall predict_* or be blamed on the scoring service."""

from __future__ import annotations

import httpx
import respx

from spliceailookup_link.api import EnsemblVepClient
from spliceailookup_link.config import settings
from spliceailookup_link.services import SpliceService
from tests.conftest import StubService, structured


@respx.mock
async def test_preflight_lookup_is_one_short_attempt() -> None:
    route = respx.get("https://rest.ensembl.org/sequence/region/human/8:140300616..140300616")
    route.side_effect = httpx.ConnectTimeout("unreachable")
    client = EnsemblVepClient()
    base = await client.reference_base("8", 140300616, 1, "GRCh38")
    await client.close()
    assert base is None
    assert route.call_count == 1  # no retry chain
    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout["connect"] == settings.ENSEMBL_PREFLIGHT_TIMEOUT_SECONDS


async def test_predict_returns_scores_with_ensembl_degraded_note(
    mcp, stub_service: StubService
) -> None:
    stub_service.ref_bases = {}  # reference_base -> None: Ensembl unavailable
    res = await mcp.call_tool("predict_splicing", {"variant_id": "chr8-140300616-T-G"})
    data = structured(res)
    assert data["spliceai"]["max_delta_score"] is not None
    assert "Ensembl" in data["degraded_mode"]


async def test_ensembl_fault_is_not_cached() -> None:
    class _DownEnsembl:
        calls = 0

        async def reference_base(self, *_a: object) -> None:
            self.calls += 1

        async def close(self) -> None:  # pragma: no cover
            return None

    ens = _DownEnsembl()
    svc = SpliceService(ensembl_client=ens)  # type: ignore[arg-type]
    assert await svc.reference_base("8", 140300616, 1, "GRCh38") is None
    assert await svc.reference_base("8", 140300616, 1, "GRCh38") is None
    assert ens.calls == 2  # a transient fault is retried, not cached for the TTL
