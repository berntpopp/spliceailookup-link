"""Shared helpers for the prediction tools (variant prep, cross-server hints)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spliceailookup_link.config import GenomeBuild
from spliceailookup_link.mcp.build_check import detect_build_mismatch
from spliceailookup_link.mcp.errors import BuildMismatchError
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
    return PreparedVariant(
        variant_id=resolution["variant_id"],
        genome_build=genome_build,
        consequence=resolution.get("consequence"),
        resolution=resolution,
    )


def see_also_for(
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
    return hints
