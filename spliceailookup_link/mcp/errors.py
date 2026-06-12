"""Structured MCP error envelopes for spliceailookup-link.

Patterned after gnomad-link / pubtator-link. The envelope shape is what LLMs
branch on; codes are deterministic per exception class so prompts can recover
without scraping free text. Every tool body runs inside run_mcp_tool, which
returns (never raises) an envelope dict.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from spliceailookup_link.api import (
    DataNotFoundError,
    RateLimitedError,
    SpliceApiError,
    UpstreamInputError,
)
from spliceailookup_link.config import settings
from spliceailookup_link.mcp.resources import get_capabilities_version
from spliceailookup_link.variant import UnsupportedContigError, VariantParseError

logger = logging.getLogger(__name__)

RECENT_MCP_ERROR_LIMIT = 50
_RECENT_ERRORS: deque[dict[str, Any]] = deque(maxlen=RECENT_MCP_ERROR_LIMIT)

# Base `_meta` merged into every success and error envelope.
_BASE_META = {"unsafe_for_clinical_use": True}

_FALLBACK_TOOL = "get_server_capabilities"

# Prediction tools whose most likely recovery, on a bad input, is to resolve the
# variant first (the caller may have passed HGVS / rsID / a wrong build).
_PREDICTION_TOOLS = {"predict_spliceai", "predict_pangolin", "predict_splicing"}


@dataclass
class McpErrorContext:
    """Per-call context so envelopes can suggest ready-to-call fallbacks."""

    tool_name: str
    variant: str | None = None
    genome_build: str | None = None
    query: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class McpToolError(Exception):
    """An exception whose payload is the JSON-serialised envelope."""

    def __init__(self, payload: dict[str, Any]):
        super().__init__(json.dumps(payload))
        self.payload = payload


class BuildMismatchError(ValueError):
    """Raised when a variant's coordinate clearly belongs to a different build."""

    def __init__(self, *, variant_id: str, inferred_build: str, requested_build: str):
        self.variant_id = variant_id
        self.inferred_build = inferred_build
        self.requested_build = requested_build
        super().__init__(
            f"{variant_id} appears to use {inferred_build} coordinates but "
            f"genome_build={requested_build} was requested."
        )


class RefMismatchError(ValueError):
    """Raised when a coordinate's REF allele does not match the genome reference."""

    def __init__(
        self,
        *,
        variant_id: str,
        observed_ref: str,
        reference_base: str,
        build: str,
        chrom: str,
        pos: int,
        alt: str = "",
        other_build_hint: dict[str, str] | None = None,
    ):
        self.variant_id = variant_id
        self.observed_ref = observed_ref
        self.reference_base = reference_base
        self.build = build
        self.alt = alt
        self.other_build_hint = other_build_hint
        super().__init__(
            f"REF allele '{observed_ref}' does not match the {build} reference base "
            f"'{reference_base}' at {chrom}:{pos}."
        )


class AmbiguousVariantError(ValueError):
    """Raised when an input resolves to more than one ALT allele at the locus."""

    def __init__(self, *, variant: str, candidates: list[str], note: str | None = None):
        self.variant = variant
        self.candidates = candidates
        self.note = note
        super().__init__(
            note or f"{variant} resolves to {len(candidates)} alleles; pick one variant_id."
        )


class CoordinateRangeError(ValueError):
    """Raised when a coordinate's position exceeds the chromosome length in all builds."""

    def __init__(self, *, chrom: str, pos: int, grch38_len: int, grch37_len: int):
        self.chrom = chrom
        self.pos = pos
        super().__init__(
            f"Position {pos:,} exceeds the length of chr{chrom.removeprefix('chr')} in all "
            f"supported builds (GRCh38 {grch38_len:,}, GRCh37 {grch37_len:,}). Verify the "
            "coordinate; if you have an HGVS/rsID, resolve_variant can derive valid coordinates."
        )


def _provenance_meta() -> dict[str, Any]:
    return {**_BASE_META, "capabilities_version": get_capabilities_version()}


