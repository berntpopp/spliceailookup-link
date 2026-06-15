"""Tool-name & argument compliance with the GeneFoundry Tool-Naming Standard v1.

Every registered tool must be unprefixed, snake_case, <= 50 chars, and start with
a canonical verb so it composes cleanly behind the ``genefoundry-router`` gateway,
which mounts this server under the ``spliceai`` namespace (tools surface as
``spliceai_<tool>``). Guards against future drift. See issue
berntpopp/spliceailookup-link#2.

Per issue #2 (resolution A), ``predict`` is part of this server's canonical verb
set: splice scoring is ML inference, which the base verb list does not otherwise
cover. ``ops``-tagged side-effecting utilities (e.g. ``warmup``) are exempt from
the verb rule per the documented fleet ops carve-out, but still must match the
name charset/length and must not self-prefix the namespace token.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

_NAME_RE = re.compile(r"^[a-z0-9_]{1,50}$")
# Base fleet verbs + ``predict`` (issue #2, resolution A: ML inference verb).
_CANONICAL_VERBS = frozenset(
    {"get", "search", "list", "resolve", "find", "compare", "compute", "predict"}
)
_NAMESPACE = "spliceai"

# Fleet-canon argument names that supersede local synonyms (issue #2, Rule 4).
# A variant identifier is ``variant_id`` (singular) / ``variant_ids`` (batch);
# the local ``variant`` / ``variants`` synonyms are forbidden.
_FORBIDDEN_ARGS = frozenset({"variant", "variants"})


async def test_tool_names_conform_to_standard_v1(mcp: Any) -> None:
    tools = await mcp.list_tools()
    assert tools, "no tools registered on the facade"
    for tool in tools:
        name = tool.name
        tags = set(tool.tags or ())
        assert _NAME_RE.match(name), f"{name!r} must match ^[a-z0-9_]{{1,50}}$"
        assert not name.startswith(f"{_NAMESPACE}_"), (
            f"{name!r} must not self-prefix the '{_NAMESPACE}' namespace "
            "token — the gateway adds it"
        )
        # ``ops`` utilities (warmup/health) are exempt from the verb rule.
        if "ops" in tags:
            continue
        assert name.split("_", 1)[0] in _CANONICAL_VERBS, (
            f"{name!r} must start with a canonical verb {sorted(_CANONICAL_VERBS)}"
        )


async def test_tool_arguments_use_fleet_canon(mcp: Any) -> None:
    tools = await mcp.list_tools()
    assert tools, "no tools registered on the facade"
    for tool in tools:
        props = set((tool.parameters or {}).get("properties", {}))
        leaked = props & _FORBIDDEN_ARGS
        assert not leaked, (
            f"{tool.name!r} exposes non-canonical argument(s) {sorted(leaked)}; "
            "use 'variant_id' / 'variant_ids' per the fleet canon (issue #2, Rule 4)"
        )


@pytest.mark.parametrize(
    "tool_name",
    ["predict_splicing", "predict_spliceai", "predict_pangolin", "resolve_variant"],
)
async def test_singular_predict_tools_expose_variant_id(mcp: Any, tool_name: str) -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    props = set((tools[tool_name].parameters or {}).get("properties", {}))
    assert "variant_id" in props, f"{tool_name!r} must expose canonical 'variant_id'"


async def test_batch_tool_exposes_variant_ids(mcp: Any) -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    props = set((tools["predict_splicing_batch"].parameters or {}).get("properties", {}))
    assert "variant_ids" in props, "predict_splicing_batch must expose canonical 'variant_ids'"


@pytest.mark.parametrize(
    "tool_name",
    ["predict_splicing", "predict_spliceai", "predict_pangolin", "predict_splicing_batch"],
)
async def test_response_mode_enum_includes_standard(mcp: Any, tool_name: str) -> None:
    tools = {t.name: t for t in await mcp.list_tools()}
    rm = (tools[tool_name].parameters or {}).get("properties", {}).get("response_mode", {})
    enum = set(rm.get("enum", []))
    assert {"minimal", "compact", "standard", "full"} <= enum, (
        f"{tool_name!r} response_mode enum must include the fleet ladder "
        f"minimal|compact|standard|full; got {sorted(enum)}"
    )
