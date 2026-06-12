"""Single source of truth for model/annotation provenance in prediction payloads.

Versions reflect the *documented* Broad SpliceAI Lookup backend configuration
(GENCODE v44 basic; modified SpliceAI; refactored Pangolin) and Ensembl VEP for
resolution. The upstream does not assert versions per call, so these are
best-known constants, not per-response assertions.
"""

from __future__ import annotations

from typing import Any

from spliceailookup_link.config import GenomeBuild, settings

_NOTE = (
    "Versions reflect the documented Broad SpliceAI Lookup backend configuration; "
    "the upstream does not assert versions per call. Computational prediction -- "
    "interpret alongside orthogonal evidence."
)

_SPLICEAI = "SpliceAI (Illumina; Jaganathan et al. 2019) via Broad SpliceAI Lookup"
_PANGOLIN = "Pangolin (Zeng & Li 2022) via Broad SpliceAI Lookup"
_SAI10K = "SpliceAI-10k consequence calculator"
_RESOLVER = "Ensembl VEP REST"


def _transcript_annotation(build: GenomeBuild) -> str:
    v = settings.GENCODE_VERSION
    if build == "GRCh37":
        return f"GENCODE {v}lift37 basic (GRCh37 liftover)"
    return f"GENCODE {v} basic (GRCh38)"


def prediction_provenance(build: GenomeBuild) -> dict[str, Any]:
    """Provenance block for a prediction payload (single source for capabilities too)."""
    return {
        "spliceai": _SPLICEAI,
        "pangolin": _PANGOLIN,
        "consequence": _SAI10K,
        "transcript_annotation": _transcript_annotation(build),
        "mane": "MANE Select prioritised when transcripts='mane' (default)",
        "resolver": _RESOLVER,
        "note": _NOTE,
    }


def data_sources() -> dict[str, Any]:
    """Versioned data_sources for the capabilities document (build-agnostic view)."""
    v = settings.GENCODE_VERSION
    return {
        "spliceai": _SPLICEAI,
        "pangolin": _PANGOLIN,
        "sai10k": _SAI10K,
        "transcript_annotation": f"GENCODE {v} basic (GRCh38; {v}lift37 for GRCh37)",
        "resolver": _RESOLVER,
    }
