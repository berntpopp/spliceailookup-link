"""Regression tests for docs/mcp-evaluation.md Part 7 (F11-F17 + #C1) and the
§8 durability invariants."""

from __future__ import annotations

import json

from spliceailookup_link.mcp.shaping import THRESHOLD_BASIS, shape_spliceai
from tests.conftest import structured
from tests.fixtures.api_responses import (
    SPLICEAI_MASKED_EMPTY_ABERR,
    SPLICEAI_MASKED_NO_EFFECT,
    SPLICEAI_TRAPPC9,
)


async def test_f13_threshold_basis_emitted_once_in_combined(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    # Exactly one threshold_basis in the whole combined payload (top-level only).
    assert json.dumps(data).count("threshold_basis") == 1
    assert data["interpretation"]["threshold_basis"] == THRESHOLD_BASIS
    # Each model sub-block keeps its decision-relevant band but drops the static string.
    assert "band" in data["spliceai"]["interpretation"]
    assert "threshold_basis" not in data["spliceai"]["interpretation"]
    assert "band" in data["pangolin"]["interpretation"]
    assert "threshold_basis" not in data["pangolin"]["interpretation"]


async def test_f13_single_model_still_has_one_threshold_basis(mcp) -> None:
    # Standalone single-model tools are self-contained: they keep their one copy.
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    assert data["interpretation"]["threshold_basis"] == THRESHOLD_BASIS
    assert json.dumps(data).count("threshold_basis") == 1


def test_f14_populated_aberration_fields_are_kept() -> None:
    shaped = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="full")
    ab = shaped["consequence"]["aberrations"][0]
    # The fixture populates these -> they must survive.
    assert ab["type"] == "exon_skipping"
    assert ab["status"] == "frameshift"
    assert ab["size_is_coding"] is True
    assert ab["introduces_stop_codon"] is True


def test_f14_null_aberration_fields_are_omitted_not_null() -> None:
    sparse = {
        **SPLICEAI_TRAPPC9,
        "sai10kPredictions": {
            "aberrations": [
                {
                    "aberration_type": "exon_skipping",
                    "affected_region": {"region_type": "intron"},
                    "status": None,
                    "size_is_coding": None,
                    "introduces_stop_codon": None,
                }
            ]
        },
    }
    shaped = shape_spliceai(sparse, response_mode="full")
    ab = shaped["consequence"]["aberrations"][0]
    assert ab["type"] == "exon_skipping"
    assert "status" not in ab  # omitted, not null
    assert "size_is_coding" not in ab
    assert "introduces_stop_codon" not in ab


def test_f14_falsy_aberration_fields_are_kept_not_omitted() -> None:
    payload = {
        **SPLICEAI_TRAPPC9,
        "sai10kPredictions": {
            "aberrations": [
                {
                    "aberration_type": "intron_retention",
                    "size_is_coding": False,
                    "introduces_stop_codon": False,
                }
            ]
        },
    }
    shaped = shape_spliceai(payload, response_mode="full")
    ab = shaped["consequence"]["aberrations"][0]
    assert ab["size_is_coding"] is False  # falsy but not None -> kept
    assert ab["introduces_stop_codon"] is False
    assert "status" not in ab  # genuinely absent -> omitted


def test_f15_masked_suppression_note_fires_on_real_signal() -> None:
    shaped = shape_spliceai(SPLICEAI_MASKED_EMPTY_ABERR, response_mode="full")
    cons = shaped["consequence"]
    assert cons["aberrations"] == []
    assert "note" in cons
    assert "mask='raw'" in cons["note"]


def test_f15_no_note_on_no_effect_masked_variant() -> None:
    shaped = shape_spliceai(SPLICEAI_MASKED_NO_EFFECT, response_mode="full")
    cons = shaped.get("consequence")
    # Either no consequence object, or one without a note -- never a misleading note.
    assert not (cons and cons.get("note"))


def test_f15_no_note_in_raw_mode() -> None:
    raw = {**SPLICEAI_MASKED_EMPTY_ABERR, "mask": 0}
    shaped = shape_spliceai(raw, response_mode="full")
    cons = shaped.get("consequence") or {}
    assert "note" not in cons


async def test_f15_combined_masked_does_not_crash(mcp) -> None:
    data = structured(
        await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G", "mask": "masked"})
    )
    assert data["success"] is True
