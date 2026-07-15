"""Response-Envelope Standard v1 framing helpers for spliceailookup-link.

Split out of ``mcp/errors.py`` to stay under the fleet's 600-LOC/module budget
(AGENTS.md "File Size Discipline"). Pure envelope-SHAPE concerns (success
framing, in-band error ``ToolResult`` construction, typed `_meta` hints) live
here; error CLASSIFICATION (exception -> error_code/retryable/recovery) stays
in ``errors.py``. Mirrors the clingen-link split (``clingen_link/mcp/envelope.py``
builds `_meta`; ``clingen_link/mcp/errors.py`` classifies).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from spliceailookup_link.config import settings
from spliceailookup_link.mcp.error_codes import normalize_error_code


def rate_budget_snapshot(*, saturated: bool) -> dict[str, Any]:
    """The advertised concurrency budget + soft client-pacing interval.

    The cap is a LOCAL asyncio.Semaphore (MAX_CONCURRENCY), not a tracked time-window
    quota. On success we advertise the soft min spacing for cache-miss calls; on a
    rate_limited failure we add remaining=0 and a retry_after_s for immediate backoff.
    """
    snap: dict[str, Any] = {
        "limit": settings.MAX_CONCURRENCY,
        "unit": "concurrent_requests",
        "min_interval_ms": settings.RATE_BUDGET_MIN_INTERVAL_MS,
    }
    if saturated:
        snap["remaining"] = 0
        snap["retry_after_s"] = max(1, round(settings.RATE_BUDGET_MIN_INTERVAL_MS / 1000))
    return snap


def latency_hint(
    cost_tier: Literal["low", "medium", "high"], expected_cold_latency_ms: int
) -> dict[str, Any]:
    """Typed cold-latency hint for `_meta` (Response-Envelope Standard v1 SS7).

    Declared alongside the protocol-level ``execution.taskSupport`` (set via
    ``task=True`` on the ``@mcp.tool`` decorator; verified against the
    installed fastmcp 3.4.2: ``Tool.task_config.mode`` feeds
    ``mcp_tool.execution = ToolExecution(taskSupport=...)`` on the wire MCP
    Tool -- ``fastmcp/tools/base.py`` ``Tool.to_mcp_tool``, backed by
    ``mcp.types.ToolExecution.taskSupport in {forbidden, optional, required}``)
    so an agent can plan for a slow call -- or fire it as a background task --
    before it blocks a turn, without waiting on a live response to find out.
    """
    return {"cost_tier": cost_tier, "expected_cold_latency_ms": expected_cold_latency_ms}


def error_tool_result(payload: dict[str, Any]) -> ToolResult:
    """Surface a structured envelope as an in-band MCP error result.

    Response-Envelope Standard v1 SS2: execution errors are a normal tool
    result with MCP ``isError: true`` AND this flat envelope present as
    ``structuredContent`` -- not raised as a bare ``fastmcp.exceptions.ToolError``
    (that shape only carries a text message and drops structuredContent).
    Verified against the installed fastmcp 3.4.2: ``ToolResult(is_error=True,
    ...)`` round-trips through ``CallToolResult(isError=True,
    structuredContent=...)`` (``ToolResult.to_mcp_result``), and
    ``Tool.convert_result`` passes a returned ``ToolResult`` straight through
    without re-validating it against the tool's declared output schema.

    This is the SINGLE error egress for the whole server, so ``error_code`` is
    canonicalised HERE (Response-Envelope Standard v1): any off-enum code -- including
    one on a raw ``McpToolError`` that bypassed the classification constructors -- is
    normalised onto the closed six, with the original preserved additively under
    ``error_subtype``. Both the ``structuredContent`` and the TextContent mirror below
    are built from the normalised payload, so they can never disagree on the wire.
    """
    payload = normalize_error_code(payload)
    text = json.dumps(payload, separators=(",", ":"))
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=payload,
        is_error=True,
    )


def frame_success(envelope: dict[str, Any], envelope_key: str | None) -> dict[str, Any]:
    """Nest domain fields under ``envelope_key`` (Response-Envelope Standard v1 SS1).

    The frame is ``{success, result|results, _meta}``; domain fields never sit
    flat at the top level beside them. Pass ``envelope_key=None`` for a tool
    that already returns the SS1 collection frame itself (a ``results`` array
    plus sibling domain keys, e.g. predict_splicing_batch) so it is not
    double-wrapped.
    """
    if envelope_key is None:
        return envelope
    meta = envelope.pop("_meta", None)
    success = envelope.pop("success", True)
    framed: dict[str, Any] = {"success": success, envelope_key: envelope}
    if meta is not None:
        framed["_meta"] = meta
    return framed
