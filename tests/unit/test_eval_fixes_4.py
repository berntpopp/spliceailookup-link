"""Regression tests for docs/mcp-consumer-assessment-2026-06-12.md (F18-F24)."""

from __future__ import annotations

from spliceailookup_link.api import RateLimitedError, SpliceApiError
from tests.conftest import StubService, expect_tool_error, structured


# --- F19: unsupported contig fast-fails before any scoring call ---
async def test_f19_mt_fast_fails_unsupported_contig_no_scoring(
    mcp, stub_service: StubService
) -> None:
    data = await expect_tool_error(mcp, "predict_splicing", {"variant_id": "MT-3243-A-G"})
    assert data["error_code"] == "unsupported_contig"
    assert data["retryable"] is False
    # The whole point: no upstream scoring slot was ever consumed.
    assert stub_service.score_calls == []
    # Recovery points the caller at the right cross-server tool.
    assert "gnomad-link" in data["recovery"]


async def test_f19_mt_in_batch_is_per_item_unsupported_contig(
    mcp, stub_service: StubService
) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_splicing_batch",
            {"variant_ids": ["chr8-140300616-T-G", "MT-3243-A-G"]},
        )
    )
    by_variant = {r["variant"]: r for r in data["results"]}
    assert by_variant["MT-3243-A-G"]["error_code"] == "unsupported_contig"
    assert "error_code" not in by_variant["chr8-140300616-T-G"]
    # MT consumed no scoring slot; only the valid item scored (spliceai + pangolin).
    assert all(c["variant_id"] != "MT-3243-A-G" for c in stub_service.score_calls)


# --- F18 + F23: resilient batch runner ---
from spliceailookup_link.mcp.tools._batch_runner import run_batch  # noqa: E402

_PARAMS = {
    "max_distance": 500,
    "mask": "raw",
    "gene_set": "basic",
    "transcripts": "mane",
    "response_mode": "compact",
    "cross_build_check": True,
    "enforce_deadline": True,
}


def _ok_result(variant: str) -> dict:
    return {
        "variant_id": variant,
        "agreement": {"verdict": "concordant_low"},
        "spliceai": {"max_delta_score": 0.1},
        "_telemetry": {"cache": "miss", "upstream_elapsed_ms": 5, "cache_age_s": None},
    }


def _make_predict_fn():
    """Fake predict_fn keyed on the variant string; deterministic, no sleeps."""
    attempts: dict[str, int] = {}

    async def predict_fn(service, *, variant, genome_build, **params):
        attempts[variant] = attempts.get(variant, 0) + 1
        if variant == "OK":
            return _ok_result(variant)
        if variant == "RETRY_OK":
            if attempts[variant] == 1:
                raise RateLimitedError("saturated")
            return _ok_result(variant)
        if variant == "ALWAYS_429":
            raise RateLimitedError("saturated")
        if variant == "ALWAYS_503":
            raise SpliceApiError("upstream 503")
        if variant == "BAD":
            from spliceailookup_link.variant import VariantParseError

            raise VariantParseError("nope")
        raise AssertionError(variant)

    return predict_fn, attempts


async def test_f18_runner_splits_terminal_retryable_and_emits_retry_variants(stub_service) -> None:
    predict_fn, attempts = _make_predict_fn()
    out = await run_batch(
        stub_service,
        variants=["OK", "RETRY_OK", "ALWAYS_503", "ALWAYS_429", "BAD"],
        genome_build="GRCh38",
        params=_PARAMS,
        predict_fn=predict_fn,
        retry_backoff_s=0,
    )
    s = out["summary"]
    assert s["ok"] == 2  # OK + RETRY_OK
    assert s["terminal_failed"] == 1  # BAD
    assert s["retryable_failed"] == 2  # ALWAYS_503 + ALWAYS_429
    assert s["failed"] == s["terminal_failed"] + s["retryable_failed"]
    assert s["retried"] == 3  # RETRY_OK + ALWAYS_503 + ALWAYS_429 each retried once
    assert attempts["RETRY_OK"] == 2 and attempts["ALWAYS_503"] == 2 and attempts["BAD"] == 1
    assert set(out["retry_variants"]) == {"ALWAYS_503", "ALWAYS_429"}


async def test_f23_runner_attaches_rate_budget_to_per_item_rate_limited(stub_service) -> None:
    predict_fn, _ = _make_predict_fn()
    out = await run_batch(
        stub_service,
        variants=["ALWAYS_429"],
        genome_build="GRCh38",
        params=_PARAMS,
        predict_fn=predict_fn,
        retry_backoff_s=0,
    )
    item = out["results"][0]
    assert item["error_code"] == "rate_limited"
    assert item["rate_budget"]["unit"] == "concurrent_requests"


