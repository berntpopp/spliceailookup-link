"""The README '## Tools' table must match the registered tool surface exactly.

GeneFoundry README Standard v1, Rule 6: the table is machine-verified, not
hand-maintained. Adding, renaming or removing a tool without updating the README
fails here.

The live tool list is obtained through the same ``mcp`` fixture that
``test_tool_names.py`` uses (``tests/conftest.py`` -> ``create_spliceai_mcp``), so
this test cannot drift from the real facade.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

README = Path(__file__).resolve().parents[2] / "README.md"

# A tool row is `| `tool_name` | purpose |`; the header/separator rows never match.
_TOOL_ROW = re.compile(r"^\|\s*`([a-z0-9_]+)`\s*\|")


def _readme_tool_names() -> set[str]:
    """Parse the tool names out of the README's '## Tools' table."""
    lines = README.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index("## Tools")
    except ValueError:  # pragma: no cover - guarded by check_readme.py too
        raise AssertionError("README.md has no '## Tools' section") from None

    names: set[str] = set()
    for line in lines[start + 1 :]:
        if line.startswith("## "):  # next section ends the table
            break
        match = _TOOL_ROW.match(line)
        if match:
            names.add(match.group(1))
    return names


async def test_readme_tools_table_matches_registered_tools(mcp: Any) -> None:
    registered = {tool.name for tool in await mcp.list_tools()}
    documented = _readme_tool_names()

    assert documented, (
        "no tool rows parsed from the README '## Tools' table; "
        "rows must look like: | `tool_name` | purpose |"
    )
    assert documented == registered, (
        "README '## Tools' table is out of sync with the registered tools.\n"
        f"  in README but not registered: {sorted(documented - registered)}\n"
        f"  registered but not in README: {sorted(registered - documented)}"
    )
