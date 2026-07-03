"""Shared pytest fixtures: a stub SpliceService and a facade wired to it."""

from __future__ import annotations

from typing import Any

import pytest

from spliceailookup_link.api import DataNotFoundError
from spliceailookup_link.mcp.facade import create_spliceai_mcp
from tests.fixtures.api_responses import (
    PANGOLIN_TRAPPC9,
    SPLICEAI_TRAPPC9,
    VEP_ABCA3,
)


class StubService:
    """In-memory stand-in for SpliceService.

    Records calls and returns canned payloads. `score_error` / `resolve_error`
    can be set to an exception instance to simulate upstream faults.
    """

    def __init__(self) -> None:
        self.score_calls: list[dict[str, Any]] = []
        self.resolve_calls: list[dict[str, Any]] = []
        self.score_error: Exception | None = None
        self.resolve_error: Exception | None = None
        self.pangolin_error: Exception | None = None
        self.only_build: str | None = None  # when set, score() not_founds in the other build
        self._seen_keys: set[tuple[Any, ...]] = set()
        self.ref_bases: dict[str, str] = {}  # build -> base at the test locus
        self.refbase_calls: list[tuple[str, int, int, str]] = []
        self.overlap_count: int | None = 1  # default: a transcript overlaps (no fast-fail)
        self.overlap_calls: list[tuple[str, int, str, int]] = []
        self.nearest: dict[str, Any] | None = None  # canned nearest_transcript result
        self.nearest_calls: list[tuple[str, int, str]] = []

    async def score(self, *, model: str, build: str, variant_id: str, **kwargs: Any):
        from spliceailookup_link.services.telemetry import CallTelemetry

        self.score_calls.append(
            {"model": model, "build": build, "variant_id": variant_id, **kwargs}
        )
        if model == "pangolin" and self.pangolin_error is not None:
            raise self.pangolin_error
        if self.score_error is not None:
            raise self.score_error
        if self.only_build is not None and build != self.only_build:
            raise DataNotFoundError("no overlapping transcript")
        key = (
            model,
            build,
            variant_id,
            kwargs.get("distance"),
            kwargs.get("mask"),
            kwargs.get("gene_set"),
        )
        cache = "hit" if key in self._seen_keys else "miss"
        self._seen_keys.add(key)
        payload = PANGOLIN_TRAPPC9 if model == "pangolin" else SPLICEAI_TRAPPC9
        return payload, CallTelemetry(
            cache=cache,
            upstream_elapsed_ms=None if cache == "hit" else 7,
            cache_age_s=0 if cache == "hit" else None,
            cache_ttl_s=86400,
        )

    async def resolve(self, text: str, build: str) -> dict[str, Any]:
        self.resolve_calls.append({"text": text, "build": build})
        if self.resolve_error is not None:
            raise self.resolve_error
        if text.lower() == "rs6025":
            return {
                "variant_id": "1-169549811-C-A",
                "genome_build": build,
                "input_kind": "rsid",
                "source": "ensembl_vep",
                "gene_symbol": "F5",
                "consequence": "missense_variant",
                "ambiguous": True,
                "variant_ids": ["1-169549811-C-A", "1-169549811-C-T"],
                "note": "rs6025 maps to 2 alleles at this locus; pick one variant_id.",
                "raw_input": text,
            }
        # Coordinate inputs resolve locally; HGVS/rsID use the canned VEP record.
        from spliceailookup_link.variant import parse_variant_input

        parsed = parse_variant_input(text)
        if parsed.kind == "coordinate":
            return {
                "variant_id": parsed.value,
                "genome_build": build,
                "input_kind": "coordinate",
                "source": "direct",
                "raw_input": text,
            }
        rec = VEP_ABCA3[0]
        return {
            "variant_id": rec["vcf_string"],
            "genome_build": build,
            "input_kind": parsed.kind,
            "source": "ensembl_vep",
            "gene_symbol": "ABCA3",
            "consequence": rec["most_severe_consequence"],
            "raw_input": text,
        }

    async def reference_base(self, chrom: str, pos: int, length: int, build: str):
        self.refbase_calls.append((chrom, pos, length, build))
        return self.ref_bases.get(build)

    async def overlapping_transcripts(self, chrom: str, pos: int, build: str, window: int):
        self.overlap_calls.append((chrom, pos, build, window))
        return self.overlap_count

    async def nearest_transcript(self, chrom: str, pos: int, build: str, max_window: int = 100_000):
        self.nearest_calls.append((chrom, pos, build))
        return self.nearest

    async def warmup(self, build: str, mask: int = 0) -> dict[str, Any]:
        return {
            "spliceai": {"status": "ok", "elapsed_ms": 3},
            "pangolin": {"status": "ok", "elapsed_ms": 4},
        }

    async def close(self) -> None:  # pragma: no cover - lifecycle no-op
        return None