async def test_f18_batch_retryable_item_goes_to_retry_variants(
    mcp, stub_service: StubService
) -> None:
    from spliceailookup_link.config import settings

    stub_service.score_error = SpliceApiError("upstream 503")
    old = settings.BATCH_RETRY_BACKOFF_SECONDS
    settings.BATCH_RETRY_BACKOFF_SECONDS = 0
    try:
        data = structured(
            await mcp.call_tool("predict_splicing_batch", {"variant_ids": ["1-100-A-T"]})
        )
    finally:
        settings.BATCH_RETRY_BACKOFF_SECONDS = old
    assert data["summary"]["retryable_failed"] == 1
    assert data["summary"]["terminal_failed"] == 0
    assert data["retry_variants"] == ["1-100-A-T"]


# --- F21: resolve_variant recovery prose is not circular ---
async def test_f21_resolve_invalid_input_recovery_is_not_circular(mcp) -> None:
    data = await expect_tool_error(mcp, "resolve_variant", {"variant_id": "totally not a variant"})
    assert data["error_code"] == "invalid_input"
    # The bug: prose told you to "call resolve_variant" from inside resolve_variant.
    assert "resolve_variant" not in data["recovery"]
    assert "get_server_capabilities" in data["recovery"]


async def test_f21_prediction_invalid_input_still_points_to_resolve(mcp) -> None:
    data = await expect_tool_error(mcp, "predict_splicing", {"variant_id": "totally not a variant"})
    assert data["error_code"] == "invalid_input"
    assert "resolve_variant" in data["recovery"]  # unchanged for prediction tools


# --- F22: include_hints opt-out ---
async def test_f22_include_hints_false_drops_next_commands_and_see_also(mcp) -> None:
    full = structured(await mcp.call_tool("predict_splicing", {"variant_id": "chr8-140300616-T-G"}))
    assert "next_commands" in full["_meta"] and "see_also" in full["_meta"]

    lean = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant_id": "chr8-140300616-T-G", "include_hints": False}
        )
    )
    assert "next_commands" not in lean["_meta"]
    assert "see_also" not in lean["_meta"]
    # Observability + provenance are retained (safety + drift detection).
    assert "request_id" in lean["_meta"]
    assert lean["_meta"]["unsafe_for_clinical_use"] is True


async def test_f22_include_hints_false_on_single_and_resolve(mcp) -> None:
    for tool in ("predict_spliceai", "predict_pangolin"):
        data = structured(
            await mcp.call_tool(tool, {"variant_id": "chr8-140300616-T-G", "include_hints": False})
        )
        assert "next_commands" not in data["_meta"] and "see_also" not in data["_meta"]
    rv = structured(
        await mcp.call_tool(
            "resolve_variant", {"variant_id": "chr8-140300616-T-G", "include_hints": False}
        )
    )
    assert "next_commands" not in rv["_meta"]


# --- F19b: resolve_variant flags non-nuclear contigs as not scoring-supported ---
async def test_f19b_resolve_marks_mt_not_scoring_supported(mcp) -> None:
    data = structured(await mcp.call_tool("resolve_variant", {"variant_id": "MT-3243-A-G"}))
    assert data["success"] is True  # resolve normalizes coordinates; it does not score
    assert data["scoring_supported"] is False
    assert "MT" in data["note"] or "itochondrial" in data["note"]


async def test_f19b_resolve_nuclear_has_no_scoring_supported_flag(mcp) -> None:
    data = structured(await mcp.call_tool("resolve_variant", {"variant_id": "chr8-140300616-T-G"}))
    assert "scoring_supported" not in data  # additive: only set when NOT supported


# --- F24: capabilities document the new code, batch semantics, include_hints ---
from spliceailookup_link.mcp.resources import (  # noqa: E402
    get_capabilities_resource,
    get_reference_resource,
)


def test_f24_capabilities_documents_new_code_and_batch_semantics():
    doc = get_capabilities_resource()
    assert "unsupported_contig" in doc["error_codes"]
    assert "batch_semantics" in doc
    assert "retry_variants" in doc["batch_semantics"]
    assert "include_hints" in doc["response_fields"]
    ref = get_reference_resource()
    assert "unsupported_contig" in ref["error_taxonomy"]["codes"]


def test_f24_capabilities_version_stable_and_12_char():
    a = get_capabilities_resource()
    b = get_capabilities_resource()
    assert a["capabilities_version"] == b["capabilities_version"]
    assert len(a["capabilities_version"]) == 12
