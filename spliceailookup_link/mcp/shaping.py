"""Shape raw SpliceAI / Pangolin payloads into compact, LLM-friendly results.

The upstream uses cryptic keys (DS_AG, DP_DL, ...). This module renames them to
readable splice-site classes, filters transcripts (MANE vs all), trims heavy
fields (exon arrays, REF/ALT raw scores) in compact mode, and builds a one-line
headline. response_mode: minimal (headline + top score), compact (per-transcript
deltas, default), full (adds REF/ALT raw scores + exon model + allNonZeroScores).
"""

from __future__ import annotations

import re
from typing import Any, Literal

ResponseMode = Literal["minimal", "compact", "full"]
Transcripts = Literal["mane", "all"]

_STRONG = 0.5
_MODERATE = 0.2

THRESHOLD_BASIS = "Δ>=0.5 high; 0.2-0.5 moderate; >0-0.2 low; 0 none (SpliceAI/Pangolin convention)"


def band(score: float | None) -> str:
    """Public four-value interpretation band for a single max delta score."""
    if score is None:
        return "none"
    if score >= _STRONG:
        return "high"
    if score >= _MODERATE:
        return "moderate"
    if score > 0:
        return "low"
    return "none"


def _minimal_single_model(result: dict[str, Any]) -> dict[str, Any]:
    transcripts = result.get("transcripts") or []
    top = transcripts[0] if transcripts else {}
    best_class, best, pos = None, None, None
    for name, d in (top.get("delta_scores") or {}).items():
        s = (d or {}).get("score")
        if s is not None and (best is None or s > best):
            best, best_class, pos = s, name, (d or {}).get("position")
    out: dict[str, Any] = {
        "model": result["model"],
        "variant_id": result["variant_id"],
        "genome_build": result["genome_build"],
        "gene": top.get("gene"),
        "max_delta_score": result.get("max_delta_score"),
        "interpretation": {"band": band(result.get("max_delta_score"))},
        "headline": result["headline"],
    }
    if best_class is not None:
        out["top"] = {"class": best_class, "score": best, "position": pos}
    cons = result.get("consequence") or {}
    aberr = (cons.get("aberrations") or [{}])[0].get("type") if cons else None
    if aberr:
        out["consequence_summary"] = aberr
    return out


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


_ENSEMBL_VERSIONED_RE = re.compile(r"^(ENS[A-Z]+\d+\.\d+)_\d+$")


def _normalize_ensembl_id(value: Any) -> Any:
    """Strip the GRCh37 GENCODE re-version suffix (ENSG...13_12 -> ENSG...13).

    Leaves clean GRCh38 ids and any non-matching value untouched so cross-build
    joins line up. Returns the input unchanged for non-string / non-matching values.
    """
    if not isinstance(value, str):
        return value
    m = _ENSEMBL_VERSIONED_RE.match(value)
    return m.group(1) if m else value


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


def _score_signature(t: dict[str, Any]) -> tuple[Any, ...]:
    ds = t.get("delta_scores") or {}
    return (
        t.get("max_delta_score"),
        tuple(sorted((k, v.get("score"), v.get("position")) for k, v in ds.items())),
    )


