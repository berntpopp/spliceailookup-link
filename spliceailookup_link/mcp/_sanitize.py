"""Caller-visible message sanitation for the MCP error surface.

This is a `no-untrusted-text` (classify) backend -- every tool RESULT is numeric
splice deltas / positions / curated identifiers, so there is no primary prose
fence (`untrusted_content.py`). The residual external-text surface is the ERROR
path: an upstream 4xx/5xx (or 200-error) body, and `str(exc)` diagnostics, can
carry injection prose and control/zero-width/bidi/NUL code points into a
caller-visible ``message``/``error`` field.

Defense in depth (secondary surface): upstream response BODIES are additionally
kept out of caller-visible messages at their source (the API clients raise
fixed, status-keyed, body-free messages -- see ``api/base_client.py`` and
``api/scoring_client.py``). ``sanitize_message`` is the code-point backstop
applied to EVERY caller-visible message/error/diagnostics string.
"""

from __future__ import annotations

from typing import Any

# The ratified fence forbidden code-point set (C0/C1 controls except tab/LF/CR,
# zero-width, word-joiner, BOM, and bidi embedding/override/isolate controls).
# Byte-identical to the fleet fence's ``FORBIDDEN_CODEPOINTS``.
FORBIDDEN_CODEPOINTS = frozenset(
    {
        *range(0x0000, 0x0009),
        *range(0x000B, 0x000D),
        *range(0x000E, 0x0020),
        *range(0x007F, 0x00A0),
        0x200B,
        0x200C,
        0x200D,
        0x2060,
        0xFEFF,
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
    }
)

MAX_MESSAGE_CHARS = 280


def sanitize_message(text: str) -> str:
    """Strip the fence's forbidden control/zero-width/bidi/NUL code points + length-cap.

    Applied to EVERY caller-visible message/error/diagnostics string so a hostile
    upstream (or a caller-influenced 4xx/5xx body) can never smuggle control,
    zero-width, bidirectional, or NUL code points into an error frame. Caller-visible
    messages are server-authored guidance data; upstream response bodies are
    additionally kept out of them at the source (see module docstring).
    """
    clean = "".join(char for char in text if ord(char) not in FORBIDDEN_CODEPOINTS)
    return clean[:MAX_MESSAGE_CHARS]


# Pydantic error ``type``s whose location IS a caller-supplied, attacker-controlled
# argument NAME (an unexpected/extra argument). That name is redacted, never echoed:
# sanitizing it would still surface hostile prose, and the name has no diagnostic value.
_UNEXPECTED_ARG_TYPES = frozenset(
    {"unexpected_keyword_argument", "unexpected_positional_argument", "extra_forbidden"}
)


def sanitize_field_errors(errors: list[Any]) -> list[dict[str, str]]:
    """Build sanitized ``{field, reason}`` rows from pydantic ``.errors()`` output.

    Both the ``field`` (joined location) and the ``reason`` (pydantic ``msg``) are
    caller-influenceable: an unexpected-argument location is the caller's own
    argument name, and a value message can echo the rejected input. The field name
    is redacted for unexpected/extra arguments and sanitized of forbidden code
    points otherwise; the reason is a fixed string for those and sanitized elsewhere.
    """
    result: list[dict[str, str]] = []
    for err in errors:
        err_type = str(err.get("type", "invalid"))
        if err_type in _UNEXPECTED_ARG_TYPES:
            field_name, reason = "unexpected_argument", "Unexpected argument for this tool."
        else:
            loc = err.get("loc", ())
            field_name = ".".join(str(x) for x in loc) if loc else "unknown"
            reason = sanitize_message(err.get("msg", err_type))
        result.append({"field": sanitize_message(field_name), "reason": reason})
    return result
