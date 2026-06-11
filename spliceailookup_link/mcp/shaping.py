"""Shape raw SpliceAI / Pangolin payloads into compact, LLM-friendly results.

The upstream uses cryptic keys (DS_AG, DP_DL, ...). This module renames them to
readable splice-site classes, filters transcripts (MANE vs all), trims heavy
fields (exon arrays, REF/ALT raw scores) in compact mode, and builds a one-line
headline. response_mode: minimal (headline + top score), compact (per-transcript
deltas, default), full (adds REF/ALT raw scores + exon model + allNonZeroScores).
"""

from __future__ import annotations

from typing import Any, Literal

ResponseMode = Literal["minimal", "compact", "full"]
Transcripts = Literal["mane", "all"]

_STRONG = 0.5
_MODERATE = 0.2

_PRIORITY_LABELS = {
    "MS": "MANE Select",
    "MP": "MANE Plus Clinical",
    "C": "Ensembl Canonical",
    "N": "non-canonical",
}


def _to_float(value: Any) -> float | None:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _priority_label(priority: Any) -> str:
    return _PRIORITY_LABELS.get(str(priority), str(priority) if priority else "unknown")


def _delta(score: Any, pos: Any) -> dict[str, Any]:
    return {"score": _to_float(score), "position": _to_int(pos)}


def _strength(score: float | None) -> str:
    if score is None:
        return "none"
    if score >= _STRONG:
        return "strong"
    if score >= _MODERATE:
        return "moderate"
    if score > 0:
        return "weak"
    return "none"


def _select_transcripts(
    scores: list[dict[str, Any]], transcripts: Transcripts
) -> list[dict[str, Any]]:
    """Filter to MANE Select when requested; fall back to all if no MANE present."""
    if transcripts == "all":
        return scores
    mane = [s for s in scores if str(s.get("t_priority")) in ("MS", "MP")]
    return mane or scores


# ---------------- SpliceAI ----------------

_SPLICEAI_CLASSES = (
    ("acceptor_gain", "DS_AG", "DP_AG"),
    ("acceptor_loss", "DS_AL", "DP_AL"),
    ("donor_gain", "DS_DG", "DP_DG"),
    ("donor_loss", "DS_DL", "DP_DL"),
)


def _shape_spliceai_transcript(raw: dict[str, Any], mode: ResponseMode) -> dict[str, Any]:
    deltas = {name: _delta(raw.get(ds), raw.get(dp)) for name, ds, dp in _SPLICEAI_CLASSES}
    scores = [d["score"] for d in deltas.values() if d["score"] is not None]
    max_score = max(scores) if scores else None
    out: dict[str, Any] = {
        "gene": raw.get("g_name"),
        "gene_id": raw.get("g_id"),
        "transcript_id": raw.get("t_id"),
        "transcript_priority": _priority_label(raw.get("t_priority")),
        "refseq_ids": raw.get("t_refseq_ids"),
        "strand": raw.get("t_strand"),
        "transcript_type": raw.get("t_type"),
        "delta_scores": deltas,
        "max_delta_score": max_score,
    }
    if mode == "full":
        out["ref_alt_scores"] = {
            "acceptor_gain": {
                "ref": _to_float(raw.get("DS_AG_REF")),
                "alt": _to_float(raw.get("DS_AG_ALT")),
            },
            "acceptor_loss": {
                "ref": _to_float(raw.get("DS_AL_REF")),
                "alt": _to_float(raw.get("DS_AL_ALT")),
            },
            "donor_gain": {
                "ref": _to_float(raw.get("DS_DG_REF")),
                "alt": _to_float(raw.get("DS_DG_ALT")),
            },
            "donor_loss": {
                "ref": _to_float(raw.get("DS_DL_REF")),
                "alt": _to_float(raw.get("DS_DL_ALT")),
            },
        }
        out["exon_model"] = {
            "exon_starts": raw.get("EXON_STARTS"),
            "exon_ends": raw.get("EXON_ENDS"),
            "cds_start": raw.get("CDS_START"),
            "cds_end": raw.get("CDS_END"),
        }
        if raw.get("SCORES_FOR_INSERTED_BASES"):
            out["scores_for_inserted_bases"] = raw["SCORES_FOR_INSERTED_BASES"]
    return out


def _shape_consequence(payload: dict[str, Any]) -> dict[str, Any] | None:
    sai = payload.get("sai10kPredictions")
    err = payload.get("sai10kPredictionsError")
    if not sai and not err:
        return None
    out: dict[str, Any] = {}
    if err:
        out["error"] = err
    aberrations = (sai or {}).get("aberrations") if isinstance(sai, dict) else None
    if aberrations:
        out["aberrations"] = [
            {
                "type": ab.get("aberration_type"),
                "affected_region": ab.get("affected_region"),
                "status": ab.get("status"),
                "size_is_coding": ab.get("size_is_coding"),
                "introduces_stop_codon": ab.get("introduces_stop_codon"),
            }
            for ab in aberrations
        ]
    elif isinstance(sai, dict):
        out["raw"] = sai
    return out or None


def shape_spliceai(
    payload: dict[str, Any],
    *,
    transcripts: Transcripts = "mane",
    response_mode: ResponseMode = "compact",
    include_consequence: bool = True,
) -> dict[str, Any]:
    raw_scores = payload.get("scores") or []
    selected = _select_transcripts(raw_scores, transcripts)
    shaped = [_shape_spliceai_transcript(s, response_mode) for s in selected]
    max_overall = max(
        (t["max_delta_score"] for t in shaped if t["max_delta_score"] is not None),
        default=None,
    )
    result: dict[str, Any] = {
        "model": "SpliceAI",
        "variant_id": payload.get("variant"),
        "genome_build": "GRCh38" if str(payload.get("hg")) == "38" else "GRCh37",
        "gene_set": payload.get("bc", "basic"),
        "max_distance": _to_int(payload.get("distance")),
        "mask": "masked" if str(payload.get("mask")) in ("1", "True", "true") else "raw",
        "max_delta_score": max_overall,
        "transcripts": shaped,
    }
    if response_mode == "minimal":
        result["transcripts"] = shaped[:1]
    if include_consequence:
        consequence = _shape_consequence(payload)
        if consequence:
            result["consequence"] = consequence
    result["headline"] = spliceai_headline(result)
    return result


