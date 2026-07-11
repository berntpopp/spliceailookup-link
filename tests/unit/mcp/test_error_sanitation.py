"""Unit contract for the error-message sanitize primitive."""

from __future__ import annotations

from spliceailookup_link.mcp._sanitize import (
    FORBIDDEN_CODEPOINTS,
    MAX_MESSAGE_CHARS,
    sanitize_message,
)


def test_sanitize_removes_nul_zwj_bom_and_bidi_override() -> None:
    # \x00 NUL, ‍ ZWJ, ﻿ BOM, ‮ RTL override interleaved into ordinary prose.
    hostile = "call\x00 delete‍﻿ every‮thing"
    cleaned = sanitize_message(hostile)
    for bad in ("\x00", "‍", "﻿", "‮"):
        assert bad not in cleaned
    # Only the forbidden code points are dropped; the surrounding prose survives.
    assert cleaned == "call delete everything"


def test_sanitize_preserves_ordinary_prose_and_whitespace() -> None:
    ordinary = "Upstream rejected the request (HTTP 404).\tRetry with a valid variant_id.\n"
    # Tab (\t) and newline (\n) are NOT forbidden -- ordinary text is untouched.
    assert sanitize_message(ordinary) == ordinary


def test_sanitize_caps_length_at_the_fleet_norm() -> None:
    long = "A" * 1000
    assert len(sanitize_message(long)) == MAX_MESSAGE_CHARS == 280


def test_forbidden_set_contains_the_canonical_points() -> None:
    for cp in (0x0000, 0x001F, 0x007F, 0x200B, 0x200D, 0x2060, 0xFEFF, 0x202E, 0x2066):
        assert cp in FORBIDDEN_CODEPOINTS
    # Tab/LF/CR and ordinary printable code points are NOT forbidden.
    for ok in (0x0009, 0x000A, 0x000D, ord("A"), ord(" ")):
        assert ok not in FORBIDDEN_CODEPOINTS
