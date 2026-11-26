"""Response-Envelope v1.2: every tool result validates against the published JSON Schema.

The schema is vendored from https://github.com/berntpopp/genefoundry-envelope at tag v1.2.0.
The real SpliceService (with its cache) runs behind the MCP facade; only the two HTTP clients
are replaced by the recorded upstream payloads of tests/fixtures.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fastmcp import Client

from spliceailookup_link.mcp.facade import create_spliceai_mcp
from spliceailookup_link.services import SpliceService
from tests.fixtures.api_responses import PANGOLIN_TRAPPC9, SPLICEAI_TRAPPC9, VEP_ABCA3

SCHEMA_PATH = Path(__file__).parents[1] / "conformance" / "envelope-v1.2.schema.json"
SCHEMA_SHA256 = "c3c47e20d7dd189aac2568fc963b5514d7a5ac2c3e5f21d126f42c96d1ad1ae3"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

VARIANT = "chr8-140300616-T-G"
HGVS = "NM_001089.3(ABCA3):c.875A>T"
# Every data tool with the upstream it names; get_server_capabilities and warmup are
# ops/meta tools, outside the v1.2 provenance rule.
DATA_CALLS: list[tuple[str, dict[str, Any], str]] = [
    ("predict_spliceai", {"variant_id": VARIANT}, "spliceai"),
    ("predict_spliceai", {"variant_id": VARIANT, "response_mode": "minimal"}, "spliceai"),
    ("predict_spliceai", {"variant_id": VARIANT, "response_mode": "full"}, "spliceai"),
    ("predict_pangolin", {"variant_id": VARIANT}, "pangolin"),
    ("predict_splicing", {"variant_id": VARIANT}, "spliceai"),
    ("predict_splicing", {"variant_id": HGVS}, "spliceai"),
    ("predict_splicing_batch", {"variant_ids": [VARIANT, HGVS]}, "spliceai"),
    ("resolve_variant", {"variant_id": HGVS}, "ensembl_vep"),
    # No upstream call: still a success envelope, dated by the response.
    ("resolve_variant", {"variant_id": VARIANT}, "ensembl_vep"),
]
OPS_TOOLS = ["get_server_capabilities", "warmup"]
ERROR_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("predict_spliceai", {"variant_id": "not a variant"}),
    ("predict_spliceai", {"wrong_argument": 1}),
    ("no_such_tool", {}),
]


class _Scoring:
    async def score(self, *, model: str, **_: Any) -> dict[str, Any]:
        return PANGOLIN_TRAPPC9 if model == "pangolin" else SPLICEAI_TRAPPC9

    async def close(self) -> None:
        return None


class _Ensembl:
    async def resolve_hgvs(self, hgvs: str, build: str) -> dict[str, Any]:
        return VEP_ABCA3[0]

    resolve_id = resolve_hgvs

    async def reference_base(self, *_: Any) -> None:
        return None

    async def overlapping_transcripts(self, *_: Any) -> int:
        return 1

    async def nearest_transcript(self, *_: Any) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.fixture
def mcp() -> Any:
    service = SpliceService(scoring_client=_Scoring(), ensembl_client=_Ensembl())  # type: ignore[arg-type]
    return create_spliceai_mcp(service_factory=lambda: service)


async def _call(mcp: Any, tool: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    return result.structured_content, result.is_error


def test_vendored_schema_is_the_v1_2_0_release() -> None:
    assert hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest() == SCHEMA_SHA256


async def test_data_calls_cover_the_registered_data_tools(mcp: Any) -> None:
    registered = {tool.name for tool in await mcp.list_tools()}
    assert {name for name, _, _ in DATA_CALLS} == registered - set(OPS_TOOLS)


@pytest.mark.parametrize(("tool", "arguments", "source"), DATA_CALLS)
async def test_success_validates_and_carries_provenance(
    mcp: Any, tool: str, arguments: dict[str, Any], source: str
) -> None:
    envelope, is_error = await _call(mcp, tool, arguments)
    assert envelope["success"] is True and is_error is False
    jsonschema.validate(envelope, SCHEMA)
    meta = envelope["_meta"]
    assert meta["source"] == source
    assert meta["data_version"] == "live"
    assert meta["retrieved_at"].endswith("Z")


@pytest.mark.parametrize(("tool", "arguments"), [*ERROR_CALLS, *((t, {}) for t in OPS_TOOLS)])
async def test_errors_and_ops_tools_carry_the_v1_meta_core(
    mcp: Any, tool: str, arguments: dict[str, Any]
) -> None:
    envelope, is_error = await _call(mcp, tool, arguments)
    assert is_error == (not envelope["success"])
    assert isinstance(envelope["_meta"]["request_id"], str)
    assert isinstance(envelope["_meta"]["elapsed_ms"], int)
    if not envelope["success"]:
        jsonschema.validate(envelope, SCHEMA)


async def test_cache_hit_keeps_the_original_fetch_time(
    mcp: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter(f"2026-01-0{day}T00:00:00Z" for day in range(1, 9))
    monkeypatch.setattr("spliceailookup_link.services.splice_service.utc_now", lambda: next(clock))
    first, _ = await _call(mcp, "predict_spliceai", {"variant_id": VARIANT})
    hit, _ = await _call(mcp, "predict_spliceai", {"variant_id": VARIANT})
    other, _ = await _call(mcp, "predict_pangolin", {"variant_id": VARIANT})
    assert first["_meta"]["retrieved_at"] == hit["_meta"]["retrieved_at"] == "2026-01-01T00:00:00Z"
    # A later call does not inherit an earlier call's (older) fetch time.
    assert other["_meta"]["retrieved_at"] == "2026-01-02T00:00:00Z"
    # Several fetches behind one call (both models, both cached): the oldest dates it.
    both, _ = await _call(mcp, "predict_splicing", {"variant_id": VARIANT})
    assert both["_meta"]["cache"] == "hit"
    assert both["_meta"]["retrieved_at"] == "2026-01-01T00:00:00Z"
