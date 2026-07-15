"""Response-Envelope Standard v1: the wire ``error_code`` is a CLOSED six-value enum.

The fleet's Behaviour Conformance gate rejects any ``error_code`` outside this set,
however sensible it reads. Before this contract the server shipped finer codes on the
wire (``validation_failed``, ``ref_mismatch``, ``ambiguous``, ``build_mismatch``,
``unsupported_contig``, ``internal_error``) -- every one of which made a live client
branching on ``error_code`` see a code its own vocabulary did not contain.

This guard pins the invariant at the CLASS level: every classification path, and every
place the taxonomy is documented, stays inside the closed six. The finer classification
survives -- additively -- under ``error_subtype`` for clients that want it, but it never
widens the wire enum. Written to FAIL against the pre-canonicalisation code.
"""

from __future__ import annotations

import pytest

from spliceailookup_link.api import (
    DataNotFoundError,
    RateLimitedError,
    SpliceApiError,
    UpstreamInputError,
)
from spliceailookup_link.mcp.errors import (
    AmbiguousVariantError,
    BuildMismatchError,
    CoordinateRangeError,
    McpErrorContext,
    RefMismatchError,
    mcp_tool_error,
    mcp_validation_tool_error,
)
from spliceailookup_link.mcp.resources import (
    get_capabilities_resource,
    get_reference_resource,
)
from spliceailookup_link.variant import UnsupportedContigError, VariantParseError

# Independent of the implementation on purpose: the test defines the contract, not the
# code under test. Response-Envelope Standard v1, harmonised across the fleet.
CLOSED_SIX = {
    "invalid_input",
    "not_found",
    "ambiguous_query",
    "upstream_unavailable",
    "rate_limited",
    "internal",
}


def _payload(exc: BaseException, **ctx):
    ctx.setdefault("tool_name", "predict_splicing")
    return mcp_tool_error(exc, McpErrorContext(**ctx)).payload


# (exception, expected canonical error_code, expected error_subtype-or-None)
_CASES = [
    (DataNotFoundError("no scores"), "not_found", None),
    (VariantParseError("bad"), "invalid_input", None),
    (UpstreamInputError("parse"), "invalid_input", None),
    (RateLimitedError("429"), "rate_limited", None),
    (SpliceApiError("503"), "upstream_unavailable", None),
    (TimeoutError("slow"), "upstream_unavailable", None),
    (KeyError("boom"), "internal", "internal_error"),
    (ValueError("nope"), "invalid_input", "validation_failed"),
    (UnsupportedContigError("MT is not scoreable"), "invalid_input", "unsupported_contig"),
    (
        BuildMismatchError(
            variant_id="8-145500000-A-T", inferred_build="GRCh37", requested_build="GRCh38"
        ),
        "invalid_input",
        "build_mismatch",
    ),
    (
        AmbiguousVariantError(variant="rs6025", candidates=["1-1-C-A", "1-1-C-T"]),
        "ambiguous_query",
        "ambiguous",
    ),
    (
        RefMismatchError(
            variant_id="8-140300616-A-G",
            observed_ref="A",
            reference_base="T",
            build="GRCh38",
            chrom="8",
            pos=140300616,
            alt="G",
        ),
        "invalid_input",
        "ref_mismatch",
    ),
    (
        CoordinateRangeError(chrom="1", pos=260000000, grch38_len=248956422, grch37_len=249250621),
        "invalid_input",
        None,
    ),
]


@pytest.mark.parametrize("exc, expected_code, expected_subtype", _CASES)
def test_wire_error_code_is_in_closed_six(exc, expected_code, expected_subtype) -> None:
    payload = _payload(exc, variant="8-140300616-A-G", genome_build="GRCh38")
    code = payload["error_code"]
    assert code in CLOSED_SIX, f"{type(exc).__name__} produced off-enum error_code {code!r}"
    assert code == expected_code
    # The finer classification survives additively -- never on the wire enum.
    if expected_subtype is None:
        assert payload.get("error_subtype") in (None, expected_code)
    else:
        assert payload.get("error_subtype") == expected_subtype
        assert expected_subtype not in CLOSED_SIX  # a subtype is NEVER a wire code


def test_pydantic_validation_maps_to_invalid_input_not_validation_failed() -> None:
    # A missing/unknown argument is the single most-probed error in the behaviour gate.
    # It MUST be invalid_input (a client that gets not_found/validation_failed for a bad
    # arg cannot act on it), with the finer detail carried under error_subtype.
    from pydantic import BaseModel, ValidationError

    class _M(BaseModel):  # minimal model to mint a real pydantic ValidationError
        x: int

    try:
        _M(x="not-an-int")
    except ValidationError as exc:
        payload = mcp_validation_tool_error(tool_name="predict_spliceai", exc=exc).payload
    assert payload["error_code"] == "invalid_input"
    assert payload["error_code"] in CLOSED_SIX
    assert payload.get("error_subtype") == "validation_failed"


def test_documented_taxonomy_never_advertises_an_off_enum_wire_code() -> None:
    # Discovery/capabilities must not describe a wire code the runtime no longer emits
    # (the "docs out of sync with the tool" trap). The canonical list is exactly the six;
    # finer subtypes live in a clearly separate section, never in the wire-code list.
    caps_codes = set(get_capabilities_resource()["error_codes"])
    assert caps_codes <= CLOSED_SIX, (
        f"capabilities advertises off-enum codes: {caps_codes - CLOSED_SIX}"
    )

    ref = get_reference_resource()["error_taxonomy"]
    ref_codes = set(ref["codes"])
    assert ref_codes <= CLOSED_SIX, f"reference advertises off-enum codes: {ref_codes - CLOSED_SIX}"
    # The finer subtypes are documented, but as SUBTYPES, never as wire codes.
    subtypes = set(ref.get("error_subtypes", {}))
    assert {"ref_mismatch", "build_mismatch", "unsupported_contig"} <= subtypes
    assert subtypes.isdisjoint(CLOSED_SIX)
    for detail in ref["error_subtypes"].values():
        assert detail["error_code"] in CLOSED_SIX
