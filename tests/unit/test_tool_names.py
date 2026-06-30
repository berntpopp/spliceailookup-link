"""Tool-name & argument compliance with the GeneFoundry Tool-Naming Standard v1.1.

Every registered tool must be unprefixed, snake_case, <= 50 chars, and start with
a canonical verb so it composes cleanly behind the ``genefoundry-router`` gateway,
which mounts this server under the ``spliceai`` namespace (tools surface as
``spliceai_<tool>``). Guards against future drift. See issue
berntpopp/spliceailookup-link#2.

VERB CANON (ratified Standard v1.1, 2026-06-30)
------------------------------------------------
Tier-1 (universal read/query, all backends):
    get, search, list, resolve, find, compare, compute, map

Tier-2 (sanctioned domain action/compute verbs):
    predict, annotate, recode, liftover, analyze, score,
    submit, export, generate, download

``predict`` is now ratified Tier-2 (ML inference verb, fleet-wide decision).
Prior local extension "issue #2 resolution A" is superseded by the ratification.

Operational/meta carve-out (by tag, not verb, Standard v1.1 §Q3):
    Tools tagged ``ops`` or ``meta`` skip the verb rule but still must match the
    name charset/length and must not self-prefix the namespace token.
    Covers ``warmup`` and similar infrastructure utilities.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

_NAME_RE = re.compile(r"^[a-z0-9_]{1,50}$")

# Ratified Tier-1: universal read/query canon (Standard v1.1, Rule 2).
_CANONICAL_VERBS = frozenset(
    {"get", "search", "list", "resolve", "find", "compare", "compute", "map"}
)

# Ratified Tier-2: sanctioned domain action/compute verbs (Standard v1.1).
_TIER2_VERBS = frozenset(
    {
        "predict",
        "annotate",
        "recode",
        "liftover",
        "analyze",
        "score",
        "submit",
        "export",
        "generate",
        "download",
    }
)

# Combined allowed verb set for domain tools.
_ALL_VERBS = _CANONICAL_VERBS | _TIER2_VERBS

_NAMESPACE = "spliceai"

# Fleet-canon argument names that supersede local synonyms (issue #2, Rule 4).
# A variant identifier is ``variant_id`` (singular) / ``variant_ids`` (batch);
# the local ``variant`` / ``variants`` synonyms are forbidden.
_FORBIDDEN_ARGS = frozenset({"variant", "variants"})


async def test_tool_names_conform_to_standard_v1_1(mcp: Any) -> None:
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
        # Ops/meta tag carve-out (Standard v1.1 §Q3, ratified fleet rule):
        # infrastructure tools are exempt from the verb rule.
        if "ops" in tags or "meta" in tags:
            continue
        assert name.split("_", 1)[0] in _ALL_VERBS, (
            f"{name!r} must start with a Tier-1 or Tier-2 verb; "
            f"Tier-1: {sorted(_CANONICAL_VERBS)}, Tier-2: {sorted(_TIER2_VERBS)}; "
            "or tag the tool 'ops'/'meta' for the operational carve-out "
            "(Standard v1.1, genefoundry-router/docs/TOOL-NAMING-STANDARD-v1.md)"
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
