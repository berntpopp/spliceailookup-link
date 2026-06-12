"""Regression tests for docs/mcp-evaluation.md Part 7 (F11-F17 + #C1) and the
§8 durability invariants."""

from __future__ import annotations

import json

from spliceailookup_link.api import DataNotFoundError, RateLimitedError
from spliceailookup_link.config import settings
from spliceailookup_link.mcp.shaping import THRESHOLD_BASIS, shape_spliceai
from tests.conftest import StubService, structured
from tests.fixtures.api_responses import (
    SPLICEAI_MASKED_EMPTY_ABERR,
    SPLICEAI_MASKED_NO_EFFECT,
    SPLICEAI_TRAPPC9,
)


async def test_f13_threshold_basis_emitted_once_in_combined(mcp) -> None:
    # F6: threshold_basis is full-only; even in full it appears exactly once (top-level).
    data = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
        )
    )
    assert json.dumps(data).count("threshold_basis") == 1
    assert data["interpretation"]["threshold_basis"] == THRESHOLD_BASIS
    # Each model sub-block keeps its decision-relevant band but drops the static string.
    assert "band" in data["spliceai"]["interpretation"]
    assert "threshold_basis" not in data["spliceai"]["interpretation"]
    assert "band" in data["pangolin"]["interpretation"]
    assert "threshold_basis" not in data["pangolin"]["interpretation"]


async def test_f13_single_model_still_has_one_threshold_basis(mcp) -> None:
    # F6: standalone single-model carries threshold_basis only in full mode (exactly once).
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
        )
    )
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


_RECOVERY_KEYS = (
    "error_code",
    "message",
    "retryable",
    "recovery_action",
    "fallback_tool",
    "fallback_args",
    "recovery",
    "next_commands",
)


async def test_f11_batch_error_item_has_full_scaffold(mcp, stub_service: StubService) -> None:
    stub_service.score_error = DataNotFoundError("no overlap")
    res = await mcp.call_tool("predict_splicing_batch", {"variants": ["1-1-A-T"]})
    data = structured(res)
    assert data["success"] is True
    assert data["summary"]["failed"] == 1
    item = data["results"][0]
    for key in _RECOVERY_KEYS:
        assert key in item, f"batch error item missing {key}"
    assert item["error_code"] == "not_found"
    assert item["next_commands"][0]["tool"] == "resolve_variant"
    assert item["next_commands"][0]["arguments"]["variant"] == "1-1-A-T"


async def test_f11_batch_error_matches_standalone(mcp, stub_service: StubService) -> None:
    stub_service.score_error = DataNotFoundError("no overlap")
    standalone = structured(await mcp.call_tool("predict_splicing", {"variant": "1-1-A-T"}))
    batch = structured(await mcp.call_tool("predict_splicing_batch", {"variants": ["1-1-A-T"]}))
    item = batch["results"][0]
    for key in ("error_code", "retryable", "recovery_action", "fallback_tool", "recovery"):
        assert item[key] == standalone[key], f"scaffold mismatch on {key}"
    assert item["next_commands"] == standalone["_meta"]["next_commands"]


async def test_f12_batch_items_carry_slim_meta(mcp) -> None:
    # Same string twice: W2 dedup scores it once (item 0 misses upstream) and
    # serves item 1 from the first result (cache == "deduped", no upstream call).
    res = await mcp.call_tool(
        "predict_splicing_batch",
        {"variants": ["8-140300616-T-G", "8-140300616-T-G"]},
    )
    data = structured(res)
    first, second = data["results"][0], data["results"][1]
    assert first["_meta"]["cache"] == "miss"
    assert second["_meta"]["cache"] == "deduped"
    assert second["_meta"]["served_from"] == "8-140300616-T-G"
    assert first["_meta"]["upstream_elapsed_ms"] is not None
    # Deduped copy made no upstream call -> field omitted, not null (omit-when-null).
    assert "upstream_elapsed_ms" not in second["_meta"]
    # Slim only: the verbose fields stay out of per-item _meta.
    assert "gene" not in first["_meta"]
    assert "resolution" not in first["_meta"]
    # Aggregate envelope _meta is unchanged (next_commands present).
    assert data["_meta"]["next_commands"][0]["tool"] == "predict_splicing"


async def test_c1_rate_limited_carries_concurrency_budget(mcp, stub_service: StubService) -> None:
    stub_service.score_error = RateLimitedError("Local concurrency limit saturated")
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    assert data["error_code"] == "rate_limited"
    budget = data["_meta"]["rate_budget"]
    assert budget["limit"] == settings.MAX_CONCURRENCY
    assert budget["remaining"] == 0
    assert budget["unit"] == "concurrent_requests"
    assert "window_s" not in budget  # never fabricate a window we don't enforce


async def test_c1_success_envelope_rate_budget_is_proactive(mcp) -> None:
    # P1#2: success now carries a proactive pacing budget (min_interval_ms) but no
    # fabricated remaining/retry_after_s -- those appear only on a rate_limited error.
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    assert data["success"] is True
    rb = data["_meta"]["rate_budget"]
    assert rb["min_interval_ms"] == 12000
    assert "remaining" not in rb
    assert "retry_after_s" not in rb


async def test_f16_resolve_description_states_ref_check_contract(mcp) -> None:
    # v0.8.0 (D1): resolve now checks the REF by default and warns on mismatch,
    # rather than silently passing a wrong REF. The docstring must say so.
    tools = await mcp.list_tools()
    desc = next(t.description for t in tools if t.name == "resolve_variant")
    low = desc.lower()
    assert "normalized" in low
    assert "ref_warning" in low or "ref base is also checked" in low
    assert "not validated" not in low  # the old, now-incorrect wording is gone