def _safe_message(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    return text[:300]


def _fallback_for(context: McpErrorContext) -> tuple[str, dict[str, Any] | None]:
    """Resolve the context-appropriate fallback tool + ready-to-call arguments."""
    if context.tool_name == "resolve_variant":
        return _FALLBACK_TOOL, None
    if context.tool_name in _PREDICTION_TOOLS and context.variant:
        return "resolve_variant", {"variant": context.variant}
    return _FALLBACK_TOOL, None


def _ref_mismatch_fallback(
    exc: RefMismatchError, context: McpErrorContext
) -> tuple[str, dict[str, Any] | None]:
    """An actionable fallback for a coordinate ref_mismatch (never the same-coord loop).

    ref_mismatch only fires on coordinate inputs (HGVS/rsID resolve via VEP and never
    reach the REF check), so re-sending the same coordinate to resolve_variant is a dead
    end. Redirect to: the matching build, a REF/ALT swap, or get_server_capabilities.
    """
    tool = context.tool_name if context.tool_name in _PREDICTION_TOOLS else "predict_splicing"
    if exc.other_build_hint:
        return tool, {"variant": exc.variant_id, "genome_build": exc.other_build_hint["build"]}
    ref, alt, base = exc.observed_ref, exc.alt, exc.reference_base
    if ref and alt and base and len(ref) == len(alt) == len(base) and alt.upper() == base.upper():
        try:
            chrom, pos, r, a = exc.variant_id.split("-", 3)
            return tool, {"variant": f"{chrom}-{pos}-{a}-{r}", "genome_build": exc.build}
        except ValueError:
            pass
    return _FALLBACK_TOOL, None


def _classify(
    exc: BaseException, context: McpErrorContext
) -> tuple[str, bool, str | None, dict[str, Any] | None]:
    """Return (error_code, retryable, fallback_tool, fallback_args).

    Subclass ordering matters: DataNotFoundError, UpstreamInputError, and
    RateLimitedError all subclass SpliceApiError, so they MUST be checked before
    the generic SpliceApiError branch.
    """
    if isinstance(exc, BuildMismatchError):
        return (
            "build_mismatch",
            False,
            context.tool_name,
            {"variant": exc.variant_id, "genome_build": exc.inferred_build},
        )
    if isinstance(exc, RefMismatchError):
        tool, args = _ref_mismatch_fallback(exc, context)
        return "ref_mismatch", False, tool, args
    if isinstance(exc, AmbiguousVariantError):
        return "ambiguous", False, "resolve_variant", {"variant": exc.variant}
    if isinstance(exc, CoordinateRangeError):
        return "invalid_input", False, _FALLBACK_TOOL, None
    if isinstance(exc, DataNotFoundError):
        tool, args = _fallback_for(context)
        return "not_found", False, tool, args
    if isinstance(exc, UnsupportedContigError):
        return "unsupported_contig", False, _FALLBACK_TOOL, None
    if isinstance(exc, (UpstreamInputError, VariantParseError)):
        tool, args = _fallback_for(context)
        return "invalid_input", False, tool, args
    if isinstance(exc, RateLimitedError):
        return "rate_limited", True, _FALLBACK_TOOL, None
    if isinstance(exc, ValueError):
        return "validation_failed", False, _FALLBACK_TOOL, None
    if isinstance(exc, (SpliceApiError, TimeoutError)):
        return "upstream_unavailable", True, _FALLBACK_TOOL, None
    return "internal_error", False, _FALLBACK_TOOL, None


def _recovery_action(error_code: str, retryable: bool) -> str:
    if retryable:
        return "retry_backoff"
    if error_code in {"invalid_input", "validation_failed", "ref_mismatch", "ambiguous"}:
        return "reformulate_input"
    if error_code == "build_mismatch":
        return "switch_tool"
    return "switch_tool"


def _recovery_text(error_code: str, fallback_tool: str | None, *, tool_name: str) -> str:
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
            "max_distance, or resubmit as a background task (task=...), which is not bound by "
            "the deadline."
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


def _envelope_message(exc: BaseException, error_code: str) -> str:
    if error_code in {
        "build_mismatch",
        "invalid_input",
        "not_found",
        "ref_mismatch",
        "ambiguous",
        "unsupported_contig",
    }:
        # These carry developer-authored or upstream guidance safe to surface.
        return _safe_message(exc)
    if error_code == "validation_failed":
        return f"Invalid input: {exc.__class__.__name__}"
    if error_code == "internal_error":
        return f"Internal error: {exc.__class__.__name__}"
    return _safe_message(exc)


def _extract_field_errors(errors: list[Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for err in errors:
        loc = err.get("loc", ())
        field_name = ".".join(str(x) for x in loc) if loc else "unknown"
        reason = err.get("msg", str(err.get("type", "invalid")))
        result.append({"field": field_name, "reason": reason})
    return result


def mcp_validation_tool_error(*, tool_name: str, exc: PydanticValidationError) -> McpToolError:
    field_errors = _extract_field_errors(list(exc.errors()))
    payload: dict[str, Any] = {
        "success": False,
        "error_code": "validation_failed",
        "message": "Invalid MCP arguments.",
        "retryable": False,
        "recovery_action": "reformulate_input",
        "fallback_tool": _FALLBACK_TOOL,
        "fallback_args": {},
        "field_errors": field_errors,
        "recovery": (
            "Inputs failed validation. Check field_errors and call get_server_capabilities "
            "for accepted parameters."
        ),
        "_meta": {
            "tool": tool_name,
            "next_commands": [{"tool": _FALLBACK_TOOL, "arguments": {}}],
            **_provenance_meta(),
        },
    }
    return McpToolError(payload)


def install_validation_error_handler(mcp_server: Any) -> None:
    """Wrap registered tools so FastMCP argument validation returns our envelope."""
    candidates: list[Any] = []
    local_provider = getattr(mcp_server, "_local_provider", None)
    components = getattr(local_provider, "_components", None)
    if isinstance(components, dict):
        candidates.extend(components.values())
    tool_manager = getattr(mcp_server, "_tool_manager", None)
    legacy_tools = getattr(tool_manager, "_tools", None)
    if isinstance(legacy_tools, dict):
        candidates.extend(legacy_tools.values())

    for tool in candidates:
        if not hasattr(tool, "run") or getattr(tool, "_splice_validation_wrapped", False):
            continue
        original_run = tool.run

        async def wrapped_run(
            arguments: dict[str, Any],
            *,
            _original_run: Callable[[dict[str, Any]], Awaitable[Any]] = original_run,
            _tool: Any = tool,
        ) -> Any:
            request_id = uuid.uuid4().hex[:12]
            start = time.perf_counter()
            try:
                return await _original_run(arguments)
            except PydanticValidationError as exc:
                envelope = mcp_validation_tool_error(
                    tool_name=str(getattr(_tool, "name", "unknown")),
                    exc=exc,
                ).payload
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                envelope["_meta"] = {
                    "request_id": request_id,
                    "timing": {"elapsed_ms": elapsed_ms},
                    **_provenance_meta(),
                    **envelope.get("_meta", {}),
                }
                record_mcp_error(
                    tool_name=str(getattr(_tool, "name", "unknown")),
                    error_code="validation_failed",
                    message=envelope["message"],
                    raw_message=str(exc),
                )
                convert_result = getattr(_tool, "convert_result", None)
                if callable(convert_result):
                    return convert_result(envelope)
                return envelope

        object.__setattr__(tool, "run", wrapped_run)
        object.__setattr__(tool, "_splice_validation_wrapped", True)


def mcp_tool_error(exc: BaseException, context: McpErrorContext) -> McpToolError:
    error_code, retryable, fallback_tool, fallback_args = _classify(exc, context)
    next_commands: list[dict[str, Any]] = []
    if fallback_tool and fallback_args:
        next_commands.append({"tool": fallback_tool, "arguments": fallback_args})
    next_commands.append({"tool": _FALLBACK_TOOL, "arguments": {}})
    payload: dict[str, Any] = {
        "success": False,
        "error_code": error_code,
        "message": _envelope_message(exc, error_code),
        "retryable": retryable,
        "recovery_action": _recovery_action(error_code, retryable),
        "fallback_tool": fallback_tool,
        "fallback_args": fallback_args,
        "recovery": _recovery_text(error_code, fallback_tool, tool_name=context.tool_name),
        "_meta": {
            "tool": context.tool_name,
            "next_commands": next_commands,
            **_provenance_meta(),
        },
    }
    if isinstance(exc, CoordinateRangeError):
        payload["recovery"] = (
            "The position is beyond the chromosome length in every supported build, so no "
            "build can score it. Verify the coordinate against the reference. resolve_variant "
            "cannot rescue a bad coordinate -- only an HGVS/rsID input."
        )
    if isinstance(exc, AmbiguousVariantError):
        build = context.genome_build or "GRCh38"
        payload["variant_ids"] = exc.candidates
        payload["_meta"]["next_commands"] = [
            {"tool": "predict_splicing", "arguments": {"variant": c, "genome_build": build}}
            for c in exc.candidates
        ] + payload["_meta"]["next_commands"]
    if isinstance(exc, RefMismatchError):
        if exc.other_build_hint:
            # D1: a wrong REF that coincidentally matches the other build's base stays a
            # ref_mismatch; the other-build possibility is a secondary hint, not a redirect.
            payload["other_build_hint"] = exc.other_build_hint
            payload["recovery"] = f"{payload['recovery']} {exc.other_build_hint['note']}"
        elif (
            exc.observed_ref
            and exc.alt
            and exc.reference_base
            and exc.alt.upper() == exc.reference_base.upper()
            and len(exc.observed_ref) == len(exc.alt) == len(exc.reference_base)
        ):
            # F2: the ALT base matches the reference here -> most likely a REF/ALT swap;
            # the fallback re-runs with REF/ALT swapped rather than looping resolve_variant.
            payload["recovery"] = (
                f"{payload['recovery']} The ALT base matches the reference here, so the most "
                "likely cause is a REF/ALT swap; the fallback re-runs with REF/ALT swapped."
            )
    if error_code == "rate_limited":
        # rate_budget reports the LOCAL concurrency cap (asyncio.Semaphore), not a
        # time window -- IETF qu=concurrent-requests, no window_s (no bucket to
        # reset). remaining=0 is exact for local saturation; for an upstream HTTP
        # 429 (also RateLimitedError) it is a conservative floor, not upstream quota.
        payload["_meta"]["rate_budget"] = {
            "limit": settings.MAX_CONCURRENCY,
            "remaining": 0,
            "unit": "concurrent_requests",
        }
    return McpToolError(payload)


def record_mcp_error(*, tool_name: str, error_code: str, message: str, raw_message: str) -> None:
    _RECENT_ERRORS.append(
        {
            "tool_name": tool_name,
            "error_code": error_code,
            "message": message,
            "raw_message": raw_message[:500],
        }
    )


def get_recent_errors() -> list[dict[str, Any]]:
    return list(_RECENT_ERRORS)


def clear_recent_errors() -> None:
    _RECENT_ERRORS.clear()


async def run_mcp_tool(
    tool_name: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    context: McpErrorContext | None = None,
    lean_meta: bool = False,
) -> dict[str, Any]:
    """Execute an MCP tool body, converting any exception to an envelope dict.

    lean_meta=True (response_mode='minimal' or include_hints=False) drops the
    repetitive capabilities_version from _meta to save tokens on high-volume
    calls; the research-use disclaimer (unsafe_for_clinical_use) is always kept.
    """
    ctx = context or McpErrorContext(tool_name=tool_name)
    request_id = uuid.uuid4().hex[:12]
    start = time.perf_counter()

    def _stamp(envelope: dict[str, Any]) -> dict[str, Any]:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        existing: dict[str, Any] = envelope.get("_meta") or {}
        meta: dict[str, Any] = {
            "request_id": request_id,
            "timing": {"elapsed_ms": elapsed_ms},
            **existing,
            **_BASE_META,  # unsafe_for_clinical_use -- always present
        }
        if not lean_meta:
            meta["capabilities_version"] = get_capabilities_version()
        envelope["_meta"] = meta
        return envelope

    try:
        result = await call()
        result.setdefault("success", True)
        return _stamp(result)
    except McpToolError as exc:
        record_mcp_error(
            tool_name=tool_name,
            error_code=exc.payload.get("error_code", "internal_error"),
            message=exc.payload.get("message", ""),
            raw_message=str(exc),
        )
        return _stamp(exc.payload)
    except Exception as exc:  # broad catch is the error-boundary contract
        wrapped = mcp_tool_error(exc, ctx)
        logger.warning(
            "mcp_tool_error tool=%s code=%s request_id=%s exc=%s",
            tool_name,
            wrapped.payload["error_code"],
            request_id,
            exc.__class__.__name__,
        )
        record_mcp_error(
            tool_name=tool_name,
            error_code=wrapped.payload["error_code"],
            message=wrapped.payload["message"],
            raw_message=str(exc),
        )
        return _stamp(wrapped.payload)
