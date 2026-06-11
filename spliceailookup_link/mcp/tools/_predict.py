"""Shared predict_splicing core: resolve -> both models -> merge/dedup -> headline.

Used by predict_splicing (single) and predict_splicing_batch (fan-out). Returns a
result dict WITHOUT the outer success/_meta envelope; callers add _meta. The
scratch key ``_telemetry`` carries cache/gene/partial/resolution data that the
caller pops and folds into _meta.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from spliceailookup_link.api import DataNotFoundError
from spliceailookup_link.config import GenomeBuild
from spliceailookup_link.mcp.errors import BuildMismatchError
from spliceailookup_link.mcp.shaping import (
    ResponseMode,
    Transcripts,
    shape_pangolin,
    shape_spliceai,
)
from spliceailookup_link.mcp.tools._common import (
    cross_build_probe,
    mask_to_int,
    prepare_variant,
)
from spliceailookup_link.mcp.tools._predict_shape import (
    assess_agreement,
    combined_headline,
    combined_interpretation,
    minimal_combined,
)
from spliceailookup_link.services import SpliceService
from spliceailookup_link.services.telemetry import CallTelemetry

_IDENTITY_KEYS = (
    "gene",
    "gene_id",
    "transcript_id",
    "transcript_priority",
    "refseq_ids",
    "strand",
)


def _aggregate_cache(teles: list[CallTelemetry]) -> tuple[str | None, int | None]:
    caches = [t.cache for t in teles]
    if not caches:
        return None, None
    if all(c == "hit" for c in caches):
        cache = "hit"
    elif all(c == "miss" for c in caches):
        cache = "miss"
    else:
        cache = "partial"
    ups = [t.upstream_elapsed_ms for t in teles if t.upstream_elapsed_ms is not None]
    return cache, (max(ups) if ups else None)


def _lift_identity(
    sai_t: dict[str, Any] | None, pang_t: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Lift one shared transcript-identity block when both models agree on transcript."""
    if not sai_t or not pang_t:
        return None
    if sai_t.get("transcript_id") and sai_t.get("transcript_id") == pang_t.get("transcript_id"):
        return {k: sai_t.get(k) for k in _IDENTITY_KEYS}
    return None


async def predict_one(
    service: SpliceService,
    *,
    variant: str,
    genome_build: GenomeBuild,
    max_distance: int,
    mask: Literal["raw", "masked"],
    gene_set: Literal["basic", "comprehensive"],
    transcripts: Transcripts,
    response_mode: ResponseMode,
    cross_build_check: bool = True,
    ctx: Any = None,
) -> dict[str, Any]:
    if ctx is not None:
        await ctx.report_progress(progress=0, total=3, message="resolving variant")
    prepared = await prepare_variant(service, variant, genome_build)
    common: dict[str, Any] = {
        "build": prepared.genome_build,
        "variant_id": prepared.variant_id,
        "distance": max_distance,
        "mask": mask_to_int(mask),
        "gene_set": gene_set,
        "raw": variant,
        "consequence": prepared.consequence,
    }
    if ctx is not None:
        await ctx.report_progress(progress=1, total=3, message="scoring SpliceAI + Pangolin")
    gathered: list[Any] = list(
        await asyncio.gather(
            service.score(model="spliceai", **common),
            service.score(model="pangolin", **common),
            return_exceptions=True,
        )
    )
    sai_res, pang_res = gathered[0], gathered[1]
    if isinstance(sai_res, BaseException) and isinstance(pang_res, BaseException):
        if (
            cross_build_check
            and prepared.resolution is None
            and isinstance(sai_res, DataNotFoundError)
        ):
            other = await cross_build_probe(
                service,
                model="spliceai",
                requested_build=genome_build,
                variant_id=prepared.variant_id,
                distance=max_distance,
                mask=mask_to_int(mask),
                gene_set=gene_set,
            )
            if other:
                raise BuildMismatchError(
                    variant_id=prepared.variant_id,
                    inferred_build=other,
                    requested_build=genome_build,
                ) from sai_res
        raise sai_res

    if ctx is not None:
        await ctx.report_progress(progress=2, total=3, message="merging models")

    result: dict[str, Any] = {
        "variant_id": prepared.variant_id,
        "genome_build": genome_build,
        "max_distance": max_distance,
        "mask": mask,
        "gene_set": gene_set,
    }
    teles: list[CallTelemetry] = []
    gene = sai_max = pang_max = consequence = None
    sai_top = pang_top = None
    partial: list[str] = []

    # For combined shaping, always use at least compact internally so transcript
    # identity can be lifted (minimal_combined is applied at the end).
    _shape_mode = "compact" if response_mode == "minimal" else response_mode

    if isinstance(sai_res, BaseException):
        partial.append(f"spliceai_failed: {sai_res!s}"[:200])
    else:
        sai_payload, sai_tele = sai_res
        teles.append(sai_tele)
        shaped_sai = shape_spliceai(sai_payload, transcripts=transcripts, response_mode=_shape_mode)
        sai_max = shaped_sai.get("max_delta_score")
        consequence = shaped_sai.pop("consequence", None)  # F4: lift, do not duplicate
        if shaped_sai.get("transcripts"):
            sai_top = shaped_sai["transcripts"][0]
            gene = sai_top.get("gene")
        result["spliceai"] = shaped_sai

    if isinstance(pang_res, BaseException):
        partial.append(f"pangolin_failed: {pang_res!s}"[:200])
    else:
        pang_payload, pang_tele = pang_res
        teles.append(pang_tele)
        shaped_pang = shape_pangolin(
            pang_payload, transcripts=transcripts, response_mode=_shape_mode
        )
        pang_max = shaped_pang.get("max_delta_score")
        if shaped_pang.get("transcripts"):
            pang_top = shaped_pang["transcripts"][0]
            if gene is None:
                gene = pang_top.get("gene")
        result["pangolin"] = shaped_pang

    identity = _lift_identity(sai_top, pang_top)
    if identity:
        result["transcript"] = identity
        for sub in ("spliceai", "pangolin"):
            block = result.get(sub)
            if block and block.get("transcripts"):
                for k in _IDENTITY_KEYS:
                    block["transcripts"][0].pop(k, None)

    if consequence is not None:
        result["consequence"] = consequence
    result["agreement"] = assess_agreement(sai_max, pang_max)
    result["interpretation"] = combined_interpretation(sai_max, pang_max)
    result["headline"] = combined_headline(
        gene, genome_build, sai_max, pang_max, consequence, result["agreement"]
    )
    cache, ups = _aggregate_cache(teles)
    age_s = ttl_s = None  # populated in the telemetry task; placeholder keeps shape stable
    telemetry = {
        "cache": cache,
        "upstream_elapsed_ms": ups,
        "cache_age_s": age_s,
        "cache_ttl_s": ttl_s,
        "gene": gene,
        "partial": partial,
        "resolution": prepared.resolution,
        "resolved_consequence": prepared.consequence,
    }
    if response_mode == "minimal":
        body = minimal_combined(result, gene)
    else:
        body = result
    body["_telemetry"] = telemetry
    return body
