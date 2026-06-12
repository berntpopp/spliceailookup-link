"""Distinguish wrong-REF from wrong-build cheaply on a coordinate prediction failure.

Called by the prediction tools only on the both-models not_found path for a
coordinate input. Two cached Ensembl reference-base lookups replace a ~17s scoring
cross-build probe and turn a misleading not_found into an accurate ref_mismatch or
build_mismatch. Falls back to the scoring probe when the reference check is
inconclusive or Ensembl is unavailable, so behavior never regresses.
"""

from __future__ import annotations

from spliceailookup_link.config import GenomeBuild
from spliceailookup_link.mcp.errors import BuildMismatchError, RefMismatchError
from spliceailookup_link.mcp.tools._common import cross_build_probe
from spliceailookup_link.services import SpliceService
from spliceailookup_link.variant import VariantParseError, split_variant_id

_ACGT = set("ACGT")


async def diagnose_coordinate_failure(
    service: SpliceService,
    *,
    variant_id: str,
    requested_build: GenomeBuild,
    distance: int,
    mask: int,
    gene_set: str,
) -> None:
    """Raise RefMismatchError / BuildMismatchError when applicable; else return.

    Returning without raising means "genuine not_found" (well-formed variant with
    no overlapping transcript) -- the caller re-raises the original not_found.
    """
    try:
        chrom, pos, ref, _alt = split_variant_id(variant_id)
    except VariantParseError:
        return
    if not ref or any(b not in _ACGT for b in ref.upper()):
        return  # only simple ACGT refs; skip N / symbolic alleles

    requested_base = await service.reference_base(chrom, pos, len(ref), requested_build)
    if requested_base is None:
        await _probe_fallback(service, variant_id, requested_build, distance, mask, gene_set)
        return
    if requested_base == ref.upper():
        return  # REF matches the requested-build reference: real no-overlap not_found

    other: GenomeBuild = "GRCh37" if requested_build == "GRCh38" else "GRCh38"
    other_base = await service.reference_base(chrom, pos, len(ref), other)
    if other_base == ref.upper():
        raise BuildMismatchError(
            variant_id=variant_id,
            inferred_build=other,
            requested_build=requested_build,
        )
    raise RefMismatchError(
        variant_id=variant_id,
        observed_ref=ref.upper(),
        reference_base=requested_base,
        build=requested_build,
        chrom=chrom,
        pos=pos,
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