def _collapse_identical_transcripts(shaped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge transcript blocks with identical delta scores into one + shared_by."""
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for t in shaped:
        sig = _score_signature(t)
        rep = groups.get(sig)
        if rep is None:
            groups[sig] = dict(t)
            order.append(sig)
            continue
        tid = t.get("transcript_id")
        if tid and tid != rep.get("transcript_id"):
            rep.setdefault("shared_by", []).append(tid)
    out = [groups[s] for s in order]
    for rep in out:
        if "shared_by" in rep:
            rep["shared_by"] = sorted(set(rep["shared_by"]))
    return out


def _apply_max_transcripts(
    shaped: list[dict[str, Any]], max_transcripts: int | None
) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    if max_transcripts is None or len(shaped) <= max_transcripts:
        return shaped, None
    ranked = sorted(shaped, key=lambda t: t.get("max_delta_score") or -1.0, reverse=True)
    return ranked[:max_transcripts], {"kept": max_transcripts, "total": len(shaped)}


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
    raw_gid = raw.get("g_id")
    raw_tid = raw.get("t_id")
    gene_id = _normalize_ensembl_id(raw_gid)
    transcript_id = _normalize_ensembl_id(raw_tid)
    out: dict[str, Any] = {
        "gene": raw.get("g_name"),
        "gene_id": gene_id,
        "transcript_id": transcript_id,
        "transcript_priority": _priority_label(raw.get("t_priority")),
        "refseq_ids": raw.get("t_refseq_ids"),
        "strand": raw.get("t_strand"),
        "transcript_type": raw.get("t_type"),
        "delta_scores": deltas,
        "max_delta_score": max_score,
    }
    if mode == "full" and (gene_id != raw_gid or transcript_id != raw_tid):
        out["gencode_id"] = {"gene_id": raw_gid, "transcript_id": raw_tid}
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
        starts = raw.get("EXON_STARTS")
        ends = raw.get("EXON_ENDS")
        out["exon_model"] = {
            "tx_start": min(starts) if starts else None,
            "tx_end": max(ends) if ends else None,
            "exon_starts": starts,
            "exon_ends": ends,
            "cds_start": raw.get("CDS_START"),
            "cds_end": raw.get("CDS_END"),
        }
        if raw.get("SCORES_FOR_INSERTED_BASES"):
            out["scores_for_inserted_bases"] = raw["SCORES_FOR_INSERTED_BASES"]
    return out


def _tx_bounds(scores: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    """Genomic transcript bounds from the MANE/top scored transcript's exons."""
    if not scores:
        return None, None
    top = next((s for s in scores if str(s.get("t_priority")) in ("MS", "MP")), scores[0])
    starts = top.get("EXON_STARTS")
    ends = top.get("EXON_ENDS")
    return (min(starts) if starts else None, max(ends) if ends else None)


def _shape_consequence(
    payload: dict[str, Any], mode: ResponseMode, max_score: float | None = None
) -> dict[str, Any] | None:
    sai = payload.get("sai10kPredictions")
    err = payload.get("sai10kPredictionsError")
    if not sai and not err:
        return None
    out: dict[str, Any] = {}
    if err:
        out["error"] = err
    raw_aberr = (sai or {}).get("aberrations") if isinstance(sai, dict) else None
    # `aberrations` is the STABLE path in every mode (possibly empty under mask=1).
    out["aberrations"] = [
        {
            k: v
            for k, v in {
                "type": ab.get("aberration_type"),
                "affected_region": ab.get("affected_region"),
                "status": ab.get("status"),
                "size_is_coding": ab.get("size_is_coding"),
                "introduces_stop_codon": ab.get("introduces_stop_codon"),
            }.items()
            if v is not None
        }
        for ab in (raw_aberr or [])
    ]
    if mode == "full" and isinstance(sai, dict):
        if sai.get("transcript_info") is not None:
            ti = dict(sai["transcript_info"])
            # D5: upstream leaves tx_start/tx_end null; derive them from the
            # scored transcript's exon arrays (never overwrite a non-null value).
            if ti.get("tx_start") is None or ti.get("tx_end") is None:
                tx_start, tx_end = _tx_bounds(payload.get("scores") or [])
                if ti.get("tx_start") is None and tx_start is not None:
                    ti["tx_start"] = tx_start
                if ti.get("tx_end") is None and tx_end is not None:
                    ti["tx_end"] = tx_end
            out["transcript_info"] = ti
        extras = {k: v for k, v in sai.items() if k not in {"aberrations", "transcript_info"}}
        if extras:
            out["raw_extras"] = extras
    masked = str(payload.get("mask")) in ("1", "True", "true")
    if masked and not out["aberrations"] and (max_score or 0.0) >= _MODERATE:
        out["note"] = (
            "mask='masked' computes aberrations on masked scores and can suppress an "
            "aberration that mask='raw' would predict; this site has a non-trivial "
            "delta (>=0.2) but no masked aberration -- re-run with mask='raw' to check."
        )
    return out


def shape_spliceai(
    payload: dict[str, Any],
    *,
    transcripts: Transcripts = "mane",
    response_mode: ResponseMode = "compact",
    include_consequence: bool = True,
    max_transcripts: int | None = None,
) -> dict[str, Any]:
    raw_scores = payload.get("scores") or []
    selected = _select_transcripts(raw_scores, transcripts)
    shaped = [_shape_spliceai_transcript(s, response_mode) for s in selected]
    shaped = _collapse_identical_transcripts(shaped)
    shaped, truncated = _apply_max_transcripts(shaped, max_transcripts)
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
    result["interpretation"] = {"band": band(max_overall), "threshold_basis": THRESHOLD_BASIS}
    if truncated is not None:
        result["transcripts_truncated"] = truncated
    if include_consequence:
        consequence = _shape_consequence(payload, response_mode, max_overall)
        if consequence:
            result["consequence"] = consequence
    result["headline"] = spliceai_headline(result)
    if response_mode == "minimal":
        return _minimal_single_model(result)
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
    raw_gid = raw.get("g_id")
    raw_tid = raw.get("t_id")
    gene_id = _normalize_ensembl_id(raw_gid)
    transcript_id = _normalize_ensembl_id(raw_tid)
    out: dict[str, Any] = {
        "gene": raw.get("g_name"),
        "gene_id": gene_id,
        "transcript_id": transcript_id,
        "transcript_priority": _priority_label(raw.get("t_priority")),
        "refseq_ids": raw.get("t_refseq_ids"),
        "strand": raw.get("t_strand"),
        "delta_scores": deltas,
        "max_delta_score": max(abs_scores) if abs_scores else None,
    }
    if mode == "full" and (gene_id != raw_gid or transcript_id != raw_tid):
        out["gencode_id"] = {"gene_id": raw_gid, "transcript_id": raw_tid}
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
    max_transcripts: int | None = None,
) -> dict[str, Any]:
    raw_scores = payload.get("scores") or []
    selected = _select_transcripts(raw_scores, transcripts)
    shaped = [_shape_pangolin_transcript(s, response_mode) for s in selected]
    shaped = _collapse_identical_transcripts(shaped)
    shaped, truncated = _apply_max_transcripts(shaped, max_transcripts)
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
        "transcripts": shaped,
    }
    result["interpretation"] = {"band": band(max_overall), "threshold_basis": THRESHOLD_BASIS}
    if truncated is not None:
        result["transcripts_truncated"] = truncated
    if response_mode == "full" and payload.get("allNonZeroScores"):
        result["all_non_zero_scores"] = {
            "transcript_id": payload.get("allNonZeroScoresTranscriptId"),
            "strand": payload.get("allNonZeroScoresStrand"),
            "scores": payload.get("allNonZeroScores"),
        }
    result["headline"] = pangolin_headline(result)
    if response_mode == "minimal":
        return _minimal_single_model(result)
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