@pytest.fixture
def stub_service() -> StubService:
    return StubService()


@pytest.fixture
def mcp(stub_service: StubService):
    return create_spliceai_mcp(service_factory=lambda: stub_service)


def envelope(result: Any) -> dict[str, Any]:
    """Return the RAW structuredContent exactly as the tool returned it.

    Response-Envelope Standard v1: single-item success results are framed as
    ``{"success": true, "result": {...}, "_meta": {...}}``; collection tools
    (predict_splicing_batch) return ``{"success": true, "results": [...], ...}``
    directly; execution errors are the flat ``{"success": false, "error_code",
    ...}`` frame. Use this helper for envelope-SHAPE conformance assertions.
    Use ``structured()`` below for legacy field-level assertions that predate
    the ``result`` wrapper.
    """
    sc = getattr(result, "structured_content", None)
    if sc is None:
        sc = getattr(result, "data", None)
    if sc is None and isinstance(result, tuple):
        sc = result[-1]
    return sc or {}


def structured(result: Any) -> dict[str, Any]:
    """Extract + flatten the structured payload from a FastMCP call_tool result.

    Single-item success envelopes nest the domain payload under ``result``
    (Response-Envelope Standard v1 SS1); this transparently unwraps it (dropping
    the wrapper key, not merging it alongside a duplicate copy) so existing call
    sites can keep asserting domain fields at the top level next to
    ``success``/``_meta``. Collection envelopes (``results`` array + sibling
    domain keys) and error envelopes are already flat and pass through
    unchanged. Use ``envelope()`` to assert on the raw wire frame instead.
    """
    sc = envelope(result)
    inner = sc.get("result")
    if isinstance(inner, dict):
        flattened = dict(inner)
        flattened["success"] = sc.get("success", True)
        if "_meta" in sc:
            flattened["_meta"] = sc["_meta"]
        return flattened
    return sc


async def expect_tool_error(
    mcp: Any, name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call a tool expected to FAIL and return its structured error envelope.

    Response-Envelope Standard v1 SS2: execution errors are delivered IN-BAND --
    a normal tool result with MCP ``isError: true`` AND the flat error envelope
    (error_code, recovery, fallback_tool, next_commands, _meta, ...) present as
    ``structuredContent`` on that SAME result -- not raised as a bare
    ``fastmcp.exceptions.ToolError`` (that shape only carries a text message and
    drops structuredContent). This awaits the call directly, asserts
    ``is_error``, and returns the decoded envelope.

    NOTE: per-item batch failures are NOT delivered as isError -- they stay
    embedded in a SUCCESSFUL predict_splicing_batch envelope (one bad variant
    must not fail its siblings). Use ``structured()`` and read ``results[i]``
    for those.
    """
    result = await mcp.call_tool(name, arguments or {})
    assert getattr(result, "is_error", False) is True, (
        f"{name} did not signal is_error=True for a failing call"
    )
    return structured(result)


# Re-export so tests can build their own error scenarios.
__all__ = [
    "StubService",
    "structured",
    "envelope",
    "expect_tool_error",
    "DataNotFoundError",
]
