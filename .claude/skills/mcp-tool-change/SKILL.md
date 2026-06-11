---
name: mcp-tool-change
description: Use when adding, renaming, or modifying an MCP tool in spliceailookup-link
---

# Adding or changing an MCP tool

Follow this checklist when touching the MCP surface in `spliceailookup_link/mcp/`.

1. **Define the tool** in a module under `spliceailookup_link/mcp/tools/`. Reuse the
   pattern in `spliceai.py`: `@mcp.tool(name=, title=, annotations=READ_ONLY_OPEN_WORLD,
   tags=)`, typed `Annotated[..., Field(...)]` params, a dense AI-facing docstring
   (what it does, when to use, what it returns, token cost), an inner `async def call()`,
   and `return await run_mcp_tool(name, call, context=McpErrorContext(...))`.
2. **Register it** in `spliceailookup_link/mcp/tools/__init__.py:register_splice_tools`.
3. **Shape the output** in `spliceailookup_link/mcp/shaping.py` (compact/full/minimal +
   a `headline`). Never return raw upstream keys (DS_AG, etc.) — rename to readable classes.
4. **Chain** via `_meta.next_commands` (same-server, ready-to-call) and `_meta.see_also`
   (cross-server hints). Use builders in `next_commands.py` / `_common.py`.
5. **Errors**: raise typed exceptions; the envelope in `mcp/errors.py` classifies them.
   Add a new `error_code` only with a recovery action + recovery text.
6. **Capabilities**: update `mcp/resources.py` (`tools`, `parameters`, `score_glossary`,
   `recommended_workflows`).
7. **Tests**: add a `tests/unit/test_tools.py` case via `mcp.call_tool` + the `StubService`,
   and a `tests/integration` case if behavior depends on live upstream.
8. **Run** `make ci-local`. Keep modules < 600 lines (`make lint-loc`).