def spliceai_headline(shaped: dict[str, Any]) -> str:
    transcripts = shaped.get("transcripts") or []
    build = shaped.get("genome_build", "")
    if not transcripts:
        return f"SpliceAI ({build}): no transcript scores for {shaped.get('variant_id')}."
    top = transcripts[0]
    gene = top.get("gene") or "unknown gene"
    best_class, best = None, -1.0
    for name, d in (top.get("delta_scores") or {}).items():
        s = d.get("score")
        if s is not None and s > best:
            best, best_class = s, name
    if best_class is None:
        return f"SpliceAI ({build}): {gene} — no non-zero delta scores."
    pos = (top["delta_scores"][best_class] or {}).get("position")
    label = best_class.replace("_", " ")
    consequence = shaped.get("consequence") or {}
    aberr = (consequence.get("aberrations") or [{}])[0].get("type") if consequence else None
    tail = f"; predicted {aberr.replace('_', ' ')}" if aberr else ""
    return (
        f"SpliceAI ({build}): {gene} — {_strength(best)} {label} "
        f"(Δ={best:.2f} at {pos:+d} bp){tail}."
    )


# ---------------- Pangolin ----------------

_PANGOLIN_CLASSES = (
    ("splice_gain", "DS_SG", "DP_SG"),
    ("splice_loss", "DS_SL", "DP_SL"),
)


def _shape_pangolin_transcript(raw: dict[str, Any], mode: ResponseMode) -> dict[str, Any]:
    deltas: dict[str, Any] = {}
    abs_scores: list[float] = []
    for name, ds, dp in _PANGOLIN_CLASSES:
        raw_score = _to_float(raw.get(ds))
        entry: dict[str, Any] = {
            "score": abs(raw_score) if raw_score is not None else None,
            "position": _to_int(raw.get(dp)),
        }
        # Pangolin reports loss as a negative magnitude; keep the signed value too.
        if raw_score is not None and raw_score < 0:
            entry["signed_score"] = raw_score
        deltas[name] = entry
        if entry["score"] is not None:
            abs_scores.append(entry["score"])
    out: dict[str, Any] = {
        "gene": raw.get("g_name"),
        "gene_id": raw.get("g_id"),
        "transcript_id": raw.get("t_id"),
        "transcript_priority": _priority_label(raw.get("t_priority")),
        "refseq_ids": raw.get("t_refseq_ids"),
        "strand": raw.get("t_strand"),
        "delta_scores": deltas,
        "max_delta_score": max(abs_scores) if abs_scores else None,
    }
    if mode == "full":
        out["ref_alt_scores"] = {
            "splice_gain": {
                "ref": _to_float(raw.get("SG_REF")),
                "alt": _to_float(raw.get("SG_ALT")),
            },
            "splice_loss": {
                "ref": _to_float(raw.get("SL_REF")),
                "alt": _to_float(raw.get("SL_ALT")),
            },
        }
    return out


def shape_pangolin(
    payload: dict[str, Any],
    *,
    transcripts: Transcripts = "mane",
    response_mode: ResponseMode = "compact",
) -> dict[str, Any]:
    raw_scores = payload.get("scores") or []
    selected = _select_transcripts(raw_scores, transcripts)
    shaped = [_shape_pangolin_transcript(s, response_mode) for s in selected]
    max_overall = max(
        (t["max_delta_score"] for t in shaped if t["max_delta_score"] is not None),
        default=None,
    )
    result: dict[str, Any] = {
        "model": "Pangolin",
        "variant_id": payload.get("variant"),
        "genome_build": "GRCh38" if str(payload.get("hg")) == "38" else "GRCh37",
        "gene_set": payload.get("bc", "basic"),
        "max_distance": _to_int(payload.get("distance")),
        "mask": "masked" if str(payload.get("mask")) in ("1", "True", "true") else "raw",
        "max_delta_score": max_overall,
        "transcripts": shaped[:1] if response_mode == "minimal" else shaped,
    }
    if response_mode == "full" and payload.get("allNonZeroScores"):
        result["all_non_zero_scores"] = {
            "transcript_id": payload.get("allNonZeroScoresTranscriptId"),
            "strand": payload.get("allNonZeroScoresStrand"),
            "scores": payload.get("allNonZeroScores"),
        }
    result["headline"] = pangolin_headline(result)
    return result


def pangolin_headline(shaped: dict[str, Any]) -> str:
    transcripts = shaped.get("transcripts") or []
    build = shaped.get("genome_build", "")
    if not transcripts:
        return f"Pangolin ({build}): no transcript scores for {shaped.get('variant_id')}."
    top = transcripts[0]
    gene = top.get("gene") or "unknown gene"
    best_class, best = None, -1.0
    for name, d in (top.get("delta_scores") or {}).items():
        s = d.get("score")
        if s is not None and s > best:
            best, best_class = s, name
    if best_class is None:
        return f"Pangolin ({build}): {gene} — no non-zero delta scores."
    pos = (top["delta_scores"][best_class] or {}).get("position")
    label = best_class.replace("_", " ")
    return f"Pangolin ({build}): {gene} — {_strength(best)} {label} (Δ={best:.2f} at {pos:+d} bp)."
