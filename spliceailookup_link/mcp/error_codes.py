"""Response-Envelope Standard v1: the closed wire ``error_code`` enum + subtype map.

Split out of ``mcp/errors.py`` to keep that module under the fleet's 600-LOC budget
(AGENTS.md "File Size Discipline"). The wire ``error_code`` is a CLOSED six-value enum.
The server keeps a finer, actionable classification (``build_mismatch``,
``ref_mismatch``, ...) as ``error_subtype`` for richer clients + recovery-text
selection, but every subtype maps onto exactly one canonical code -- the enum is never
widened. A client that branches on ``error_code`` sees only the six; a client that wants
the detail reads ``error_subtype``.
"""

from __future__ import annotations

from typing import Any

#: The closed wire enum, harmonised across the GeneFoundry fleet. Anything outside this
#: set is a Response-Envelope Standard v1 violation, however sensible it reads.
CANONICAL_ERROR_CODES = frozenset(
    {
        "invalid_input",
        "not_found",
        "ambiguous_query",
        "upstream_unavailable",
        "rate_limited",
        "internal",
    }
)

#: Fine-grained internal subtype -> canonical wire code. The runtime never emits a key
#: from the left column on the wire; it emits the mapped value and carries the key under
#: ``error_subtype``. Every value MUST be a member of :data:`CANONICAL_ERROR_CODES`.
_SUBTYPE_TO_CANONICAL = {
    "invalid_input": "invalid_input",
    "build_mismatch": "invalid_input",
    "ref_mismatch": "invalid_input",
    "unsupported_contig": "invalid_input",
    "validation_failed": "invalid_input",
    "not_found": "not_found",
    "ambiguous": "ambiguous_query",
    "rate_limited": "rate_limited",
    "upstream_unavailable": "upstream_unavailable",
    "upstream_policy": "upstream_unavailable",
    "internal_error": "internal",
}


def canonical_error_code(subtype: str) -> str:
    """Map a fine-grained internal subtype onto the closed six-value wire enum."""
    return _SUBTYPE_TO_CANONICAL.get(subtype, "internal")


def normalize_error_code(payload: dict[str, Any]) -> dict[str, Any]:
    """Guarantee ``payload['error_code']`` is in the closed enum at the wire EGRESS.

    Every error envelope leaves through ``error_tool_result``; this is the single point
    that normalises ANY off-enum ``error_code`` onto the canonical six -- including one on
    a RAW ``McpToolError`` whose hand-built payload bypassed the classification
    constructors -- preserving the original additively under ``error_subtype``. A no-op for
    an already-canonical code (never invents a spurious ``error_subtype``). Returns the same
    dict object when nothing needs changing; a shallow copy otherwise, so the caller's
    payload is never mutated.
    """
    code = payload.get("error_code")
    if not isinstance(code, str) or code in CANONICAL_ERROR_CODES:
        return payload
    out = dict(payload)
    out["error_code"] = canonical_error_code(code)
    out.setdefault("error_subtype", code)
    return out


# Recovery guidance keyed by fine-grained subtype (moved here for errors.py's LOC budget).
def recovery_action(error_code: str, retryable: bool) -> str:
    if retryable:
        return "retry_backoff"
    if error_code in {"invalid_input", "validation_failed", "ref_mismatch", "ambiguous"}:
        return "reformulate_input"
    if error_code == "build_mismatch":
        return "switch_tool"
    return "switch_tool"


def recovery_text(error_code: str, fallback_tool: str | None, *, tool_name: str) -> str:
    if error_code == "not_found":
        return (
            "The variant is well-formed but the model returned no scores -- it likely does not "
            "overlap a transcript in the chosen gene_set. Try gene_set='comprehensive', widen "
            "max_distance, or confirm the coordinates/build with resolve_variant."
        )
    if error_code == "invalid_input":
        if tool_name == "resolve_variant":
            # Already inside the resolver: do not tell the caller to call it again.
            return (
                "The input could not be parsed into any supported variant form. Do not "
                "retry unchanged. Provide CHROM-POS-REF-ALT (chr optional), transcript or "
                "genomic HGVS (e.g. NM_000123.4:c.10A>T), or an rsID (e.g. rs6025); call "
                "get_server_capabilities for accepted formats and examples."
            )
        return (
            "The variant could not be parsed or the upstream rejected it. Do not retry "
            "unchanged. Call resolve_variant to normalize HGVS / rsIDs / loose coordinates into "
            "CHROM-POS-REF-ALT, then retry the prediction."
        )
    if error_code == "build_mismatch":
        return (
            "The coordinate looks like the other genome build. Re-call with the corrected "
            "genome_build (the fallback arguments already carry the inferred build)."
        )
    if error_code == "rate_limited":
        return (
            "Upstream is interactive-use-only and rate-limited, or local concurrency is "
            "saturated. Back off (a few seconds) and retry; reduce concurrent calls. Cached "
            "results do not count against the limit."
        )
    if error_code == "upstream_unavailable":
        return (
            "The scoring service failed transiently, or the call exceeded the server's "
            "soft deadline (comprehensive gene_set and/or a large max_distance are slow "
            "and may 503 upstream). Retry with backoff using gene_set='basic' or a smaller "
            "max_distance."
        )
    if error_code == "upstream_policy":
        return (
            "The upstream answered outside this server's HTTP policy (a redirect to a "
            "non-allowlisted or non-https URL, too many redirects, or an oversized body). "
            "This is deterministic: do not retry immediately; try again later or report it."
        )
    if error_code == "validation_failed":
        return "Inputs failed validation. Check the tool schema and call get_server_capabilities."
    if error_code == "ref_mismatch":
        return (
            "The REF allele does not match the genome reference at this position "
            "(likely a swapped REF/ALT, the opposite strand, or the wrong build). "
            "Fix the REF allele, or pass an HGVS/rsID to resolve_variant to get "
            "canonical CHROM-POS-REF-ALT, then retry."
        )
    if error_code == "ambiguous":
        return (
            "This input maps to more than one ALT allele at the locus. Pick one "
            "variant_id (see variant_ids / next_commands, one prediction per allele) "
            "and retry, or call resolve_variant to review the candidates."
        )
    if error_code == "unsupported_contig":
        return (
            "This contig is outside the SpliceAI/Pangolin nuclear scope (chr1-22, X, Y). "
            "Do not retry unchanged. For mitochondrial variants, use gnomad-link "
            "get_mitochondrial_variant; otherwise confirm the variant is on a nuclear "
            "chromosome and re-submit."
        )
    return f"Unexpected failure. Call {fallback_tool} for a safe entry point."
