"""Heuristic build-mismatch detection for CHROM-POS variant IDs.

Compares the position component of a variant id against per-build chromosome
length tables. When the position is unambiguously within one build's range but
beyond the other's, the requested genome_build can be flagged as likely wrong
before spending a slow, rate-limited scoring call. Ambiguous positions (within
both builds) return None and the call proceeds. Adapted from gnomad-link.
"""

from __future__ import annotations

# GRCh38 chromosome lengths (1-based). Source: GRC / UCSC.
_GRCH38_LENGTHS: dict[str, int] = {
    "1": 248_956_422,
    "2": 242_193_529,
    "3": 198_295_559,
    "4": 190_214_555,
    "5": 181_538_259,
    "6": 170_805_979,
    "7": 159_345_973,
    "8": 145_138_636,
    "9": 138_394_717,
    "10": 133_797_422,
    "11": 135_086_622,
    "12": 133_275_309,
    "13": 114_364_328,
    "14": 107_043_718,
    "15": 101_991_189,
    "16": 90_338_345,
    "17": 83_257_441,
    "18": 80_373_285,
    "19": 58_617_616,
    "20": 64_444_167,
    "21": 46_709_983,
    "22": 50_818_468,
    "X": 156_040_895,
    "Y": 57_227_415,
}

# GRCh37 chromosome lengths (1-based). Source: GRC / UCSC.
_GRCH37_LENGTHS: dict[str, int] = {
    "1": 249_250_621,
    "2": 243_199_373,
    "3": 198_022_430,
    "4": 191_154_276,
    "5": 180_915_260,
    "6": 171_115_067,
    "7": 159_138_663,
    "8": 146_364_022,
    "9": 141_213_431,
    "10": 135_534_747,
    "11": 135_006_516,
    "12": 133_851_895,
    "13": 115_169_878,
    "14": 107_349_540,
    "15": 102_531_392,
    "16": 90_354_753,
    "17": 81_195_210,
    "18": 78_077_248,
    "19": 59_128_983,
    "20": 63_025_520,
    "21": 48_129_895,
    "22": 51_304_566,
    "X": 155_270_560,
    "Y": 59_373_566,
}


def _strip_chr(chrom: str) -> str:
    return chrom.removeprefix("chr").removeprefix("CHR").upper()


def likely_build(chrom: str, pos: int) -> str | None:
    """Return 'GRCh37', 'GRCh38', or None for ambiguous/mito/unknown chromosomes.

    Mitochondrial and unplaced contigs return None (mito coordinates are
    build-stable; the position-length heuristic does not apply).
    """
    c = _strip_chr(chrom)
    if c in ("M", "MT") or c not in _GRCH38_LENGTHS:
        return None
    g37 = _GRCH37_LENGTHS.get(c, 0)
    g38 = _GRCH38_LENGTHS[c]
    if pos > g38 and pos <= g37:
        return "GRCh37"
    if pos > g37 and pos <= g38:
        return "GRCh38"
    return None


def detect_build_mismatch(variant_id: str, requested_build: str) -> str | None:
    """Return the inferred build when variant_id clearly belongs to a different build, else None."""
    try:
        chrom, pos_s, _, _ = variant_id.split("-", 3)
        pos = int(pos_s)
    except (ValueError, AttributeError):
        return None
    inferred = likely_build(chrom, pos)
    if inferred and inferred != requested_build:
        return inferred
    return None
