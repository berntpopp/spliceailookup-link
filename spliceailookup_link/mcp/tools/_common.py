"""Shared helpers for the prediction tools (variant prep, cross-server hints)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from spliceailookup_link.api import DataNotFoundError, SpliceApiError
from spliceailookup_link.config import GenomeBuild, settings
from spliceailookup_link.mcp.build_check import detect_build_mismatch
from spliceailookup_link.mcp.errors import AmbiguousVariantError, BuildMismatchError
from spliceailookup_link.services import SpliceService
from spliceailookup_link.variant import (
    UnsupportedContigError,
    parse_variant_input,
    unsupported_contig_reason,
)

_MASK_TO_INT = {"raw": 0, "masked": 1}


def mask_to_int(mask: str) -> int:
    return _MASK_TO_INT.get(mask, 0)


def _reject_unsupported_contig(variant_id: str) -> None:
    """Fast-fail non-nuclear contigs (MT / non-standard) before any scoring call."""
    reason = unsupported_contig_reason(variant_id)
    if reason is not None:
        raise UnsupportedContigError(reason)


def running_as_task(ctx: Any) -> bool:
    return bool(ctx is not None and getattr(ctx, "is_background_task", False))


async def run_with_deadline(coro: Any, *, ctx: Any, enforce: bool = True) -> Any:
    """Await `coro` under the foreground soft deadline, or directly when bypassed.

    The deadline is bypassed when running as a background task, when `enforce`
    is False (e.g. a batch item whose parent batch is a background task), or when
    PREDICT_SOFT_DEADLINE_SECONDS is 0. On timeout, raises SpliceApiError, which
    the error layer classifies as a retryable upstream_unavailable.
    """
    deadline = settings.PREDICT_SOFT_DEADLINE_SECONDS
    bypass = not enforce or running_as_task(ctx)
    if deadline and not bypass:
        try:
            return await asyncio.wait_for(coro, timeout=deadline)
        except TimeoutError as exc:
            raise SpliceApiError(
                f"Scoring exceeded the server's {deadline}s deadline "
                "(comprehensive gene_set and/or a large max_distance are slow)."
            ) from exc
    return await coro


@dataclass
class PreparedVariant:
    variant_id: str
    genome_build: GenomeBuild
    consequence: str | None
    resolution: dict[str, Any] | None  # populated when the input was HGVS/rsID


async def prepare_variant(
    service: SpliceService,
    raw_variant: str,
    genome_build: GenomeBuild,
    *,
    cross_build_check: bool = True,
    max_distance: int = 500,
) -> PreparedVariant:
    """Normalize any input to a CHROM-POS-REF-ALT id, resolving HGVS/rsID via VEP.

    Raises VariantParseError (-> invalid_input) for uninterpretable input,
    BuildMismatchError (-> build_mismatch) when a coordinate's position cannot
    belong to the requested build, and RefMismatchError (-> ref_mismatch) when a
    coordinate's REF does not match the requested-build reference base -- both
    before any slow scoring call. The pre-flight ref check is gated by
    cross_build_check and settings.PREFLIGHT_REF_CHECK_ENABLED.
    """
    parsed = parse_variant_input(raw_variant)
    if parsed.kind == "coordinate":
        _reject_unsupported_contig(parsed.value)
        from spliceailookup_link.mcp.build_check import out_of_range

        chrom_s, pos_s, _, _ = parsed.value.split("-", 3)
        lengths = out_of_range(chrom_s, int(pos_s))
        if lengths is not None:
            from spliceailookup_link.mcp.errors import CoordinateRangeError

            raise CoordinateRangeError(
                chrom=chrom_s, pos=int(pos_s), grch38_len=lengths[0], grch37_len=lengths[1]
            )
        inferred = detect_build_mismatch(parsed.value, genome_build)
        if inferred is not None:
            raise BuildMismatchError(
                variant_id=parsed.value,
                inferred_build=inferred,
                requested_build=genome_build,
            )
        if cross_build_check and settings.PREFLIGHT_REF_CHECK_ENABLED:
            # Local import avoids a module cycle (_diagnose imports _common).
            from spliceailookup_link.mcp.tools._diagnose import preflight_ref_mismatch

            await preflight_ref_mismatch(
                service, variant_id=parsed.value, requested_build=genome_build
            )
        if cross_build_check and settings.PREFLIGHT_OVERLAP_CHECK_ENABLED:
            # D4: fast-fail a genuine not_found before the slow scoring dispatch.
            from spliceailookup_link.mcp.tools._diagnose import preflight_no_overlap

            await preflight_no_overlap(
                service,
                variant_id=parsed.value,
                requested_build=genome_build,
                window=max_distance,
            )
        return PreparedVariant(
            variant_id=parsed.value,
            genome_build=genome_build,
            consequence=None,
            resolution=None,
        )
    resolution = await service.resolve(raw_variant, genome_build)
    if resolution.get("ambiguous"):
        raise AmbiguousVariantError(
            variant=raw_variant,
            candidates=resolution.get("variant_ids") or [resolution["variant_id"]],
            note=resolution.get("note"),
        )
    _reject_unsupported_contig(resolution["variant_id"])
    return PreparedVariant(
        variant_id=resolution["variant_id"],
        genome_build=genome_build,
        consequence=resolution.get("consequence"),
        resolution=resolution,
    )


async def cross_build_probe(
    service: SpliceService,
    *,
    model: str,
    requested_build: GenomeBuild,
    variant_id: str,
    distance: int,
    mask: int,
    gene_set: str,
) -> GenomeBuild | None:
    """Return the OTHER build if the variant scores there (cache-backed), else None."""
    other: GenomeBuild = "GRCh37" if requested_build == "GRCh38" else "GRCh38"
    try:
        payload, _ = await service.score(
            model=model,
            build=other,
            variant_id=variant_id,
            distance=distance,
            mask=mask,
            gene_set=gene_set,
        )
    except DataNotFoundError:
        return None
    return other if payload.get("scores") else None


def see_also_for(
    variant_id: str,
    genome_build: GenomeBuild,
    gene: str | None,
    response_mode: str = "compact",
    gene_id: str | None = None,
) -> list[dict[str, Any]]:
    """Cross-server hints. minimal -> []; compact -> {server,hint}; full -> + example args."""
    if response_mode == "minimal":
        return []
    full = _see_also_full(variant_id, genome_build, gene, gene_id)
    if response_mode == "full":
        return full
    return [{"server": h["server"], "hint": h["hint"]} for h in full]


def _see_also_full(
    variant_id: str, genome_build: GenomeBuild, gene: str | None, gene_id: str | None = None
) -> list[dict[str, Any]]:
    """Cross-server follow-up hints (sibling -link MCP servers). Not callable here."""
    dataset = "gnomad_r4" if genome_build == "GRCh38" else "gnomad_r2_1"
    hints: list[dict[str, Any]] = [
        {
            "server": "gnomad-link",
            "hint": "allele frequency and ClinVar classification for this variant",
            "example": {
                "tool": "get_variant_frequencies",
                "arguments": {"variant_id": variant_id, "dataset": dataset},
            },
        }
    ]
    if gene:
        hints.append(
            {
                "server": "genereviews-link",
                "hint": f"gene-disease context for {gene}",
                "example": {"tool": "search_passages", "arguments": {"q": gene}},
            }
        )
        if gene_id:
            # F4: gtex expects an Ensembl/GENCODE id, not a gene symbol.
            gtex_example = {
                "tool": "get_median_expression_levels",
                "arguments": {"gencode_id": [gene_id]},
            }
        else:
            gtex_example = {"tool": "search_gtex_genes", "arguments": {"query": gene}}
        hints.append(
            {
                "server": "gtex-link",
                "hint": f"tissue expression for {gene}",
                "example": gtex_example,
            }
        )
        hints.append(
            {
                "server": "uniprot-link",
                "hint": f"protein domains, features, and disease variants for {gene}",
                "example": {
                    "tool": "find_proteins",
                    "arguments": {"gene": gene, "organism_taxon": 9606, "reviewed": True},
                },
            }
        )
    return hints
