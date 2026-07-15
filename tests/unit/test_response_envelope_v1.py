"""Response-Envelope Standard v1 conformance.

Locks the ratified fleet contract (docs/RESPONSE-ENVELOPE-STANDARD-v1.md in the
genefoundry-router-standards repo) for spliceailookup-link:

- Success: single-item tools frame the domain payload under ``result``;
  predict_splicing_batch (a collection tool) frames it under ``results`` plus
  sibling domain keys. Both carry a top-level ``success`` and ``_meta``.
- Execution errors are delivered IN-BAND: MCP ``isError: true`` AND the flat
  error frame (``error_code``, ``message``, ``retryable``, ``recovery_action``)
  as ``structuredContent`` on that same result -- not a raised ToolError.
- ``_meta.unsafe_for_clinical_use`` is present on every result (success and
  error).
- Long-running prediction tools declare protocol-level
  ``execution.taskSupport`` (SEP-1686 / MCP 2025-11-25) AND type their cold
  latency into ``_meta`` (``cost_tier`` / ``expected_cold_latency_ms``).
"""

from __future__ import annotations

from tests.conftest import StubService, envelope, expect_tool_error

_SINGLE_ITEM_TOOLS = (
    "predict_spliceai",
    "predict_pangolin",
    "predict_splicing",
    "resolve_variant",
    "get_server_capabilities",
    "warmup",
)

_LONG_RUNNING_TOOLS = (
    "predict_spliceai",
    "predict_pangolin",
    "predict_splicing",
    "predict_splicing_batch",
)


async def test_single_item_success_is_framed_under_result(mcp) -> None:
    for name, args in (
        ("predict_spliceai", {"variant_id": "chr8-140300616-T-G"}),
        ("predict_pangolin", {"variant_id": "chr8-140300616-T-G"}),
        ("predict_splicing", {"variant_id": "chr8-140300616-T-G"}),
        ("resolve_variant", {"variant_id": "chr8-140300616-T-G"}),
        ("get_server_capabilities", {}),
        ("warmup", {}),
    ):
        raw = envelope(await mcp.call_tool(name, args))
        assert raw["success"] is True, f"{name}: success flag missing/false"
        assert isinstance(raw.get("result"), dict), f"{name}: no `result` object in frame"
        assert "results" not in raw, f"{name}: single-item tool must not carry `results`"
        assert "_meta" in raw, f"{name}: no `_meta` block"


async def test_batch_success_is_framed_under_results_array() -> None:
    mcp = None
    from spliceailookup_link.mcp.facade import create_spliceai_mcp

    mcp = create_spliceai_mcp(service_factory=lambda: StubService())
    raw = envelope(
        await mcp.call_tool("predict_splicing_batch", {"variant_ids": ["chr8-140300616-T-G"]})
    )
    assert raw["success"] is True
    assert isinstance(raw.get("results"), list), "batch: no `results` array in frame"
    assert "result" not in raw, "batch: collection tool must not carry singular `result`"
    assert "_meta" in raw


async def test_error_is_inband_iserror_true_with_flat_structured_content(mcp) -> None:
    result = await mcp.call_tool("predict_spliceai", {"variant_id": "chr99-1000-A-G"})
    assert result.is_error is True
    payload = result.structured_content
    assert payload is not None, "error result dropped structuredContent"
    assert payload["success"] is False
    assert payload["error_code"] == "invalid_input"
    assert payload["error_subtype"] == "unsupported_contig"
    assert "message" in payload
    assert "retryable" in payload
    assert "recovery_action" in payload
    assert "result" not in payload
    assert "results" not in payload


async def test_error_envelope_carries_unsafe_for_clinical_use(mcp) -> None:
    data = await expect_tool_error(mcp, "predict_spliceai", {"variant_id": "chr99-1000-A-G"})
    assert data["_meta"]["unsafe_for_clinical_use"] is True


async def test_success_envelope_carries_unsafe_for_clinical_use(mcp) -> None:
    for name, args in (
        ("predict_spliceai", {"variant_id": "chr8-140300616-T-G"}),
        ("predict_splicing_batch", {"variant_ids": ["chr8-140300616-T-G"]}),
    ):
        raw = envelope(await mcp.call_tool(name, args))
        assert raw["_meta"]["unsafe_for_clinical_use"] is True, name


async def test_long_running_tools_declare_task_support_optional(mcp) -> None:
    # SEP-1686 / MCP 2025-11-25 execution.taskSupport, verified against the
    # installed fastmcp 3.4.2 (Tool.task_config.mode -> ToolExecution(taskSupport=...)).
    for name in _LONG_RUNNING_TOOLS:
        tool = await mcp.get_tool(name)
        assert tool.task_config is not None, f"{name}: no task_config"
        assert tool.task_config.mode == "optional", f"{name}: taskSupport != optional"
        mcp_tool = tool.to_mcp_tool()
        assert mcp_tool.execution is not None, f"{name}: no wire-level `execution` block"
        assert mcp_tool.execution.taskSupport == "optional", (
            f"{name}: wire-level execution.taskSupport != optional"
        )


async def test_long_running_tools_type_cold_latency_in_meta(mcp) -> None:
    for name, args in (
        ("predict_spliceai", {"variant_id": "chr8-140300616-T-G"}),
        ("predict_pangolin", {"variant_id": "chr8-140300616-T-G"}),
        ("predict_splicing", {"variant_id": "chr8-140300616-T-G"}),
        ("predict_splicing_batch", {"variant_ids": ["chr8-140300616-T-G"]}),
    ):
        raw = envelope(await mcp.call_tool(name, args))
        meta = raw["_meta"]
        assert meta.get("cost_tier") in {"low", "medium", "high"}, f"{name}: no cost_tier"
        assert isinstance(meta.get("expected_cold_latency_ms"), int), (
            f"{name}: expected_cold_latency_ms missing/not typed"
        )
        assert meta["expected_cold_latency_ms"] > 0, name


async def test_fast_tools_do_not_carry_latency_hint(mcp) -> None:
    # resolve_variant / get_server_capabilities / warmup are sub-second-to-a-few-second
    # calls, not the ~60s SpliceAI/Pangolin compute path -- no cost_tier noise.
    for name, args in (
        ("resolve_variant", {"variant_id": "chr8-140300616-T-G"}),
        ("get_server_capabilities", {}),
        ("warmup", {}),
    ):
        raw = envelope(await mcp.call_tool(name, args))
        assert "cost_tier" not in raw["_meta"], name
