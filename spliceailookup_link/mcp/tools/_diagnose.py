"""Distinguish wrong-REF from wrong-build on a coordinate prediction.

A cached Ensembl reference-base lookup classifies a coordinate failure:
- preflight_ref_mismatch runs BEFORE scoring (in prepare_variant): a REF that
  does not match the requested-build reference is a fast ref_mismatch, never a
  ~17s not_found, and never a misleading build_mismatch.
- diagnose_coordinate_failure runs on the post-scoring not_found path as a
  safety net (e.g. when Ensembl was unavailable at preflight time). It only
  asserts build_mismatch via the scoring cross_build_probe, which confirms the
  variant actually scores on the other build, so the redirect is productive.
"""

from __future__ import annotations

from spliceailookup_link.config import GenomeBuild
from spliceailookup_link.mcp.errors import BuildMismatchError, RefMismatchError
from spliceailookup_link.mcp.tools._common import cross_build_probe
from spliceailookup_link.services import SpliceService
from spliceailookup_link.variant import VariantParseError, split_variant_id

_ACGT = set("ACGT")


def _is_simple_ref(ref: str) -> bool:
    return bool(ref) and all(b in _ACGT for b in ref.upper())


async def _build_ref_mismatch(
    service: SpliceService,
    *,
    variant_id: str,
    chrom: str,
    pos: int,
    ref: str,
    requested_base: str,
    requested_build: GenomeBuild,
) -> RefMismatchError:
    """Construct a RefMismatchError, enriched with a secondary other-build hint
    when the typed REF happens to match the other build's base (D1)."""
    other: GenomeBuild = "GRCh37" if requested_build == "GRCh38" else "GRCh38"
    other_base = await service.reference_base(chrom, pos, len(ref), other)
    hint: dict[str, str] | None = None
    if other_base == ref.upper():
        hint = {
            "build": other,
            "note": (
                f"REF '{ref.upper()}' matches the {other} reference base at "
                f"{chrom}:{pos}; if you intended {other}, re-run with "
                f"genome_build={other}, or call resolve_variant for canonical "
                "CHROM-POS-REF-ALT."
            ),
        }
    return RefMismatchError(
        variant_id=variant_id,
        observed_ref=ref.upper(),
        reference_base=requested_base,
        build=requested_build,
        chrom=chrom,
        pos=pos,
        other_build_hint=hint,
    )


async def preflight_ref_mismatch(
    service: SpliceService, *, variant_id: str, requested_build: GenomeBuild
) -> None:
    """Raise RefMismatchError when the coordinate's REF does not match the
    requested-build reference base. No-op (return) when inconclusive (Ensembl
    unavailable), when the REF matches, or for non-ACGT/symbolic REFs."""
    try:
        chrom, pos, ref, _alt = split_variant_id(variant_id)
    except VariantParseError:
        return
    if not _is_simple_ref(ref):
        return
    requested_base = await service.reference_base(chrom, pos, len(ref), requested_build)
    if requested_base is None or requested_base == ref.upper():
        return
    raise await _build_ref_mismatch(
        service,
        variant_id=variant_id,
        chrom=chrom,
        pos=pos,
        ref=ref,
        requested_base=requested_base,
        requested_build=requested_build,
    )


async def diagnose_coordinate_failure(
    service: SpliceService,
    *,
    variant_id: str,
    requested_build: GenomeBuild,
    distance: int,
    mask: int,
    gene_set: str,
) -> None:
    """Post-scoring not_found safety net. Returning without raising means a
    genuine not_found (well-formed variant, no overlapping transcript)."""
    try:
        chrom, pos, ref, _alt = split_variant_id(variant_id)
    except VariantParseError:
        return
    if not _is_simple_ref(ref):
        return
    requested_base = await service.reference_base(chrom, pos, len(ref), requested_build)
    if requested_base is None:
        # Ensembl inconclusive: fall back to a scoring probe of the other build,
        # which raises BuildMismatchError only if the variant actually scores there.
        await _probe_fallback(service, variant_id, requested_build, distance, mask, gene_set)
        return
    if requested_base == ref.upper():
        return  # REF matches the requested-build reference: genuine not_found.
    # Position is in-range (prepare_variant already ruled out build_mismatch) and
    # the REF is wrong -> ref_mismatch (with optional hint). Never build_mismatch.
    raise await _build_ref_mismatch(
        service,
        variant_id=variant_id,
        chrom=chrom,
        pos=pos,
        ref=ref,
        requested_base=requested_base,
        requested_build=requested_build,
    )


async def _probe_fallback(
    service: SpliceService,
    variant_id: str,
    requested_build: GenomeBuild,
    distance: int,
    mask: int,
    gene_set: str,
) -> None:
    other = await cross_build_probe(
        service,
        model="spliceai",
        requested_build=requested_build,
        variant_id=variant_id,
        distance=distance,
        mask=mask,
        gene_set=gene_set,
    )
    if other:
        raise BuildMismatchError(
            variant_id=variant_id,
            inferred_build=other,
            requested_build=requested_build,
        )
