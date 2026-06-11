"""Tests for the httpx-based scoring + Ensembl clients (respx-mocked)."""

from __future__ import annotations

import httpx
import pytest
import respx

from spliceailookup_link.api import (
    DataNotFoundError,
    EnsemblVepClient,
    RateLimitedError,
    ScoringClient,
    SpliceApiError,
    UpstreamInputError,
)
from tests.fixtures.api_responses import (
    SPLICEAI_NO_SCORES,
    SPLICEAI_PARSE_ERROR,
    SPLICEAI_TRAPPC9,
    VEP_ABCA3,
)

_SAI38 = "https://spliceai-38-xwkwwwxdwq-uc.a.run.app/spliceai/"


@respx.mock
async def test_score_success() -> None:
    respx.get(_SAI38).mock(return_value=httpx.Response(200, json=SPLICEAI_TRAPPC9))
    client = ScoringClient()
    result = await client.score(
        model="spliceai", build="GRCh38", variant="8-140300616-T-G", distance=500, mask=0
    )
    assert result["scores"][0]["g_name"] == "TRAPPC9"
    await client.close()


@respx.mock
async def test_score_parse_error_maps_to_input_error() -> None:
    respx.get(_SAI38).mock(return_value=httpx.Response(200, json=SPLICEAI_PARSE_ERROR))
    client = ScoringClient()
    with pytest.raises(UpstreamInputError):
        await client.score(
            model="spliceai", build="GRCh38", variant="notavariant", distance=50, mask=0
        )
    await client.close()


@respx.mock
async def test_score_no_scores_maps_to_not_found() -> None:
    respx.get(_SAI38).mock(return_value=httpx.Response(200, json=SPLICEAI_NO_SCORES))
    client = ScoringClient()
    with pytest.raises(DataNotFoundError):
        await client.score(
            model="spliceai", build="GRCh38", variant="6-31740453-G-T", distance=50, mask=0
        )
    await client.close()


@respx.mock
async def test_persistent_429_maps_to_rate_limited() -> None:
    respx.get(_SAI38).mock(return_value=httpx.Response(429, text="Too Many Requests"))
    client = ScoringClient()
    with pytest.raises(RateLimitedError):
        await client.score(
            model="spliceai", build="GRCh38", variant="8-140300616-T-G", distance=50, mask=0
        )
    await client.close()


@respx.mock
async def test_500_retries_then_raises_upstream() -> None:
    route = respx.get(_SAI38).mock(return_value=httpx.Response(503, text="Service Unavailable"))
    client = ScoringClient()
    with pytest.raises(SpliceApiError):
        await client.score(
            model="spliceai", build="GRCh38", variant="8-140300616-T-G", distance=50, mask=0
        )
    # MAX_RETRIES default 3 -> 4 attempts total.
    assert route.call_count >= 2
    await client.close()


@respx.mock
async def test_ensembl_resolve_hgvs() -> None:
    respx.get(url__startswith="https://rest.ensembl.org/vep/human/hgvs/").mock(
        return_value=httpx.Response(200, json=VEP_ABCA3)
    )
    client = EnsemblVepClient()
    rec = await client.resolve_hgvs("NM_001089.3:c.875A>T", "GRCh38")
    assert rec["vcf_string"] == "16-2317763-T-A"
    await client.close()


@respx.mock
async def test_ensembl_400_maps_to_input_error() -> None:
    respx.get(url__startswith="https://rest.ensembl.org/vep/human/hgvs/").mock(
        return_value=httpx.Response(400, json={"error": "bad HGVS"})
    )
    client = EnsemblVepClient()
    with pytest.raises(UpstreamInputError):
        await client.resolve_hgvs("garbage:c.bad", "GRCh38")
    await client.close()
