"""FastMCP-core not-found reflection guard (Response-Envelope v1.1 fast-follow).

FastMCP core (pinned ``>=3.4.5,<4.0.0``) reflects the caller's OWN requested tool
name / resource URI / prompt name back to the caller (and to logs) BEFORE any
backend middleware runs. This module closes that residual with fixed, input-free
messages built from CONSTANTS only, mirroring the ratified fleet references
(``mondo``/``hpo`` registry preflight, ``clinvar`` protocol backstop,
``panelapp``/``autopvs1`` validation-log scrub filter).

The reflected text is *caller-supplied* (a caller self-reflection surface), so
this is materially lower-risk than the upstream-injection leak the prior sweep
closed. It is still worth closing: the reflected name/URI -- with any
control/zero-width/bidi/NUL code points -- lands in shared operator logs and in an
agent's tool-result context. Fixed constants remove the channel entirely.

Layers (spec §3), copied per repo (no shared runtime library exists fleet-wide):

* Layer 1 -- ``on_call_tool`` registry preflight: ``get_tool(name)`` returns
  ``None`` for an unknown/disabled tool, so we return a fixed, name-free
  ``not_found`` envelope (as an in-band ``is_error`` ``ToolResult``) BEFORE core
  dispatch. Closes the unknown-TOOL surface; never echoes ``_meta.tool``.
* Layer 2 -- ``on_read_resource`` boundary: an unknown (URL-valid) resource makes
  core raise ``NotFoundError("Unknown resource: '<uri>'")``; we re-raise a fixed
  URI-free ``ResourceError``. This server registers only static resources that
  never raise an author-authored ``ResourceError``, so the allowlist is empty and
  ANY resource error is replaced (``str(exc)`` is never re-published: sanitation
  strips code points but PRESERVES injection prose).
* Layer 3 -- protocol-handler backstop: wraps the raw ``CallTool`` / ``ReadResource``
  / ``GetPrompt`` request handlers as the OUTERMOST layer. Replaces any non-envelope
  ``isError`` tool result (the unknown-tool *return* path) and re-raises fixed
  input-free messages for resource/prompt dispatch failures -- the ONLY layer that
  covers the unknown-PROMPT surface.
* Layer 5 -- validation-log scrub filter: FastMCP's pre-middleware DEBUG logs and
  the MCP SDK session's request-validation logs echo the raw name/URI (with code
  points) on their own loggers/handlers. The filter neutralizes those records at
  the source loggers (root + ``mcp.shared.session`` + the ``fastmcp`` non-propagating
  parent and its Rich handlers) so caller input never reaches a log sink.

Layer 4 (arg-validation) is the existing tool-run wrapper installed by
``install_validation_error_handler`` (``mcp/errors.py``): it converts FastMCP's own
``ValidationError``/pydantic ``ValidationError`` into the fixed ``invalid_input``
envelope (error_subtype ``validation_failed``). Layer 6 (OTel span redaction) is a
no-op here: ``opentelemetry-sdk`` is
NOT in the base (runtime) dependency closure (``uv tree --no-dev`` excludes it) and
the codebase configures no tracer, so the tracer provider is non-recording -- no
span exception attributes are ever captured, so there is nothing to redact (fleet
policy: do NOT add the SDK dependency).
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import mcp.types
from fastmcp.exceptions import NotFoundError as FastMCPNotFoundError
from fastmcp.exceptions import ResourceError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult

from spliceailookup_link.mcp.envelope import error_tool_result
from spliceailookup_link.mcp.resources import get_capabilities_version

logger = logging.getLogger(__name__)

# Fixed, input-free public messages. They NEVER contain the requested name/URI
# (nor a ``_meta.tool`` echo of it): sanitation strips code points but not
# injection prose, so a fixed constant is the only safe source (prior-sweep
# lesson). ``not_found`` reuses this repo's error-code vocabulary (spec §3.1).
_UNKNOWN_TOOL_MESSAGE = "The requested tool is not available."
_UNKNOWN_RESOURCE_MESSAGE = "The requested resource is not available."
_UNKNOWN_PROMPT_MESSAGE = "The requested prompt is not available."
_UNKNOWN_TOOL_SUGGESTION = "Call get_server_capabilities to list the available tools."
_FALLBACK_TOOL = "get_server_capabilities"

#: Closed set of author-authored, code-point-free fixed ResourceError messages
#: that resource handlers may legitimately surface to the caller. This server
#: registers only static resources (capabilities/usage/reference/research-use/
#: citations) that never raise an author-authored ResourceError, so the set is
#: empty: ANY resource error is treated as untrusted and replaced with
#: :data:`_UNKNOWN_RESOURCE_MESSAGE`.
_KNOWN_RESOURCE_MESSAGES: frozenset[str] = frozenset()


def _unknown_tool_payload() -> dict[str, Any]:
    """The fixed, name-free ``not_found`` envelope payload for an unknown tool.

    ``_meta`` deliberately OMITS a ``tool`` key so the requested (caller-controlled)
    name is never reflected back on the wire. Every value is a server-authored
    constant.
    """
    return {
        "success": False,
        "error_code": "not_found",
        "message": _UNKNOWN_TOOL_MESSAGE,
        "retryable": False,
        "recovery_action": "switch_tool",
        "fallback_tool": _FALLBACK_TOOL,
        "fallback_args": {},
        "recovery": _UNKNOWN_TOOL_SUGGESTION,
        "_meta": {
            "next_commands": [{"tool": _FALLBACK_TOOL, "arguments": {}}],
            "unsafe_for_clinical_use": True,
            "capabilities_version": get_capabilities_version(),
        },
    }


def unknown_tool_result() -> ToolResult:
    """Return a fixed, name-free ``not_found`` in-band error ``ToolResult``.

    Carries both ``structured_content`` and a matching TextContent JSON mirror,
    with ``is_error=True`` (Response-Envelope Standard v1 SS2) so a FastMCP Client
    never re-logs the requested name while validating an ``is_error=False`` result.
    """
    return error_tool_result(_unknown_tool_payload())


class NotFoundGuard(Middleware):
    """Layer 1 (tool preflight) + Layer 2 (resource boundary)."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        """Preflight the tool NAME; an unknown name never reaches core dispatch.

        ``get_tool`` returns ``None`` (it does not raise) for an unknown or
        disabled tool, so an unknown name is caught here and answered with a
        fixed, name-free envelope. Otherwise defer to the chain (the tool-run
        arg-validation wrapper + the tool body).
        """
        fctx = getattr(context, "fastmcp_context", None)
        name = getattr(getattr(context, "message", None), "name", None)
        if fctx is not None and isinstance(name, str):
            try:
                tool = await fctx.fastmcp.get_tool(name)
            except Exception:
                tool = object()  # resolution failure: defer to core, do not mask
            if tool is None:
                logger.warning("mcp_unknown_tool")
                return unknown_tool_result()
        return await call_next(context)

    async def on_read_resource(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """Emit a FIXED, URI-free error for a resource not-found / read failure.

        The requested URI is caller-controlled; FastMCP core echoes it
        (``Unknown resource: '<uri>'`` / ``Error reading resource '<uri>'``) in
        both the direct exception and the protocol error. Re-raise a fixed
        message so the URI never reaches the caller/protocol.
        """
        try:
            return await call_next(context)
        except ResourceError as exc:
            # Author-classified ResourceError: surface it ONLY when its message is
            # one of a closed set of known fixed constants. NEVER re-publish
            # str(exc) -- sanitation strips code points but PRESERVES injection
            # prose, so a fixed constant / validated enum is the only safe
            # caller-visible source (error-sanitation-sweep rule).
            if str(exc) in _KNOWN_RESOURCE_MESSAGES:
                raise
            logger.warning("mcp_resource_error type=%s", type(exc).__name__)
            raise ResourceError(_UNKNOWN_RESOURCE_MESSAGE) from None
        except Exception as exc:
            logger.warning("mcp_resource_error type=%s", type(exc).__name__)
            raise ResourceError(_UNKNOWN_RESOURCE_MESSAGE) from None


# ---------------------------------------------------------------------------
# Layer 3 -- protocol-handler backstop (clinvar pattern)
# ---------------------------------------------------------------------------


class ProtocolError(Exception):
    """A dispatch-level failure re-raised with a FIXED, input-free message."""


def _is_structured_envelope(call_result: mcp.types.CallToolResult) -> bool:
    """True if an ``isError`` result carries one of OUR JSON envelopes.

    Distinguishes a structured spliceailookup-link error (already input-free --
    it has an ``error_code``) from a RAW FastMCP dispatch error whose plain-text
    message echoes the caller-supplied tool name (``Unknown tool: '<name>'``).
    """
    if not call_result.content:
        return False
    text = getattr(call_result.content[0], "text", None)
    if not isinstance(text, str):
        return False
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and "error_code" in obj


def _fixed_tool_not_found_result() -> mcp.types.ServerResult:
    """A fixed, input-free ServerResult for an unknown/failed tool dispatch."""
    return mcp.types.ServerResult(unknown_tool_result().to_mcp_result())


def install_protocol_error_handler(mcp_server: Any) -> None:
    """Wrap the tool/resource/prompt request handlers as the OUTERMOST layer.

    A FastMCP core not-found (or read) error can no longer reflect the
    caller-supplied name/URI. Install AFTER all tools/resources/prompts are
    registered so the handlers exist.
    """
    handlers = mcp_server._mcp_server.request_handlers

    call_tool = handlers.get(mcp.types.CallToolRequest)
    if call_tool is not None:

        async def wrapped_call_tool(
            request: mcp.types.CallToolRequest,
            *,
            _orig: Any = call_tool,
        ) -> mcp.types.ServerResult:
            try:
                result = cast(mcp.types.ServerResult, await _orig(request))
            except FastMCPNotFoundError:
                # Unknown-tool *raise* drift (should not reach here once Layer 1
                # is active) -- answer with the fixed name-free envelope.
                logger.warning("mcp_protocol_error kind=tool")
                return _fixed_tool_not_found_result()
            # FastMCP *returns* an isError CallToolResult with a raw plain-text
            # message ("Unknown tool: '<name>'") for an unknown tool; replace any
            # isError result that is NOT one of our structured envelopes. A masked
            # runtime ToolError is a FastMCPError (raised, not returned) and does
            # not pass through here, so this only catches the name-echoing return.
            root = getattr(result, "root", None)
            if (
                isinstance(root, mcp.types.CallToolResult)
                and root.isError
                and not _is_structured_envelope(root)
            ):
                logger.warning("mcp_protocol_error kind=tool")
                return _fixed_tool_not_found_result()
            return result

        handlers[mcp.types.CallToolRequest] = wrapped_call_tool

    for request_type, message, kind in (
        (mcp.types.ReadResourceRequest, _UNKNOWN_RESOURCE_MESSAGE, "resource"),
        (mcp.types.GetPromptRequest, _UNKNOWN_PROMPT_MESSAGE, "prompt"),
    ):
        orig = handlers.get(request_type)
        if orig is None:
            continue

        async def wrapped(
            request: Any,
            *,
            _orig: Any = orig,
            _message: str = message,
            _kind: str = kind,
        ) -> Any:
            try:
                return await _orig(request)
            except Exception as exc:
                # Re-raise with a FIXED, input-free message so no requested
                # name/URI (or its code points) reaches the JSON-RPC error frame.
                # Log the exception CLASS only (never the caller-controlled value).
                logger.warning("mcp_protocol_error kind=%s type=%s", _kind, type(exc).__name__)
                raise ProtocolError(_message) from None

        handlers[request_type] = wrapped


# ---------------------------------------------------------------------------
# Layer 5 -- validation-log scrub filter (panelapp/autopvs1 pattern)
# ---------------------------------------------------------------------------
#
# Each entry is a substring that appears in the ``record.msg`` of a FastMCP-core
# or MCP-SDK log line that reflects the caller-supplied name/URI (either
# interpolated into an f-string ``msg`` or carried in ``record.args``). Matching
# on ``msg`` (the format string) covers both forms because the scrub clears the
# args as well. Verified against this stack's real records (see probe / tests):
# "Tool cache miss for <name>", "[<srv>] Handler called: call_tool/read_resource/
# get_prompt <name/uri>", and the SDK-session "Failed to validate request" /
# "Message that failed validation" root records for a malformed URI.
_SCRUB_MARKERS: tuple[str, ...] = (
    "Handler called: call_tool",
    "Handler called: read_resource",
    "Handler called: get_prompt",
    "Invalid arguments for tool",
    "Error calling tool",
    "Error reading resource",
    "Failed to validate request",
    "Failed to validate notification",
    "Message that failed validation",
    "Tool cache miss for",
)

# The source loggers on which those records are CREATED. A logging filter must be
# attached to the originating logger (or its handlers) -- logger-level filters are
# skipped during propagation, but HANDLER-level filters DO run during propagation.
# The MCP SDK session logs the request-validation failure via the module-level
# ``logging.warning`` (root). ``fastmcp`` is FastMCP's non-propagating parent
# logger (propagate=False, its own Rich handlers): attaching there -- and to its
# handlers -- scrubs at the handler level any record that propagates up from a
# child logger to the Rich handlers.
_SCRUB_LOGGERS: tuple[str, ...] = (
    "",  # root -- mcp.shared.session request-validation failures
    "fastmcp",  # non-propagating parent + its Rich handlers (handler-level scrub)
    "fastmcp.server.server",
    "fastmcp.server.mixins.mcp_operations",
    "mcp.shared.session",
    "mcp.server.lowlevel.server",
)

_SCRUBBED_MESSAGE = "MCP request rejected (details omitted)."


class _ValidationLogScrubFilter(logging.Filter):
    """Scrub log records that would echo a caller-supplied tool name / URI.

    Replaces the record payload with fixed metadata (clearing ``args`` /
    ``exc_info`` / ``exc_text`` / ``stack_info``) so the caller-chosen name/URI --
    and any control/zero-width/bidi/NUL code points it carries -- can never reach
    a log or telemetry sink. Always returns ``True``: the (now input-free) record
    is still emitted for operational visibility.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg if isinstance(record.msg, str) else ""
        if any(marker in msg for marker in _SCRUB_MARKERS):
            record.msg = _SCRUBBED_MESSAGE
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


#: One shared filter instance so idempotent installs don't stack duplicates.
_SHARED_FILTER = _ValidationLogScrubFilter()


def _has_filter(target: logging.Logger | logging.Handler) -> bool:
    return any(isinstance(f, _ValidationLogScrubFilter) for f in target.filters)


def install_validation_log_filter() -> None:
    """Idempotently attach the scrub filter to each source logger (and handlers).

    Attach directly to each originating logger -- including ROOT (where
    ``mcp.shared.session`` emits its request-validation failures via a bare
    ``logging.warning``) and FastMCP's own non-propagating ``fastmcp`` logger,
    whose Rich handlers would otherwise bypass a root-only filter. Also attach to
    each logger's existing handlers as belt-and-braces. Call after the FastMCP
    facade is built, so the framework handlers already exist.
    """
    for name in _SCRUB_LOGGERS:
        target = logging.getLogger(name)
        if not _has_filter(target):
            target.addFilter(_SHARED_FILTER)
        for handler in target.handlers:
            if not _has_filter(handler):
                handler.addFilter(_SHARED_FILTER)
