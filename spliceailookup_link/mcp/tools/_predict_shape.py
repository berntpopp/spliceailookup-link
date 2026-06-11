"""Presentation helpers for predict_splicing.

Single source of truth for model agreement: `assess_agreement` computes the
verdict, and `combined_headline` renders that verdict verbatim -- the headline
never recomputes agreement from raw scores (the F6 bug). `assess_agreement`
distinguishes a moderate concordance band (F6b) so two both-moderate models read
as agreeing, not "discordant".
"""

from __future__ import annotations

from typing import Any

from spliceailookup_link.mcp.shaping import THRESHOLD_BASIS, band

_HIGH = 0.5
_LOW = 0.2

_VERDICT_CLAUSE = {
    "concordant_high": "models agree (both strong)",
    "concordant_moderate": "models agree (both moderate)",
    "concordant_low": "models agree (both low/none)",
    "discordant": "models disagree",
}
_INCOMPLETE_CLAUSE = "only one model scored"


def assess_agreement(sai_max: float | None, pang_max: float | None) -> dict[str, Any]:
    """Summarise whether the two independent models agree on impact magnitude."""
    if sai_max is None or pang_max is None:
        return {"verdict": "incomplete", "detail": "one model returned no score"}
    both_high = sai_max >= _HIGH and pang_max >= _HIGH
    both_low = sai_max < _LOW and pang_max < _LOW
    both_moderate = (_LOW <= sai_max < _HIGH) and (_LOW <= pang_max < _HIGH)
    if both_high:
        verdict, detail = "concordant_high", "both models predict a strong splicing effect"
    elif both_low:
        verdict, detail = "concordant_low", "both models predict little or no splicing effect"
    elif both_moderate:
        verdict, detail = "concordant_moderate", "both models predict a moderate splicing effect"
    else:
        verdict, detail = "discordant", "models disagree on the magnitude; interpret with caution"
    return {
        "verdict": verdict,
        "detail": detail,
        "spliceai_max_delta": sai_max,
        "pangolin_max_delta": pang_max,
    }


def combined_headline(
    gene: str | None,
    build: str,
    sai_max: float | None,
    pang_max: float | None,
    consequence: dict[str, Any] | None,
    agreement: dict[str, Any],
) -> str:
    """Render a one-line headline whose agreement clause is the verdict verbatim."""
    gene_label = gene or "variant"
    parts: list[str] = []
    if sai_max is not None:
        parts.append(f"SpliceAI Δ={sai_max:.2f}")
    if pang_max is not None:
        parts.append(f"Pangolin Δ={pang_max:.2f}")
    scores = "; ".join(parts) if parts else "no scores"
    aberr = None
    if consequence and consequence.get("aberrations"):
        aberr = (consequence["aberrations"][0] or {}).get("type")
    tail = f"; predicted {aberr.replace('_', ' ')}" if aberr else ""
    if sai_max is not None and pang_max is not None:
        verdict_part = f"; {_VERDICT_CLAUSE.get(agreement.get('verdict'), '')}"
    elif (sai_max is None) != (pang_max is None):
        verdict_part = f"; {_INCOMPLETE_CLAUSE}"
    else:
        verdict_part = ""
    return f"{gene_label} ({build}): {scores}{verdict_part}{tail}."


def combined_interpretation(sai_max: float | None, pang_max: float | None) -> dict[str, Any]:
    """Top-level band for the combined result: the stronger of the two models."""
    scores = [s for s in (sai_max, pang_max) if s is not None]
    top = max(scores) if scores else None
    return {"band": band(top), "threshold_basis": THRESHOLD_BASIS}
