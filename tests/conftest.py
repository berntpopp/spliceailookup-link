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

    async def score(
        self, *, model: str, build: str, variant_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        self.score_calls.append(
            {"model": model, "build": build, "variant_id": variant_id, **kwargs}
        )
        if model == "pangolin" and self.pangolin_error is not None:
            raise self.pangolin_error
        if self.score_error is not None:
            raise self.score_error
        return PANGOLIN_TRAPPC9 if model == "pangolin" else SPLICEAI_TRAPPC9

    async def resolve(self, text: str, build: str) -> dict[str, Any]:
        self.resolve_calls.append({"text": text, "build": build})
        if self.resolve_error is not None:
            raise self.resolve_error
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
