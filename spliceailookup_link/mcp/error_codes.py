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
