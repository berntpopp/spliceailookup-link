"""D1 (Tool-Schema Documentation v1.1): each description's first sentence says what it returns."""

from __future__ import annotations

import re

from tests.conftest import structured

# ponytail: copied from genefoundry-router scripts/surface.py (lead_states_return);
# re-vendor byte-identically if this repo ever vendors surface.py.
LEAD_CHARS = 300
_LEAD_VERBS = (  # noqa: SIM905 - verbatim copy of the router list
    "get fetch retrieve list look lookup search find resolve report read predict annotate "
    "compute calculate count summarize summarise map translate convert describe show check "
    "validate classify score rank query browse discover explain compare diff normalize normalise "
    "expand evaluate assess enumerate identify locate export download match suggest recommend "
    "inspect generate render estimate"
).split()
_VERB_LEAD = re.compile(
    rf"^(?:use this (?:tool )?to )?(?:{'|'.join(_LEAD_VERBS)})(?:s|es|ies)?\b", re.IGNORECASE
)
_RETURNS = re.compile(
    r"(?<!not )(?<!n't )(?<!n\u2019t )(?<!never )\breturn(?:s|ed|ing)?\b(?!\s*[~<\d])|→|->",
    re.IGNORECASE,
)
_FIELD_LIST = re.compile(r"\w+\W+\w+[^:]*:\s+[^.;:]+,")
_SENTENCE_END = re.compile(r"(?<!e\.g\.)(?<!i\.e\.)(?<=[.!?])\s+")


def lead_states_return(description: str | None) -> bool:
    text = " ".join((description or "").split())
    lead = _SENTENCE_END.split(text, maxsplit=1)[0][:LEAD_CHARS]
    return bool(_VERB_LEAD.match(lead) or _RETURNS.search(lead) or _FIELD_LIST.search(lead))


def test_lead_rule_rejects_usage_only_lead() -> None:
    assert not lead_states_return("Use this when a caller needs constraint metrics for a gene.")
    assert lead_states_return("Use this when X; returns a, b and c.")


async def test_every_tool_description_leads_with_what_it_returns(mcp) -> None:
    failing = [t.name for t in await mcp.list_tools() if not lead_states_return(t.description)]
    assert failing == []


async def test_predict_splicing_returns_the_fields_its_lead_names(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant_id": "chr8-140300616-T-G"}))
    for field in ("headline", "spliceai", "pangolin", "consequence", "agreement"):
        assert field in data, field
    assert data["agreement"]["verdict"]
