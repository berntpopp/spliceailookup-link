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


def structured(result: Any) -> dict[str, Any]:
    """Extract the structured payload from a FastMCP call_tool result."""
    sc = getattr(result, "structured_content", None)
    if sc is None:
        sc = getattr(result, "data", None)
    if sc is None and isinstance(result, tuple):
        sc = result[-1]
    return sc or {}


# Re-export so tests can build their own error scenarios.
__all__ = ["StubService", "structured", "DataNotFoundError"]
