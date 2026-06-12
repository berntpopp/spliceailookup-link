"""Variant input parsing and normalization.

The scoring API only accepts CHROM-POS-REF-ALT. Users (and the website) supply
many shapes: dash/colon/whitespace-delimited coordinates, transcript or genomic
HGVS, and rsIDs. This module classifies an input and, for coordinate-shaped
inputs, normalizes it to the canonical `chrom-pos-ref-alt` the API wants. HGVS
and rsID inputs are flagged for Ensembl VEP resolution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

InputKind = Literal["coordinate", "hgvs", "rsid"]

_VALID_CHROMS = {str(i) for i in range(1, 23)} | {"X", "Y", "M", "MT"}
_ALLELE_RE = re.compile(r"^[ACGTN]+$", re.IGNORECASE)
_RSID_RE = re.compile(r"^rs\d+$", re.IGNORECASE)
# HGVS markers: a transcript/genomic reference followed by a :c./:g./:n./:m. change.
_HGVS_RE = re.compile(r"[:.]\s*[cgnmr]\.", re.IGNORECASE)
_HGVS_PREFIX_RE = re.compile(r"^(NM_|NR_|NC_|NG_|XM_|XR_|ENST|ENSG|LRG_)", re.IGNORECASE)


class VariantParseError(ValueError):
    """Raised when an input cannot be interpreted as any supported variant form.

    Message contains only static guidance (no echoed user values), so it is safe
    to surface verbatim in an error envelope.
    """


class UnsupportedContigError(VariantParseError):
    """Raised when a variant's contig is outside the splice models' nuclear scope.

    SpliceAI and Pangolin are trained on the nuclear chromosomes (1-22, X, Y);
    mitochondrial (M/MT) and non-standard contigs are out of model scope and would
    otherwise burn a slow upstream slot before a 503. Subclasses VariantParseError
    so the error layer maps it deterministically (a distinct code, checked first).
    """


# Contigs the SpliceAI / Pangolin models actually score (nuclear genome only).
SCORING_CONTIGS = {str(i) for i in range(1, 23)} | {"X", "Y"}


def unsupported_contig_reason(variant_id: str) -> str | None:
    """Return a reason string if variant_id's contig is not scorable, else None."""
    chrom = variant_id.split("-", 1)[0]
    c = chrom.removeprefix("chr").removeprefix("CHR").upper()
    if c in SCORING_CONTIGS:
        return None
    if c in ("M", "MT"):
        return (
            "Mitochondrial contig (MT) is not supported by the SpliceAI/Pangolin "
            "splice models, which score only the nuclear chromosomes (1-22, X, Y)."
        )
    return (
        f"Contig '{chrom}' is not supported by the SpliceAI/Pangolin splice models, "
        "which score only the nuclear chromosomes (1-22, X, Y)."
    )


@dataclass(frozen=True)
class VariantInput:
    """A classified variant input.

    For `coordinate`, `value` is the canonical CHROM-POS-REF-ALT. For `hgvs` and
    `rsid`, `value` is the cleaned string to hand to Ensembl VEP.
    """

    kind: InputKind
    value: str


def looks_like_rsid(text: str) -> bool:
    return bool(_RSID_RE.match(text.strip()))


def looks_like_hgvs(text: str) -> bool:
    t = text.strip()
    return bool(_HGVS_RE.search(t)) or bool(_HGVS_PREFIX_RE.match(t))


def clean_hgvs(text: str) -> str:
    """Strip the website-style annotations the VEP endpoint does not want.

    `NM_001089.3(ABCA3):c.875A>T (p.Glu292Val)` -> `NM_001089.3:c.875A>T`.
    """
    t = text.strip()
    # Drop a trailing protein annotation in parentheses, e.g. " (p.Glu292Val)".
    t = re.sub(r"\s*\(p\.[^)]*\)\s*$", "", t, flags=re.IGNORECASE)
    # Drop a gene name in parentheses between the transcript and the colon,
    # e.g. "NM_001089.3(ABCA3):c..." -> "NM_001089.3:c...".
    t = re.sub(r"\(([^)]*)\)(?=\s*:)", "", t)
    return t.strip()


def normalize_coordinate(text: str) -> str | None:
    """Return canonical CHROM-POS-REF-ALT for a coordinate-shaped input, else None.

    Accepts dash, colon, or whitespace/tab delimiters and an optional `chr`
    prefix. Returns None (rather than raising) when the input is not four
    coordinate tokens, so the caller can fall through to HGVS/rsID handling.
    """
    t = text.strip()
    if not t:
        return None
    # Split on any run of dash / colon / whitespace.
    tokens = re.split(r"[\s:\-]+", t)
    if len(tokens) != 4:
        return None
    chrom, pos, ref, alt = tokens
    chrom = re.sub(r"^chr", "", chrom, flags=re.IGNORECASE).upper()
    if chrom not in _VALID_CHROMS:
        return None
    if not pos.isdigit() or int(pos) < 1:
        return None
    ref_u, alt_u = ref.upper(), alt.upper()
    if not _ALLELE_RE.match(ref_u) or not _ALLELE_RE.match(alt_u):
        return None
    return f"{chrom}-{int(pos)}-{ref_u}-{alt_u}"


def parse_variant_input(text: str) -> VariantInput:
    """Classify a raw variant string into a coordinate / hgvs / rsid input.

    Resolution order: explicit rsID, then coordinate (the unambiguous 4-token
    shape), then HGVS. Raises VariantParseError when nothing matches.
    """
    if text is None or not str(text).strip():
        raise VariantParseError(
            "Empty variant input. Provide CHROM-POS-REF-ALT (e.g. 8-140300616-T-G), "
            "HGVS (e.g. NM_000123.4:c.10A>T), or an rsID."
        )
    t = str(text).strip()

    if looks_like_rsid(t):
        return VariantInput(kind="rsid", value=t.lower())

    coordinate = normalize_coordinate(t)
    if coordinate is not None:
        return VariantInput(kind="coordinate", value=coordinate)

    if looks_like_hgvs(t):
        return VariantInput(kind="hgvs", value=clean_hgvs(t))

    raise VariantParseError(
        "Could not interpret the input as a variant. Supported forms: "
        "CHROM-POS-REF-ALT (chr optional; dash/colon/space delimited), "
        "transcript or genomic HGVS (e.g. NM_000123.4:c.10A>T or 17:g.43044295G>A), "
        "or an rsID (e.g. rs6025)."
    )


def split_variant_id(variant_id: str) -> tuple[str, int, str, str]:
    """Split a canonical CHROM-POS-REF-ALT id. Raises VariantParseError if malformed."""
    parts = variant_id.split("-")
    if len(parts) != 4:
        raise VariantParseError(f"Malformed variant id (expected CHROM-POS-REF-ALT): {variant_id}")
    chrom, pos_s, ref, alt = parts
    if not pos_s.isdigit():
        raise VariantParseError(f"Malformed variant id (non-integer position): {variant_id}")
    return chrom, int(pos_s), ref, alt
