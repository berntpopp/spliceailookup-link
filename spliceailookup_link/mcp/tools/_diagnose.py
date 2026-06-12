"""Distinguish wrong-REF from wrong-build on a coordinate prediction.

A cached Ensembl reference-base lookup classifies a coordinate failure:
- preflight_ref_mismatch runs BEFORE scoring (in prepare_variant): a REF that
  does not match the requested-build reference is a fast ref_mismatch, never a
  ~17s not_found, and never a misleading build_mismatch.
- diagnose_coordinate_failure runs on the post-scoring not_found path as a
  safety net (e.g. when Ensembl was unavailable at preflight time). It only
  asserts build_mismatch via the scoring cross_build_probe, which confirms the
  variant actually scores on the other build, so the redirect is productive.

`check_ref` is the single Ensembl-comparison core: it returns a RefCheck verdict
instead of raising, so the predict pre-flight (which raises) and resolve_variant
(which warns) share one path. `preflight_no_overlap` reuses the same
fast-fail-before-dispatch idea for genuine not_found (D4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from spliceailookup_link.api import DataNotFoundError
from spliceailookup_link.config import GenomeBuild
from spliceailookup_link.mcp.errors import BuildMismatchError, RefMismatchError
from spliceailookup_link.mcp.tools._common import cross_build_probe
from spliceailookup_link.services import SpliceService
from spliceailookup_link.variant import VariantParseError, split_variant_id

_ACGT = set("ACGT")


def _is_simple_ref(ref: str) -> bool:
    return bool(ref) and all(b in _ACGT for b in ref.upper())


@dataclass
class RefCheck:
    """Verdict of comparing a coordinate's REF to the requested-build reference base."""

    status: Literal["match", "mismatch", "inconclusive", "skip"]
    requested_base: str | None = None
    observed_ref: str | None = None
    chrom: str | None = None
    pos: int | None = None
    other_build: GenomeBuild | None = None


async def check_ref(
    service: SpliceService, *, variant_id: str, requested_build: GenomeBuild
) -> RefCheck:
    """Compare a coordinate's REF to the requested-build reference base (no raise).

    'skip' for malformed ids / non-ACGT REFs (nothing to check); 'inconclusive'
    when Ensembl is unavailable; 'match'/'mismatch' otherwise. On 'mismatch',
    `other_build` is set when the typed REF matches the OTHER build's base.
    """
    try:
        chrom, pos, ref, _alt = split_variant_id(variant_id)
    except VariantParseError:
        return RefCheck(status="skip")
    if not _is_simple_ref(ref):
        return RefCheck(status="skip")
    requested_base = await service.reference_base(chrom, pos, len(ref), requested_build)
    if requested_base is None:
        return RefCheck(status="inconclusive", chrom=chrom, pos=pos, observed_ref=ref.upper())
    if requested_base == ref.upper():
        return RefCheck(
            status="match",
            requested_base=requested_base,
            observed_ref=ref.upper(),
            chrom=chrom,
            pos=pos,
        )
    other: GenomeBuild = "GRCh37" if requested_build == "GRCh38" else "GRCh38"
    other_base = await service.reference_base(chrom, pos, len(ref), other)
    return RefCheck(
        status="mismatch",
        requested_base=requested_base,
        observed_ref=ref.upper(),
        chrom=chrom,
        pos=pos,
        other_build=other if other_base == ref.upper() else None,
    )


def _ref_mismatch_error(
    variant_id: str, requested_build: GenomeBuild, check: RefCheck
) -> RefMismatchError:
    """Build a RefMismatchError from a mismatch RefCheck (with optional other-build hint)."""
    hint: dict[str, str] | None = None
    if check.other_build:
        hint = {
            "build": check.other_build,
            "note": (
                f"REF '{check.observed_ref}' matches the {check.other_build} reference base at "
                f"{check.chrom}:{check.pos}; if you intended {check.other_build}, re-run with "
                f"genome_build={check.other_build}, or call resolve_variant for canonical "
                "CHROM-POS-REF-ALT."
            ),
        }
    try:
        _c, _p, _r, alt = split_variant_id(variant_id)
    except VariantParseError:
        alt = ""
    return RefMismatchError(
        variant_id=variant_id,
        observed_ref=check.observed_ref or "",
        reference_base=check.requested_base or "",
        build=requested_build,
        chrom=check.chrom or "",
        pos=check.pos or 0,
        alt=alt,
        other_build_hint=hint,
    )


async def preflight_ref_mismatch(
    service: SpliceService, *, variant_id: str, requested_build: GenomeBuild
) -> None:
    """Raise RefMismatchError when the coordinate's REF mismatches the requested build.

    No-op when the REF matches, is inconclusive (Ensembl unavailable), or is
    non-ACGT/symbolic.
    """
    check = await check_ref(service, variant_id=variant_id, requested_build=requested_build)
    if check.status == "mismatch":
        raise _ref_mismatch_error(variant_id, requested_build, check)


async def preflight_no_overlap(
    service: SpliceService, *, variant_id: str, requested_build: GenomeBuild, window: int
) -> None:
    """Raise DataNotFoundError fast when no transcript overlaps the scan window (D4).

    Conservative: only a conclusive zero-overlap count fast-fails; None (Ensembl
    unavailable / unexpected) or >0 falls through to real scoring, so the pre-check
    can confirm a not_found but never invent one.
    """
    try:
        chrom, pos, _ref, _alt = split_variant_id(variant_id)
    except VariantParseError:
        return
    count = await service.overlapping_transcripts(chrom, pos, requested_build, window)
    if count == 0:
        err = DataNotFoundError(
            "No transcript overlaps the scan window for this coordinate; SpliceAI/Pangolin "
            "return no scores (fast-failed locally before dispatch)."
        )
        # W4: best-effort distance to the nearest annotated transcript so a caller
        # can decide whether widening max_distance would help. Any fault -> omit.
        try:
            nearest = await service.nearest_transcript(chrom, pos, requested_build)
        except Exception:
            nearest = None
        if nearest is not None:
            err.nearest_transcript = nearest  # type: ignore[attr-defined]
        raise err


async def diagnose_coordinate_failure(
    service: SpliceService,
    *,
    variant_id: str,
    requested_build: GenomeBuild,
    distance: int,
    mask: int,
    gene_set: str,
) -> None:
    """Post-scoring not_found safety net. Returning without raising = genuine not_found."""
    check = await check_ref(service, variant_id=variant_id, requested_build=requested_build)
    if check.status == "mismatch":
        raise _ref_mismatch_error(variant_id, requested_build, check)
    if check.status == "inconclusive":
        # Ensembl inconclusive: fall back to a scoring probe of the other build,
        # which raises BuildMismatchError only if the variant actually scores there.
        await _probe_fallback(service, variant_id, requested_build, distance, mask, gene_set)
    # match / skip -> genuine not_found.


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
