"""Tests for SpliceService resolution + caching with faked clients."""

from __future__ import annotations

import re
from typing import Any

from spliceailookup_link.services import SpliceService
from spliceailookup_link.services.telemetry import CallTelemetry
from tests.fixtures.api_responses import SPLICEAI_TRAPPC9, VEP_ABCA3, VEP_RS6025


class _FakeScoring:
    def __init__(self) -> None:
        self.calls = 0

    async def score(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return SPLICEAI_TRAPPC9

    async def close(self) -> None:
        return None


class _FakeEnsembl:
    def __init__(self) -> None:
        self.hgvs_calls = 0

    async def resolve_hgvs(self, hgvs: str, build: str) -> dict[str, Any]:
        self.hgvs_calls += 1
        return VEP_ABCA3[0]

    async def resolve_id(self, vid: str, build: str) -> dict[str, Any]:
        return VEP_ABCA3[0]

    async def close(self) -> None:
        return None


def _service() -> tuple[SpliceService, _FakeScoring, _FakeEnsembl]:
    scoring, ensembl = _FakeScoring(), _FakeEnsembl()
    svc = SpliceService(scoring_client=scoring, ensembl_client=ensembl, cache_ttl_minutes=60)
    return svc, scoring, ensembl


async def test_resolve_coordinate_skips_upstream() -> None:
    svc, _, ensembl = _service()
    out = await svc.resolve("chr8-140300616-T-G", "GRCh38")
    assert out["variant_id"] == "8-140300616-T-G"
    assert out["source"] == "direct"
    assert ensembl.hgvs_calls == 0


async def test_resolve_hgvs_uses_vep_and_normalizes() -> None:
    svc, _, ensembl = _service()
    out = await svc.resolve("NM_001089.3(ABCA3):c.875A>T", "GRCh38")
    assert out["variant_id"] == "16-2317763-T-A"
    assert out["gene_symbol"] == "ABCA3"
    assert out["consequence"] == "missense_variant"
    assert ensembl.hgvs_calls == 1


async def test_score_caches_identical_calls() -> None:
    svc, scoring, _ = _service()
    args = {
        "model": "spliceai",
        "build": "GRCh38",
        "variant_id": "8-140300616-T-G",
        "distance": 500,
        "mask": 0,
    }
    a, _ = await svc.score(**args)
    b, _ = await svc.score(**args)
    assert a is b or a == b
    # Second identical call served from cache -> exactly one upstream call.
    assert scoring.calls == 1


async def test_score_reports_cache_miss_then_hit() -> None:
    svc, scoring, _ = _service()
    args = {
        "model": "spliceai",
        "build": "GRCh38",
        "variant_id": "8-140300616-T-G",
        "distance": 500,
        "mask": 0,
    }
    _, t1 = await svc.score(**args)
    _, t2 = await svc.score(**args)
    assert isinstance(t1, CallTelemetry)
    assert t1.cache == "miss" and isinstance(t1.upstream_elapsed_ms, int)
    assert t2.cache == "hit" and t2.upstream_elapsed_ms is None
    assert scoring.calls == 1


async def test_score_distinct_params_not_cached_together() -> None:
    svc, scoring, _ = _service()
    base = {"model": "spliceai", "build": "GRCh38", "variant_id": "8-140300616-T-G", "mask": 0}
    await svc.score(distance=500, **base)
    await svc.score(distance=50, **base)
    assert scoring.calls == 2


async def test_warmup_calls_both_models() -> None:
    svc, scoring, _ = _service()
    detail = await svc.warmup("GRCh38")
    assert set(detail) == {"spliceai", "pangolin"}
    assert all(d["status"] == "ok" for d in detail.values())
    assert all(isinstance(d["elapsed_ms"], int) for d in detail.values())
    assert scoring.calls == 2


class _FakeEnsemblMulti:
    async def resolve_hgvs(self, hgvs: str, build: str) -> dict[str, Any]:
        return VEP_RS6025[0]

    async def resolve_id(self, vid: str, build: str) -> dict[str, Any]:
        return VEP_RS6025[0]

    async def close(self) -> None:
        return None


async def test_resolve_multiallelic_rsid_is_structured() -> None:
    svc = SpliceService(scoring_client=_FakeScoring(), ensembl_client=_FakeEnsemblMulti())
    out = await svc.resolve("rs6025", "GRCh38")
    coord = re.compile(r"^[\dXYM]+-\d+-[ACGT]+-[ACGT]+$")
    assert coord.match(out["variant_id"]), out["variant_id"]
    assert out["ambiguous"] is True
    assert len(out["variant_ids"]) == 2
    assert all(coord.match(v) for v in out["variant_ids"])
    assert "note" in out


async def test_cache_age_and_ttl_telemetry() -> None:
    svc, _, _ = _service()
    args = {
        "model": "spliceai",
        "build": "GRCh38",
        "variant_id": "8-140300616-T-G",
        "distance": 500,
        "mask": 0,
        "gene_set": "basic",
    }
    _, t1 = await svc.score(**args)
    _, t2 = await svc.score(**args)
    assert t1.cache == "miss"
    assert t1.cache_ttl_s == 3600
    assert t2.cache == "hit"
    assert isinstance(t2.cache_age_s, int) and t2.cache_age_s >= 0
    assert t2.cache_ttl_s == 3600
