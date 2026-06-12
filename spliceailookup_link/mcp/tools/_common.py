"""Shared helpers for the prediction tools (variant prep, cross-server hints)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spliceailookup_link.api import DataNotFoundError
from spliceailookup_link.config import GenomeBuild
from spliceailookup_link.mcp.build_check import detect_build_mismatch
from spliceailookup_link.mcp.errors import AmbiguousVariantError, BuildMismatchError
from spliceailookup_link.services import SpliceService
from spliceailookup_link.variant import parse_variant_input

_MASK_TO_INT = {"raw": 0, "masked": 1}


def mask_to_int(mask: str) -> int:
    return _MASK_TO_INT.get(mask, 0)


@dataclass
class PreparedVariant:
    variant_id: str
    genome_build: GenomeBuild
    consequence: str | None
    resolution: dict[str, Any] | None  # populated when the input was HGVS/rsID


async def prepare_variant(
    service: SpliceService, raw_variant: str, genome_build: GenomeBuild
) -> PreparedVariant:
    """Normalize any input to a CHROM-POS-REF-ALT id, resolving HGVS/rsID via VEP.

    Raises VariantParseError (-> invalid_input) for uninterpretable input and
    BuildMismatchError (-> build_mismatch) when a coordinate clearly belongs to the
    other build, so a slow scoring call is never wasted on the wrong build.
    """
    parsed = parse_variant_input(raw_variant)
    if parsed.kind == "coordinate":
        inferred = detect_build_mismatch(parsed.value, genome_build)
        if inferred is not None:
            raise BuildMismatchError(
                variant_id=parsed.value,
                inferred_build=inferred,
                requested_build=genome_build,
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
) -> list[dict[str, Any]]:
    """Cross-server hints. minimal -> []; compact -> {server,hint}; full -> + example args."""
    if response_mode == "minimal":
        return []
    full = _see_also_full(variant_id, genome_build, gene)
    if response_mode == "full":
        return full
    return [{"server": h["server"], "hint": h["hint"]} for h in full]


def _see_also_full(
    variant_id: str, genome_build: GenomeBuild, gene: str | None
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
        hints.append(
            {
                "server": "gtex-link",
                "hint": f"tissue expression for {gene}",
                "example": {
                    "tool": "get_median_expression_levels",
                    "arguments": {"gencode_id": [gene]},
                },
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
