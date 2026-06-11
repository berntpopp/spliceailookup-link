"""Regression tests for the findings in docs/mcp-evaluation.md (F1-F5)."""

from __future__ import annotations

import re

from tests.conftest import structured

_COORD = re.compile(r"^[\dXYM]+-\d+-[ACGT]+-[ACGT]+$")


async def test_f1_multiallelic_rsid_chains_cleanly(mcp) -> None:
    res = await mcp.call_tool("resolve_variant", {"variant": "rs6025"})
    data = structured(res)
    assert _COORD.match(data["variant_id"])
    assert data["ambiguous"] is True
    cmds = data["_meta"]["next_commands"]
    assert len(cmds) == 2
    for c in cmds:
        assert _COORD.match(c["arguments"]["variant"])
