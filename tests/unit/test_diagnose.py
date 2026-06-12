"""F1+F8: coordinate-failure diagnostic (ref_mismatch vs cheap build_mismatch)."""

from __future__ import annotations

import pytest

from spliceailookup_link.mcp.errors import BuildMismatchError, RefMismatchError
from spliceailookup_link.mcp.tools._diagnose import diagnose_coordinate_failure
from tests.conftest import StubService


async def _run(svc: StubService, variant_id: str, build: str = "GRCh38") -> None:
    await diagnose_coordinate_failure(
        svc,
        variant_id=variant_id,
        requested_build=build,
        distance=500,
        mask=0,
        gene_set="basic",
    )


async def test_ref_mismatch_when_ref_matches_neither_build() -> None:
    svc = StubService()
    svc.ref_bases = {"GRCh38": "T", "GRCh37": "C"}  # REF 'A' matches neither
    with pytest.raises(RefMismatchError) as ei:
        await _run(svc, "8-140300616-A-G")
    assert ei.value.reference_base == "T"
    assert svc.score_calls == []  # no slow scoring cross-build probe


async def test_ref_mismatch_with_secondary_hint_when_ref_matches_other_build() -> None:
    # D1: a wrong REF that coincidentally matches the OTHER build's base is a
    # ref_mismatch (the requested-build coordinate is valid), NOT a build_mismatch.
    svc = StubService()
    svc.ref_bases = {"GRCh38": "T", "GRCh37": "C"}  # REF 'C' matches GRCh37 only
    with pytest.raises(RefMismatchError) as ei:
        await _run(svc, "8-140300616-C-A", build="GRCh38")
    assert ei.value.reference_base == "T"
    assert ei.value.other_build_hint is not None
    assert ei.value.other_build_hint["build"] == "GRCh37"
    assert svc.score_calls == []  # no slow scoring probe


async def test_genuine_not_found_when_ref_matches_requested_build() -> None:
    svc = StubService()
    svc.ref_bases = {"GRCh38": "A", "GRCh37": "A"}  # REF matches -> real no-overlap
    await _run(svc, "8-140300616-A-G")  # returns (no raise)
    assert svc.score_calls == []


async def test_falls_back_to_scoring_probe_when_ensembl_unavailable() -> None:
    svc = StubService()
    svc.ref_bases = {}  # reference_base returns None -> inconclusive
    svc.only_build = "GRCh37"  # variant only scores in the other build
    with pytest.raises(BuildMismatchError):
        await _run(svc, "8-140300616-A-G", build="GRCh38")
    assert any(c["build"] == "GRCh37" for c in svc.score_calls)


async def test_skips_non_acgt_ref() -> None:
    svc = StubService()
    svc.ref_bases = {"GRCh38": "T", "GRCh37": "C"}
    await _run(svc, "8-140300616-N-G")  # symbolic/N ref -> no-op
    assert svc.refbase_calls == []
    assert svc.score_calls == []