async def test_f17_descriptions_disambiguate_one_vs_both(mcp) -> None:
    tools = {t.name: t.description for t in await mcp.list_tools()}
    assert "BOTH models" in tools["predict_splicing"]
    assert "ONE model" in tools["predict_spliceai"]
    assert "ONE model" in tools["predict_pangolin"]


# ---------------- §8 durability invariants ----------------


async def test_inv_batch_item_matches_single_call(mcp) -> None:
    """§8.1 success parity: a batch item == standalone result minus outer envelope."""
    single = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    batch = structured(
        await mcp.call_tool("predict_splicing_batch", {"variants": ["chr8-140300616-T-G"]})
    )
    item = batch["results"][0]
    shared = ("agreement", "interpretation", "consequence", "transcript", "headline")
    for key in shared:
        if key in single:
            assert item.get(key) == single[key], f"batch/single divergence on {key}"
    if "molecular_consequence" in single:
        assert item.get("molecular_consequence") == single["molecular_consequence"]


async def test_inv_cross_tool_error_envelope_parity(mcp, stub_service: StubService) -> None:
    """§8.2: every resolve/predict tool emits the same error key set."""
    stub_service.score_error = DataNotFoundError("no overlap")
    stub_service.resolve_error = DataNotFoundError("no overlap")
    required = {
        "error_code",
        "message",
        "retryable",
        "recovery_action",
        "fallback_tool",
        "fallback_args",
        "recovery",
    }
    for tool in ("predict_spliceai", "predict_pangolin", "predict_splicing", "resolve_variant"):
        data = structured(await mcp.call_tool(tool, {"variant": "8-140300616-T-G"}))
        assert data["success"] is False
        assert required <= set(data), f"{tool} dropped error keys: {required - set(data)}"
        assert "next_commands" in data["_meta"]
    batch = structured(
        await mcp.call_tool("predict_splicing_batch", {"variants": ["8-140300616-T-G"]})
    )
    item = batch["results"][0]
    assert (required - {"message"}) <= set(item)


async def test_inv_no_duplicated_threshold_basis(mcp) -> None:
    """§8.3: the static THRESHOLD_BASIS string appears at most once per payload."""
    combined = structured(
        await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"})
    )
    assert json.dumps(combined).count(THRESHOLD_BASIS) <= 1
    batch = structured(
        await mcp.call_tool(
            "predict_splicing_batch",
            {"variants": ["chr8-140300616-T-G", "8-140300616-T-G"]},
        )
    )
    for item in batch["results"]:
        assert json.dumps(item).count(THRESHOLD_BASIS) <= 1


async def test_inv_no_null_leaf_in_full_mode(mcp) -> None:
    """§8.4: full-mode payloads omit-when-null rather than ship null leaves."""

    def walk(node: object, path: str = "") -> list[str]:
        bad: list[str] = []
        if isinstance(node, dict):
            for k, v in node.items():
                bad += walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                bad += walk(v, f"{path}[{i}]")
        elif node is None:
            bad.append(path)
        return bad

    data = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
        )
    )
    nulls = [
        p
        for p in walk(data)
        if p.rsplit(".", 1)[-1]
        not in {
            "fallback_tool",
            "fallback_args",
            "cache_age_s",
            "upstream_elapsed_ms",
            "signed_score",
        }
    ]
    assert nulls == [], f"unexpected null leaves in full mode: {nulls}"


async def test_caps_document_new_contracts(mcp) -> None:
    caps = structured(await mcp.call_tool("get_server_capabilities", {}))
    blob = json.dumps(caps).lower()
    # F17 which-tool guidance
    assert "which tool" in blob or ("both models" in blob and "one model" in blob)
    # F16 caveat (updated: now mentions ref_mismatch detection)
    assert "normalized, not deeply validated" in blob or "ref_mismatch" in blob
    # #C1 concurrency unit (never a fabricated window)
    assert "concurrent_requests" in blob
    assert "window_s" not in blob
    # F14 aberration sub-field note
    assert "size_is_coding" in blob


async def test_caps_version_changes_and_is_stable(mcp) -> None:
    a = structured(await mcp.call_tool("get_server_capabilities", {}))
    b = structured(await mcp.call_tool("get_server_capabilities", {}))
    assert a["capabilities_version"] == b["capabilities_version"]  # stable
    assert isinstance(a["capabilities_version"], str) and len(a["capabilities_version"]) >= 8


async def test_inv_no_null_leaf_in_batch_item(mcp) -> None:
    """§8.4 (batch): full-mode batch items omit-when-null, same as single calls."""

    def walk(node: object, path: str = "") -> list[str]:
        bad: list[str] = []
        if isinstance(node, dict):
            for k, v in node.items():
                bad += walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                bad += walk(v, f"{path}[{i}]")
        elif node is None:
            bad.append(path)
        return bad

    data = structured(
        await mcp.call_tool(
            "predict_splicing_batch",
            {"variants": ["chr8-140300616-T-G"], "response_mode": "full"},
        )
    )
    item = data["results"][0]
    nulls = [
        p
        for p in walk(item)
        if p.rsplit(".", 1)[-1] not in {"cache_age_s", "upstream_elapsed_ms", "signed_score"}
    ]
    assert nulls == [], f"unexpected null leaves in batch item: {nulls}"
