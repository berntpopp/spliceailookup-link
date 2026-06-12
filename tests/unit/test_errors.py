"""Tests for the structured error envelope + classification."""

from __future__ import annotations

from spliceailookup_link.api import (
    DataNotFoundError,
    RateLimitedError,
    SpliceApiError,
    UpstreamInputError,
)
from spliceailookup_link.mcp.errors import (
    AmbiguousVariantError,
    BuildMismatchError,
    McpErrorContext,
    RefMismatchError,
    mcp_tool_error,
    run_mcp_tool,
)
from spliceailookup_link.variant import VariantParseError


def _classify(exc, **ctx):
    payload = mcp_tool_error(exc, McpErrorContext(**ctx)).payload
    return payload


def test_not_found_classification() -> None:
    p = _classify(DataNotFoundError("no scores"), tool_name="predict_spliceai", variant="1-1-A-T")
    assert p["error_code"] == "not_found"
    assert p["retryable"] is False
    assert p["fallback_tool"] == "resolve_variant"
    assert p["recovery_action"] == "switch_tool"


def test_invalid_input_classification() -> None:
    p = _classify(VariantParseError("bad"), tool_name="resolve_variant", query="x")
    assert p["error_code"] == "invalid_input"
    assert p["recovery_action"] == "reformulate_input"


def test_upstream_input_error_classification() -> None:
    p = _classify(UpstreamInputError("parse"), tool_name="predict_pangolin", variant="1-1-A-T")
    assert p["error_code"] == "invalid_input"


def test_rate_limited_retryable() -> None:
    p = _classify(RateLimitedError("429"), tool_name="predict_splicing")
    assert p["error_code"] == "rate_limited"
    assert p["retryable"] is True
    assert p["recovery_action"] == "retry_backoff"


def test_upstream_unavailable_retryable() -> None:
    p = _classify(SpliceApiError("503"), tool_name="predict_splicing")
    assert p["error_code"] == "upstream_unavailable"
    assert p["retryable"] is True


def test_build_mismatch_recovery_carries_inferred_build() -> None:
    exc = BuildMismatchError(
        variant_id="8-145500000-A-T", inferred_build="GRCh37", requested_build="GRCh38"
    )
    p = _classify(exc, tool_name="predict_spliceai", variant="8-145500000-A-T")
    assert p["error_code"] == "build_mismatch"
    cmd0 = p["_meta"]["next_commands"][0]
    assert cmd0["tool"] == "predict_spliceai"
    assert cmd0["arguments"]["genome_build"] == "GRCh37"


def test_internal_error_for_unexpected() -> None:
    p = _classify(KeyError("boom"), tool_name="predict_splicing")
    assert p["error_code"] == "internal_error"
    assert p["retryable"] is False


async def test_run_mcp_tool_injects_success_and_meta() -> None:
    async def call() -> dict:
        return {"value": 1}

    out = await run_mcp_tool("x", call)
    assert out["success"] is True
    assert out["_meta"]["unsafe_for_clinical_use"] is True


async def test_run_mcp_tool_returns_envelope_on_exception() -> None:
    async def call() -> dict:
        raise DataNotFoundError("nope")

    out = await run_mcp_tool(
        "predict_spliceai",
        call,
        context=McpErrorContext(tool_name="predict_spliceai", variant="1-1-A-T"),
    )
    assert out["success"] is False
    assert out["error_code"] == "not_found"


def test_ambiguous_lists_alleles_and_per_allele_next_commands() -> None:
    exc = AmbiguousVariantError(
        variant="rs6025",
        candidates=["1-169549811-C-A", "1-169549811-C-T"],
        note="rs6025 maps to 2 alleles at this locus; pick one variant_id.",
    )
    env = mcp_tool_error(
        exc,
        McpErrorContext(
            tool_name="predict_splicing", variant="rs6025", genome_build="GRCh38"
        ),
    ).payload
    assert env["error_code"] == "ambiguous"
    assert env["retryable"] is False
    assert env["variant_ids"] == ["1-169549811-C-A", "1-169549811-C-T"]
    cmds = env["_meta"]["next_commands"]
    assert cmds[0] == {
        "tool": "predict_splicing",
        "arguments": {"variant": "1-169549811-C-A", "genome_build": "GRCh38"},
    }
    assert cmds[1]["arguments"]["variant"] == "1-169549811-C-T"


def test_ref_mismatch_classifies_and_routes_to_resolve() -> None:
    exc = RefMismatchError(
        variant_id="8-140300616-A-G",
        observed_ref="A",
        reference_base="T",
        build="GRCh38",
        chrom="8",
        pos=140300616,
    )
    env = mcp_tool_error(
        exc, McpErrorContext(tool_name="predict_splicing", variant="8-140300616-A-G")
    ).payload
    assert env["error_code"] == "ref_mismatch"
    assert env["retryable"] is False
    assert env["recovery_action"] == "reformulate_input"
    assert env["fallback_tool"] == "resolve_variant"
    assert "does not match" in env["message"]
    assert "T" in env["message"]
