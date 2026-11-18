"""Guard: every tool/argument named in a response hint must actually exist (issue #113).

Covers ``_meta.next_commands``, ``fallback_tool`` and ``_meta.see_also`` -- both the
literal templates in the package source (AST scan) and the hints real responses emit.
Own-tool refs are checked against this server's live input schemas. Cross-server
``see_also`` refs carry the GeneFoundry-gateway name (``<namespace>_<tool>``; a bare name
would be rewritten to ``spliceai_<tool>``) and are checked against PINNED parameter sets
taken from each sibling repo's MCP tool signature -- update the pin if a sibling renames.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import spliceailookup_link
from spliceailookup_link.mcp.tools._common import see_also_for
from tests.conftest import expect_tool_error, structured

_CROSS_SERVER: dict[str, set[str]] = {
    "gnomad_get_variant_frequencies": {"variant_id", "dataset", "populations"},
    "genereviews_search_passages": {"q"},
    "gtex_get_median_expression_levels": {"gencode_id", "tissue_site_detail_id"},
    "gtex_search_genes": {"query", "offset", "limit"},
    "uniprot_find_proteins": {"gene_symbol", "organism_taxon", "reviewed"},
}
_REF_KEYS = {"tool", "fallback_tool"}
# Same bare-identifier rule the gateway rewrites by; skips prose such as citation "tool" text.
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


async def _own_schemas(mcp: Any) -> dict[str, set[str]]:
    return {t.name: set(t.parameters.get("properties", {})) for t in await mcp.list_tools()}


def _refs(obj: Any) -> list[tuple[str, set[str]]]:
    """Every (tool, argument-names) pair referenced anywhere in a payload."""
    out: list[tuple[str, set[str]]] = []
    if isinstance(obj, dict):
        for key in _REF_KEYS & obj.keys():
            if isinstance(obj[key], str) and _NAME_RE.match(obj[key]):
                args = obj.get("arguments") if key == "tool" else None
                out.append((obj[key], set(args) if isinstance(args, dict) else set()))
        for value in obj.values():
            out += _refs(value)
    elif isinstance(obj, list):
        for item in obj:
            out += _refs(item)
    return out


def _source_refs() -> list[tuple[str, set[str]]]:
    """Literal hint templates: {"tool": "x", "arguments": {...}} dicts and cmd("x", **kw)."""
    out: list[tuple[str, set[str]]] = []
    for path in Path(spliceailookup_link.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Dict):
                d = {
                    k.value: v
                    for k, v in zip(node.keys, node.values, strict=True)
                    if isinstance(k, ast.Constant)
                }
                for key in _REF_KEYS & d.keys():
                    v = d[key]
                    if (
                        isinstance(v, ast.Constant)
                        and isinstance(v.value, str)
                        and _NAME_RE.match(v.value)
                    ):
                        args = d.get("arguments") if key == "tool" else None
                        names = (
                            {k.value for k in args.keys if isinstance(k, ast.Constant)}
                            if isinstance(args, ast.Dict)
                            else set()
                        )
                        out.append((v.value, names))
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "cmd"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                out.append((node.args[0].value, {k.arg for k in node.keywords if k.arg}))
    return out


def _assert_resolves(refs: list[tuple[str, set[str]]], own: dict[str, set[str]]) -> None:
    assert refs, "no hints collected -- the walk is broken"
    for tool, args in refs:
        known = own.get(tool, _CROSS_SERVER.get(tool))
        assert known is not None, f"hint names unknown tool {tool!r}"
        assert args <= known, f"{tool}: unknown argument(s) {sorted(args - known)}"


async def test_source_hint_templates_resolve(mcp) -> None:
    _assert_resolves(_source_refs(), await _own_schemas(mcp))


async def test_emitted_hints_resolve(mcp) -> None:
    refs: list[tuple[str, set[str]]] = []
    v = "8-140300616-T-G"
    for tool in ("predict_splicing", "predict_spliceai", "predict_pangolin"):
        for mode in ("compact", "full"):
            refs += _refs(
                structured(await mcp.call_tool(tool, {"variant_id": v, "response_mode": mode}))
            )
    refs += _refs(structured(await mcp.call_tool("predict_splicing_batch", {"variant_ids": [v]})))
    refs += _refs(await expect_tool_error(mcp, "predict_splicing", {"variant_id": "not-a-variant"}))
    for gene_id in (None, "ENSG00000167632"):
        for mode in ("compact", "full"):
            refs += _refs(see_also_for(v, "GRCh38", "TRAPPC9", mode, gene_id))
    _assert_resolves(refs, await _own_schemas(mcp))


async def test_see_also_tools_are_gateway_names() -> None:
    hints = see_also_for("8-140300616-T-G", "GRCh38", "TRAPPC9", "compact")
    assert {h["tool"] for h in hints} <= _CROSS_SERVER.keys()
