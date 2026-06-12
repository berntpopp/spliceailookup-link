"""Regression tests for docs/mcp-evaluation.md Part 4 (F6-F10 + #2/#4/#5)."""

from __future__ import annotations

import json

from tests.conftest import structured


async def test_f6_headline_matches_verdict_concordant_high(mcp) -> None:
    # Stub returns SpliceAI 0.83 / Pangolin 0.85 -> concordant_high.
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert data["agreement"]["verdict"] == "concordant_high"
    assert "models agree" in data["headline"]
    assert "models disagree" not in data["headline"]


async def test_interpretation_band_on_combined(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert data["interpretation"]["band"] == "high"
    # F6: the static threshold_basis glossary is full-only now; the band stays in compact.
    assert "threshold_basis" not in data["interpretation"]


async def test_cache_ttl_and_age_in_meta(mcp) -> None:
    first = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    assert first["_meta"]["cache"] == "miss"
    assert first["_meta"]["cache_ttl_s"] == 86400
    assert "cache_age_s" not in first["_meta"]
    second = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    assert second["_meta"]["cache"] == "hit"
    assert second["_meta"]["cache_age_s"] == 0
    assert second["_meta"]["cache_ttl_s"] == 86400


async def test_f8_combined_minimal_is_headline_tier(mcp) -> None:
    full = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "compact"}
        )
    )
    minimal = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "minimal"}
        )
    )
    assert len(json.dumps(minimal)) < len(json.dumps(full))
    assert "spliceai" not in minimal and "pangolin" not in minimal
    assert minimal["agreement"]["verdict"] == "concordant_high"
    # F3: per-model maxes live in agreement{} under the same names as compact/full.
    assert minimal["agreement"]["spliceai_max_delta"] == 0.83
    assert minimal["agreement"]["pangolin_max_delta"] == 0.85
    assert "spliceai_max" not in minimal and "pangolin_max" not in minimal
    assert minimal["interpretation"]["band"] == "high"
    assert "TRAPPC9" in minimal["headline"]


async def test_f9_validation_failed_has_request_id_and_timing(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "8-140300616-T-G", "max_distance": 20000}
        )
    )
    assert data["success"] is False
    assert data["error_code"] == "validation_failed"
    meta = data["_meta"]
    assert isinstance(meta["request_id"], str) and len(meta["request_id"]) == 12
    assert isinstance(meta["timing"]["elapsed_ms"], int)
    assert data["field_errors"]


async def test_capabilities_advertises_background_execution(mcp) -> None:
    data = structured(await mcp.call_tool("get_server_capabilities", {}))
    bg = data["background_execution"]
    assert set(bg["task_eligible_tools"]) == {
        "predict_spliceai",
        "predict_pangolin",
        "predict_splicing",
        "predict_splicing_batch",
    }
    assert bg["task_support"] == "optional"


async def test_task_tool_descriptions_mention_background(mcp) -> None:
    for name in ("predict_splicing", "predict_spliceai", "predict_pangolin"):
        tool = await mcp.get_tool(name)
        assert "background task" in tool.description.lower()


async def test_capabilities_documents_new_contracts(mcp) -> None:
    data = structured(await mcp.call_tool("get_server_capabilities", {}))
    blob = json.dumps(data).lower()
    assert "concordant_moderate" in blob
    assert "shared_by" in blob
    assert "minimal" in blob and "compact" in blob and "full" in blob


# ===== G1: uniprot-link in see_also =====


async def test_g1_see_also_includes_uniprot_full(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "8-140300616-T-G", "response_mode": "full"}
        )
    )
    servers = {h["server"] for h in data["_meta"]["see_also"]}
    assert "uniprot-link" in servers
    uni = next(h for h in data["_meta"]["see_also"] if h["server"] == "uniprot-link")
    assert uni["example"]["tool"] == "find_proteins"
    assert uni["example"]["arguments"]["gene"] == "TRAPPC9"


async def test_g1_see_also_uniprot_collapsed_in_compact(mcp) -> None:
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    servers = {h["server"] for h in data["_meta"]["see_also"]}
    assert "uniprot-link" in servers
    uni = next(h for h in data["_meta"]["see_also"] if h["server"] == "uniprot-link")
    assert set(uni) == {"server", "hint"}  # compact collapses, no example


# ===== G2: molecular_consequence =====


async def test_g2_combined_molecular_consequence_and_headline(mcp) -> None:
    data = structured(
        await mcp.call_tool("predict_splicing", {"variant": "NM_001089.3(ABCA3):c.875A>T"})
    )
    assert data["molecular_consequence"] == "missense_variant"
    assert "missense variant" in data["headline"]


async def test_g2_coordinate_has_no_molecular_consequence(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert "molecular_consequence" not in data
    assert "missense variant" not in data["headline"]


async def test_g2_single_model_molecular_consequence_field(mcp) -> None:
    data = structured(
        await mcp.call_tool("predict_spliceai", {"variant": "NM_001089.3(ABCA3):c.875A>T"})
    )
    assert data["molecular_consequence"] == "missense_variant"


async def test_g2_combined_minimal_keeps_molecular_consequence(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_splicing",
            {"variant": "NM_001089.3(ABCA3):c.875A>T", "response_mode": "minimal"},
        )
    )
    assert data["molecular_consequence"] == "missense_variant"


# ===== Step 9: capabilities docs =====


async def test_capabilities_documents_uniprot_and_molecular_consequence(mcp) -> None:
    data = structured(await mcp.call_tool("get_server_capabilities", {}))
    blob = json.dumps(data).lower()
    assert "uniprot-link" in blob
    assert "molecular_consequence" in blob


# ===== FIX 1 regression: minimal single-model must not crash =====


async def test_minimal_single_model_does_not_crash(mcp) -> None:
    for tool in ("predict_spliceai", "predict_pangolin"):
        data = structured(
            await mcp.call_tool(tool, {"variant": "8-140300616-T-G", "response_mode": "minimal"})
        )
        assert data.get("success") is True
        assert "error_code" not in data
        assert "headline" in data
        assert data["max_delta_score"] is not None


# ===== FIX 2 regression: validation envelope must carry unsafe_for_clinical_use =====


async def test_f9_validation_envelope_carries_provenance(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "8-140300616-T-G", "max_distance": 20000}
        )
    )
    assert data["error_code"] == "validation_failed"
    assert data["_meta"]["unsafe_for_clinical_use"] is True
