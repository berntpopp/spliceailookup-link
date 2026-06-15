"""Runtime observability in _meta."""

from __future__ import annotations

from spliceailookup_link.services.telemetry import CallTelemetry, is_served_warm
from tests.conftest import structured


def test_is_served_warm_cache_hit() -> None:
    assert is_served_warm("hit", None, 5000) is True


def test_is_served_warm_fast_miss() -> None:
    assert is_served_warm("miss", 800, 5000) is True


def test_is_served_warm_cold_miss() -> None:
    assert is_served_warm("miss", 20000, 5000) is False


def test_is_served_warm_unknown_miss() -> None:
    # No upstream timing recorded and not a hit -> conservatively not warm.
    assert is_served_warm("partial", None, 5000) is False


def test_call_telemetry_served_warm_uses_default_threshold() -> None:
    assert CallTelemetry(cache="hit").served_warm() is True
    assert CallTelemetry(cache="miss", upstream_elapsed_ms=20000).served_warm() is False


async def test_spliceai_meta_reports_cache_miss_then_hit(mcp) -> None:
    first = structured(
        await mcp.call_tool("predict_spliceai", {"variant_id": "chr8-140300616-T-G"})
    )
    second = structured(
        await mcp.call_tool("predict_spliceai", {"variant_id": "chr8-140300616-T-G"})
    )
    assert first["_meta"]["cache"] == "miss"
    assert "upstream_elapsed_ms" in first["_meta"]
    assert second["_meta"]["cache"] == "hit"


async def test_combined_meta_cache_present(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant_id": "chr8-140300616-T-G"}))
    assert data["_meta"]["cache"] in {"hit", "miss", "partial"}
