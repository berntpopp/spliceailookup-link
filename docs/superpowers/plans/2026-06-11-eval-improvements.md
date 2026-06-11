# Evaluation-Driven Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift spliceailookup-link past 9/10 on both axes of `docs/mcp-evaluation.md` by fixing findings F1–F5 and shipping runtime observability, token trims, progress + native MCP Tasks, a batch tool, a warmup tool, and a capabilities content-hash.

**Architecture:** Keep the layering (variant → api clients → SpliceService → mcp facade/tools). Add a small services-layer telemetry primitive surfaced through `_meta`; centralize per-call `request_id`/`timing` in `run_mcp_tool`; extract a shared `predict_one()` core so `predict_splicing` and the new batch tool share one code path; fix the resolver and shaping bugs at their source; adopt FastMCP progress + `task=True`.

**Tech Stack:** Python 3.12, FastMCP 3.x (`fastmcp[tasks]` → Docket), httpx, async-lru, pydantic, pytest + respx. Source of truth: `docs/superpowers/specs/2026-06-11-eval-improvements-design.md`.

**Conventions for every task:** modern typing (`list[str]`, `X | None`); ASCII; run `make format lint typecheck test` before each commit; never let a `spliceailookup_link/` module cross 600 LOC (`make lint-loc`). Commit messages end with the project's Co-Authored-By trailer. Do **not** push.

---

## File Structure

**New files:**
- `spliceailookup_link/services/telemetry.py` — `CallTelemetry` dataclass + cache-miss `ContextVar` + helpers. Service-layer (no mcp import).
- `spliceailookup_link/mcp/tools/_predict.py` — shared `predict_one()` core (resolve → both models → cross-build → merge/dedup → headline/agreement). Used by `predict_splicing` and the batch tool.
- `spliceailookup_link/mcp/tools/batch.py` — `predict_splicing_batch`.
- `tests/unit/test_telemetry.py`, `tests/unit/test_batch.py`, `tests/unit/test_eval_fixes.py` — new test modules.

**Modified:**
- `services/splice_service.py` — F1 resolver fix; `score()` returns telemetry; `warmup()`.
- `mcp/errors.py` — `request_id` + `timing.elapsed_ms` in `run_mcp_tool` (success + error paths).
- `mcp/shaping.py` — F2 stable `consequence` contract.
- `mcp/tools/resolve.py` — F1 ambiguous payload + schema.
- `mcp/tools/combined.py` — slimmed to register `predict_splicing` over `predict_one`; F3 next_commands; progress; `task=True`.
- `mcp/tools/spliceai.py`, `pangolin.py` — telemetry `_meta`, F5 cross-build, progress, `task=True`.
- `mcp/tools/_common.py` — `see_also` policy by `response_mode`; `cross_build_probe()`.
- `mcp/tools/metadata.py` — register `warmup`.
- `mcp/tools/__init__.py` — register batch tool.
- `mcp/next_commands.py` — `after_resolve_many()`, `for_combined()`.
- `mcp/resources.py` — `capabilities_version` + `descriptor_chars`.
- `mcp/facade.py` — `FastMCP(..., tasks=True)`.
- `config.py` — `DOCKET_URL`.
- `pyproject.toml` — `fastmcp[tasks]`, version `0.2.0`.
- `tests/conftest.py` — `StubService.score` returns telemetry tuple + cache simulation; multi-allele resolve branch.
- `tests/fixtures/api_responses.py` — `VEP_RS6025`, `SPLICEAI_TRAPPC9_GRCH37`, masked SAI-10k fixture.
- Docs: `README.md`, capabilities/reference resources, `docs/mcp-evaluation.md` (re-score).

---

## Task 1: F1 — multi-allelic rsID resolver (HIGH, do first)

**Files:**
- Modify: `spliceailookup_link/services/splice_service.py` (`_normalize_vep_record`)
- Modify: `spliceailookup_link/mcp/next_commands.py`
- Modify: `spliceailookup_link/mcp/tools/resolve.py`
- Modify: `tests/fixtures/api_responses.py`
- Modify: `tests/conftest.py`
- Test: `tests/unit/test_service.py`, `tests/unit/test_eval_fixes.py` (new)

- [ ] **Step 1: Add the rs6025 fixture**

In `tests/fixtures/api_responses.py` append:

```python
# Ensembl VEP rsID resolution where vcf_string is a LIST (multi-allelic locus).
# rs6025 (Factor V Leiden) reports two ALT alleles at one position.
VEP_RS6025: list[dict[str, Any]] = [
    {
        "input": "rs6025",
        "id": "rs6025",
        "seq_region_name": "1",
        "start": 169549811,
        "vcf_string": ["1-169549811-C-A", "1-169549811-C-T"],
        "most_severe_consequence": "missense_variant",
        "assembly_name": "GRCh38",
        "transcript_consequences": [{"gene_symbol": "F5", "gene_id": "ENSG00000198734"}],
    }
]
```

- [ ] **Step 2: Write the failing service test**

In `tests/unit/test_service.py` add (and extend `_FakeEnsembl` so `resolve_id` can return the multi-allele record):

```python
import re
from tests.fixtures.api_responses import VEP_RS6025


class _FakeEnsemblMulti:
    async def resolve_hgvs(self, hgvs: str, build: str) -> dict[str, Any]:
        return VEP_RS6025[0]

    async def resolve_id(self, vid: str, build: str) -> dict[str, Any]:
        return VEP_RS6025[0]

    async def close(self) -> None:
        return None


async def test_resolve_multiallelic_rsid_is_structured() -> None:
    svc = SpliceService(scoring_client=_FakeScoring(), ensembl_client=_FakeEnsemblMulti())
    out = await svc.resolve("rs6025", "GRCh38")
    coord = re.compile(r"^[\dXYM]+-\d+-[ACGT]+-[ACGT]+$")
    assert coord.match(out["variant_id"]), out["variant_id"]
    assert out["ambiguous"] is True
    assert len(out["variant_ids"]) == 2
    assert all(coord.match(v) for v in out["variant_ids"])
    assert "note" in out
```

- [ ] **Step 3: Run it — expect FAIL**

Run: `uv run pytest tests/unit/test_service.py::test_resolve_multiallelic_rsid_is_structured -v`
Expected: FAIL — `variant_id` is the stringified list `"['1-169549811-C-A', ...]"`, `ambiguous` KeyError.

- [ ] **Step 4: Fix `_normalize_vep_record`**

Replace the `vcf_string` handling block in `splice_service.py:_normalize_vep_record`:

```python
def _strip_chr(value: str) -> str:
    return value[3:] if value.lower().startswith("chr") else value


def _normalize_vep_record(
    record: dict[str, Any], parsed: VariantInput, build: GenomeBuild, raw_input: str
) -> dict[str, Any]:
    vcf_string = record.get("vcf_string")
    # VEP returns vcf_string as a list when an rsID maps to multiple ALT alleles.
    raw_ids = vcf_string if isinstance(vcf_string, list) else [vcf_string]
    candidates: list[str] = []
    for item in raw_ids:
        if item:
            cid = _strip_chr(str(item))
            if cid not in candidates:
                candidates.append(cid)
    gene_names = record.get("transcript_consequences") or []
    gene_symbol = next((tc["gene_symbol"] for tc in gene_names if tc.get("gene_symbol")), None)
    result: dict[str, Any] = {
        "variant_id": candidates[0],
        "genome_build": build,
        "input_kind": parsed.kind,
        "source": "ensembl_vep",
        "resolved_from": parsed.value,
        "assembly_name": record.get("assembly_name"),
        "gene_symbol": gene_symbol,
        "consequence": record.get("most_severe_consequence"),
        "raw_input": raw_input,
    }
    if len(candidates) > 1:
        result["ambiguous"] = True
        result["variant_ids"] = candidates
        result["note"] = (
            f"{parsed.value} maps to {len(candidates)} alleles at this locus; "
            "pick one variant_id before predicting."
        )
    return result
```

- [ ] **Step 5: Run it — expect PASS**

Run: `uv run pytest tests/unit/test_service.py::test_resolve_multiallelic_rsid_is_structured -v`
Expected: PASS.

- [ ] **Step 6: Add `after_resolve_many` to next_commands.py**

```python
def after_resolve_many(variant_ids: list[str], genome_build: str) -> list[dict[str, Any]]:
    """One predict_splicing per allele so every candidate is directly callable."""
    return [cmd("predict_splicing", variant=v, genome_build=genome_build) for v in variant_ids]
```

- [ ] **Step 7: Wire the resolver tool (next_commands fan-out + schema)**

In `resolve.py`, extend `_OUTPUT_SCHEMA["properties"]` with:

```python
            "ambiguous": {"type": "boolean"},
            "variant_ids": {"type": "array", "items": {"type": "string"}},
            "note": {"type": ["string", "null"]},
```

and change the `call()` body's `_meta` assignment:

```python
            ids = result.get("variant_ids") or [result["variant_id"]]
            result["_meta"] = {"next_commands": after_resolve_many(ids, genome_build)}
```

(Add `after_resolve_many` to the import from `next_commands`.)

- [ ] **Step 8: Add a tool-level regression and a StubService multi-allele branch**

In `tests/conftest.py` `StubService.resolve`, before the coordinate check, add:

```python
        if text.lower() == "rs6025":
            return {
                "variant_id": "1-169549811-C-A",
                "genome_build": build,
                "input_kind": "rsid",
                "source": "ensembl_vep",
                "gene_symbol": "F5",
                "consequence": "missense_variant",
                "ambiguous": True,
                "variant_ids": ["1-169549811-C-A", "1-169549811-C-T"],
                "note": "rs6025 maps to 2 alleles at this locus; pick one variant_id.",
                "raw_input": text,
            }
```

In `tests/unit/test_eval_fixes.py` (new):

```python
"""Regression tests for the findings in docs/mcp-evaluation.md (F1-F5)."""

from __future__ import annotations

import re

from tests.conftest import StubService, structured

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
```

- [ ] **Step 9: Run the suite + commit**

Run: `uv run pytest tests/unit/test_service.py tests/unit/test_eval_fixes.py -v`
Expected: PASS.

```bash
git add spliceailookup_link/services/splice_service.py spliceailookup_link/mcp/next_commands.py spliceailookup_link/mcp/tools/resolve.py tests/
git commit -m "fix: structured multi-allelic rsID resolution (F1)"
```

---

## Task 2: Per-call request_id + timing in `run_mcp_tool`

**Files:**
- Modify: `spliceailookup_link/mcp/errors.py`
- Test: `tests/unit/test_eval_fixes.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_meta_has_request_id_and_timing(mcp) -> None:
    res = await mcp.call_tool("get_server_capabilities", {})
    meta = structured(res)["_meta"]
    assert isinstance(meta["request_id"], str) and len(meta["request_id"]) == 12
    assert isinstance(meta["timing"]["elapsed_ms"], int)


async def test_error_envelope_has_request_id(mcp, stub_service: StubService) -> None:
    from spliceailookup_link.variant import VariantParseError

    stub_service.resolve_error = VariantParseError("bad")
    res = await mcp.call_tool("predict_spliceai", {"variant": "totally invalid"})
    data = structured(res)
    assert data["success"] is False
    assert "request_id" in data["_meta"]
```

- [ ] **Step 2: Run — expect FAIL** (`KeyError: 'request_id'`).

Run: `uv run pytest tests/unit/test_eval_fixes.py -k request_id -v`

- [ ] **Step 3: Implement in `run_mcp_tool`**

At the top of `errors.py` add imports `import time`, `import uuid`. Replace `run_mcp_tool` so it measures and stamps on every return path:

```python
async def run_mcp_tool(
    tool_name: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    context: McpErrorContext | None = None,
) -> dict[str, Any]:
    """Execute an MCP tool body, converting any exception to an envelope dict."""
    ctx = context or McpErrorContext(tool_name=tool_name)
    request_id = uuid.uuid4().hex[:12]
    start = time.perf_counter()

    def _stamp(envelope: dict[str, Any]) -> dict[str, Any]:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        existing: dict[str, Any] = envelope.get("_meta") or {}
        envelope["_meta"] = {
            "request_id": request_id,
            "timing": {"elapsed_ms": elapsed_ms},
            **existing,
            **_provenance_meta(),
        }
        return envelope

    try:
        result = await call()
        if isinstance(result, dict):
            result.setdefault("success", True)
            return _stamp(result)
        return result
    except McpToolError as exc:
        record_mcp_error(
            tool_name=tool_name,
            error_code=exc.payload.get("error_code", "internal_error"),
            message=exc.payload.get("message", ""),
            raw_message=str(exc),
        )
        return _stamp(exc.payload)
    except Exception as exc:  # broad catch is the error-boundary contract
        wrapped = mcp_tool_error(exc, ctx)
        logger.warning(
            "mcp_tool_error tool=%s code=%s request_id=%s exc=%s",
            tool_name,
            wrapped.payload["error_code"],
            request_id,
            exc.__class__.__name__,
        )
        record_mcp_error(
            tool_name=tool_name,
            error_code=wrapped.payload["error_code"],
            message=wrapped.payload["message"],
            raw_message=str(exc),
        )
        return _stamp(wrapped.payload)
```

- [ ] **Step 4: Run — expect PASS.** `uv run pytest tests/unit/test_eval_fixes.py -k request_id -v`

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/errors.py tests/unit/test_eval_fixes.py
git commit -m "feat: stamp request_id + timing.elapsed_ms on every envelope"
```

---

## Task 3: Scoring cache telemetry (`_meta.cache` / `upstream_elapsed_ms`)

**Files:**
- Create: `spliceailookup_link/services/telemetry.py`
- Modify: `spliceailookup_link/services/splice_service.py`
- Modify: `spliceailookup_link/mcp/tools/spliceai.py`, `pangolin.py`, `combined.py`
- Modify: `tests/conftest.py`
- Test: `tests/unit/test_telemetry.py` (new), `tests/unit/test_service.py`

- [ ] **Step 1: Create the telemetry primitive**

`spliceailookup_link/services/telemetry.py`:

```python
"""Per-call telemetry for scoring (cache hit/miss + upstream timing).

`score()` runs its cached leaf in the SAME asyncio task it is awaited from, so a
ContextVar set inside the leaf is visible to score() after the await. score()
then returns the telemetry BY VALUE, which is safe to read across the
asyncio.gather task boundary in the combined tool.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

_cache_miss: ContextVar[bool] = ContextVar("splice_cache_miss", default=False)


def begin_cache_probe() -> None:
    _cache_miss.set(False)


def mark_cache_miss() -> None:
    _cache_miss.set(True)


def was_cache_miss() -> bool:
    return _cache_miss.get()


@dataclass(slots=True)
class CallTelemetry:
    cache: str  # "hit" | "miss"
    upstream_elapsed_ms: int | None = None
```

- [ ] **Step 2: Write the failing service test**

In `tests/unit/test_service.py`:

```python
from spliceailookup_link.services.telemetry import CallTelemetry


async def test_score_reports_cache_miss_then_hit() -> None:
    svc, scoring, _ = _service()
    args = {"model": "spliceai", "build": "GRCh38", "variant_id": "8-140300616-T-G",
            "distance": 500, "mask": 0}
    payload1, t1 = await svc.score(**args)
    payload2, t2 = await svc.score(**args)
    assert isinstance(t1, CallTelemetry)
    assert t1.cache == "miss" and isinstance(t1.upstream_elapsed_ms, int)
    assert t2.cache == "hit" and t2.upstream_elapsed_ms is None
    assert scoring.calls == 1
```

Note: this changes `score()`'s return contract. Update the existing
`test_score_caches_identical_calls` / `test_score_distinct_params_not_cached_together`
to unpack: `a, _ = await svc.score(**args)`.

- [ ] **Step 3: Run — expect FAIL** (`too many values to unpack` / current returns a dict).

- [ ] **Step 4: Update `SpliceService.score` + `_score_uncached`**

In `splice_service.py` add `from time import perf_counter` and
`from spliceailookup_link.services.telemetry import CallTelemetry, begin_cache_probe, mark_cache_miss, was_cache_miss`.

At the top of `_score_uncached` add `mark_cache_miss()`. Replace `score`:

```python
    async def score(
        self,
        *,
        model: str,
        build: GenomeBuild,
        variant_id: str,
        distance: int,
        mask: int,
        gene_set: str = "basic",
        raw: str | None = None,
        consequence: str | None = None,
    ) -> tuple[dict[str, Any], CallTelemetry]:
        """Return (raw payload, telemetry) for one variant; cached by params."""
        begin_cache_probe()
        start = perf_counter()
        payload = await self._score_cached(
            model, build, variant_id, distance, mask, gene_set, raw, consequence
        )
        elapsed_ms = int((perf_counter() - start) * 1000)
        missed = was_cache_miss()
        return payload, CallTelemetry(
            cache="miss" if missed else "hit",
            upstream_elapsed_ms=elapsed_ms if missed else None,
        )
```

- [ ] **Step 5: Update tool call sites to unpack + stamp `_meta`**

`spliceai.py` and `pangolin.py` — change `result = await service.score(...)` to:

```python
            payload, tele = await service.score(...)
            shaped = shape_spliceai(payload, transcripts=transcripts, response_mode=response_mode, include_consequence=include_consequence)
            shaped["_meta"] = {
                "next_commands": for_variant(prepared.variant_id, genome_build),
                "see_also": see_also_for(prepared.variant_id, genome_build, gene, response_mode),
                "cache": tele.cache,
            }
            if tele.upstream_elapsed_ms is not None:
                shaped["_meta"]["upstream_elapsed_ms"] = tele.upstream_elapsed_ms
            return shaped
```

(Adjust to each tool's existing `_meta` construction; the new keys are `cache` and conditional `upstream_elapsed_ms`. `see_also_for`'s new `response_mode` arg arrives in Task 6 — until then call it without it; this plan introduces the arg in Task 6 and updates these call sites there. For Task 3 just add `cache`/`upstream_elapsed_ms`.)

`combined.py` — the gather now yields `(payload, tele)` tuples or exceptions:

```python
            sai_res, pang_res = gathered[0], gathered[1]
            ...
            if isinstance(sai_res, BaseException):
                partial.append(...)
            else:
                sai_payload, sai_tele = sai_res
                shaped_sai = shape_spliceai(sai_payload, ...)
                ...
```

Aggregate cache into `_meta` (both miss → "miss", both hit → "hit", else "partial"):

```python
            caches = [t.cache for t in (sai_tele_opt, pang_tele_opt) if t is not None]
            if caches:
                meta["cache"] = "hit" if all(c == "hit" for c in caches) else ("miss" if all(c == "miss" for c in caches) else "partial")
                ups = [t.upstream_elapsed_ms for t in (sai_tele_opt, pang_tele_opt) if t and t.upstream_elapsed_ms is not None]
                if ups:
                    meta["upstream_elapsed_ms"] = max(ups)
```

(Track `sai_tele_opt`/`pang_tele_opt`, default `None`, set when the branch succeeds.) Note: Task 5 rewrites `combined.py` to call `predict_one`; this telemetry merge moves into `predict_one` there. For Task 3, implement it inline so tests pass now.

- [ ] **Step 6: Update `StubService.score` to return telemetry**

In `tests/conftest.py`:

```python
    def __init__(self) -> None:
        ...
        self._seen_keys: set[tuple] = set()

    async def score(self, *, model: str, build: str, variant_id: str, **kwargs: Any):
        from spliceailookup_link.services.telemetry import CallTelemetry

        self.score_calls.append({"model": model, "build": build, "variant_id": variant_id, **kwargs})
        if model == "pangolin" and self.pangolin_error is not None:
            raise self.pangolin_error
        if self.score_error is not None:
            raise self.score_error
        key = (model, build, variant_id, kwargs.get("distance"), kwargs.get("mask"), kwargs.get("gene_set"))
        cache = "hit" if key in self._seen_keys else "miss"
        self._seen_keys.add(key)
        payload = PANGOLIN_TRAPPC9 if model == "pangolin" else SPLICEAI_TRAPPC9
        return payload, CallTelemetry(cache=cache, upstream_elapsed_ms=None if cache == "hit" else 7)
```

- [ ] **Step 7: Add a tool-level telemetry test** in `tests/unit/test_telemetry.py`:

```python
"""Runtime observability in _meta."""

from __future__ import annotations

from tests.conftest import structured


async def test_spliceai_meta_reports_cache_miss_then_hit(mcp) -> None:
    first = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    second = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    assert first["_meta"]["cache"] == "miss"
    assert "upstream_elapsed_ms" in first["_meta"]
    assert second["_meta"]["cache"] == "hit"


async def test_combined_meta_cache_present(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert data["_meta"]["cache"] in {"hit", "miss", "partial"}
```

- [ ] **Step 8: Run + commit**

Run: `uv run pytest tests/unit/test_service.py tests/unit/test_telemetry.py tests/unit/test_tools.py -v`
Expected: PASS (existing tool tests still green; they ignore the new `_meta` keys).

```bash
git add spliceailookup_link/services tests/ spliceailookup_link/mcp/tools/
git commit -m "feat: cache hit/miss + upstream timing telemetry in _meta"
```

---

## Task 4: F2 — stable `consequence.aberrations` contract

**Files:**
- Modify: `spliceailookup_link/mcp/shaping.py`
- Modify: `tests/fixtures/api_responses.py`
- Test: `tests/unit/test_shaping.py`, `tests/unit/test_eval_fixes.py`

- [ ] **Step 1: Add a masked SAI-10k fixture** (empty aberrations) in `api_responses.py`:

```python
# SpliceAI payload where the SAI-10k aberration list is empty (typical under mask=1)
# but the raw object still carries transcript_info — this triggered the F2 drift.
SPLICEAI_MASKED_EMPTY_ABERR: dict[str, Any] = {
    "variant": "8-140300616-T-G", "hg": "38", "bc": "basic", "distance": 500, "mask": 1,
    "scores": SPLICEAI_TRAPPC9["scores"],
    "sai10kPredictions": {"aberrations": [], "transcript_info": {"strand": "-", "exon_count": 23}},
}
```

- [ ] **Step 2: Write the failing tests** in `tests/unit/test_shaping.py`:

```python
from spliceailookup_link.mcp.shaping import shape_spliceai
from tests.fixtures.api_responses import SPLICEAI_MASKED_EMPTY_ABERR, SPLICEAI_TRAPPC9


def test_consequence_aberrations_is_stable_path_when_empty():
    out = shape_spliceai(SPLICEAI_MASKED_EMPTY_ABERR, response_mode="compact")
    assert out["consequence"]["aberrations"] == []
    assert "raw" not in out["consequence"]


def test_full_mode_adds_transcript_info_as_sibling():
    out = shape_spliceai(SPLICEAI_MASKED_EMPTY_ABERR, response_mode="full")
    assert "aberrations" in out["consequence"]
    assert out["consequence"]["transcript_info"] == {"strand": "-", "exon_count": 23}


def test_populated_aberrations_unchanged():
    out = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="compact")
    assert out["consequence"]["aberrations"][0]["type"] == "exon_skipping"
```

- [ ] **Step 3: Run — expect FAIL** (`consequence.raw` present; no stable `aberrations`).

- [ ] **Step 4: Rewrite `_shape_consequence` (add `mode` param)** in `shaping.py`:

```python
def _shape_consequence(payload: dict[str, Any], mode: ResponseMode) -> dict[str, Any] | None:
    sai = payload.get("sai10kPredictions")
    err = payload.get("sai10kPredictionsError")
    if not sai and not err:
        return None
    out: dict[str, Any] = {}
    if err:
        out["error"] = err
    raw_aberr = (sai or {}).get("aberrations") if isinstance(sai, dict) else None
    out["aberrations"] = [
        {
            "type": ab.get("aberration_type"),
            "affected_region": ab.get("affected_region"),
            "status": ab.get("status"),
            "size_is_coding": ab.get("size_is_coding"),
            "introduces_stop_codon": ab.get("introduces_stop_codon"),
        }
        for ab in (raw_aberr or [])
    ]
    if mode == "full" and isinstance(sai, dict):
        if sai.get("transcript_info") is not None:
            out["transcript_info"] = sai["transcript_info"]
        extras = {k: v for k, v in sai.items() if k not in {"aberrations", "transcript_info"}}
        if extras:
            out["raw_extras"] = extras
    return out
```

In `shape_spliceai`, change the call to `_shape_consequence(payload, response_mode)`.

- [ ] **Step 5: Run — expect PASS.** `uv run pytest tests/unit/test_shaping.py -v`

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/shaping.py tests/
git commit -m "fix: stabilize consequence.aberrations across response modes (F2)"
```

---

## Task 5: F3 + F4 — extract `predict_one`, dedup, uniform next_commands

**Files:**
- Create: `spliceailookup_link/mcp/tools/_predict.py`
- Modify: `spliceailookup_link/mcp/tools/combined.py`
- Modify: `spliceailookup_link/mcp/next_commands.py`
- Test: `tests/unit/test_eval_fixes.py`, `tests/unit/test_tools.py`

- [ ] **Step 1: Write failing tests** in `tests/unit/test_eval_fixes.py`:

```python
async def test_f3_predict_splicing_has_next_commands(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    cmds = data["_meta"]["next_commands"]
    assert cmds and cmds[0]["tool"] in {"predict_spliceai", "predict_pangolin"}
    assert cmds[0]["arguments"]["response_mode"] == "full"


async def test_f4_no_duplicate_consequence_or_identity(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert "consequence" in data            # top-level only
    assert "consequence" not in data["spliceai"]
    assert "transcript" in data             # single lifted identity block
    assert data["transcript"]["gene"] == "TRAPPC9"
    # per-model sub-objects no longer repeat refseq_ids when transcript matches
    assert "refseq_ids" not in data["spliceai"]
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Add `for_combined` to next_commands.py**

```python
def for_combined(variant_id: str, genome_build: str) -> list[dict[str, Any]]:
    """Same-server drill-down: full single-model scores for this variant."""
    return [
        cmd("predict_spliceai", variant=variant_id, genome_build=genome_build, response_mode="full"),
    ]
```

- [ ] **Step 4: Create `_predict.py` with the shared core**

```python
"""Shared predict_splicing core: resolve -> both models -> merge/dedup -> headline.

Used by predict_splicing (single) and predict_splicing_batch (fan-out). Returns a
result dict WITHOUT the outer success/_meta envelope; callers add _meta.
"""

from __future__ import annotations

import asyncio
from typing import Any

from spliceailookup_link.api import DataNotFoundError
from spliceailookup_link.config import GenomeBuild
from spliceailookup_link.mcp.errors import BuildMismatchError
from spliceailookup_link.mcp.shaping import shape_pangolin, shape_spliceai
from spliceailookup_link.mcp.tools._common import (
    cross_build_probe,
    mask_to_int,
    prepare_variant,
)
from spliceailookup_link.services import SpliceService
from spliceailookup_link.services.telemetry import CallTelemetry

_HIGH = 0.5
_LOW = 0.2

_IDENTITY_KEYS = ("gene", "gene_id", "transcript_id", "transcript_priority", "refseq_ids", "strand")


def _assess_agreement(sai_max: float | None, pang_max: float | None) -> dict[str, Any]:
    if sai_max is None or pang_max is None:
        return {"verdict": "incomplete", "detail": "one model returned no score"}
    both_high = sai_max >= _HIGH and pang_max >= _HIGH
    both_low = sai_max < _LOW and pang_max < _LOW
    if both_high:
        verdict, detail = "concordant_high", "both models predict a strong splicing effect"
    elif both_low:
        verdict, detail = "concordant_low", "both models predict little or no splicing effect"
    else:
        verdict, detail = "discordant", "models disagree on the magnitude; interpret with caution"
    return {"verdict": verdict, "detail": detail,
            "spliceai_max_delta": sai_max, "pangolin_max_delta": pang_max}


def _aggregate_cache(teles: list[CallTelemetry]) -> tuple[str | None, int | None]:
    caches = [t.cache for t in teles]
    if not caches:
        return None, None
    cache = "hit" if all(c == "hit" for c in caches) else ("miss" if all(c == "miss" for c in caches) else "partial")
    ups = [t.upstream_elapsed_ms for t in teles if t.upstream_elapsed_ms is not None]
    return cache, (max(ups) if ups else None)


def _lift_identity(sai_t: dict[str, Any] | None, pang_t: dict[str, Any] | None) -> dict[str, Any] | None:
    """Lift one shared transcript-identity block when both models agree on transcript."""
    if not sai_t or not pang_t:
        return None
    if sai_t.get("transcript_id") and sai_t.get("transcript_id") == pang_t.get("transcript_id"):
        return {k: sai_t.get(k) for k in _IDENTITY_KEYS}
    return None


async def predict_one(
    service: SpliceService,
    *,
    variant: str,
    genome_build: GenomeBuild,
    max_distance: int,
    mask: str,
    gene_set: str,
    transcripts: str,
    response_mode: str,
    cross_build_check: bool = True,
    ctx: Any = None,
) -> dict[str, Any]:
    if ctx is not None:
        await ctx.report_progress(progress=0, total=3, message="resolving variant")
    prepared = await prepare_variant(service, variant, genome_build)
    common = {"build": prepared.genome_build, "variant_id": prepared.variant_id,
              "distance": max_distance, "mask": mask_to_int(mask), "gene_set": gene_set,
              "raw": variant, "consequence": prepared.consequence}
    if ctx is not None:
        await ctx.report_progress(progress=1, total=3, message="scoring SpliceAI + Pangolin")
    gathered: list[Any] = await asyncio.gather(
        service.score(model="spliceai", **common),
        service.score(model="pangolin", **common),
        return_exceptions=True,
    )
    sai_res, pang_res = gathered[0], gathered[1]
    if isinstance(sai_res, BaseException) and isinstance(pang_res, BaseException):
        if cross_build_check and prepared.resolution is None and isinstance(sai_res, DataNotFoundError):
            other = await cross_build_probe(
                service, model="spliceai", requested_build=genome_build,
                variant_id=prepared.variant_id, distance=max_distance,
                mask=mask_to_int(mask), gene_set=gene_set,
            )
            if other:
                raise BuildMismatchError(variant_id=prepared.variant_id,
                                         inferred_build=other, requested_build=genome_build) from sai_res
        raise sai_res

    if ctx is not None:
        await ctx.report_progress(progress=2, total=3, message="merging models")

    result: dict[str, Any] = {"variant_id": prepared.variant_id, "genome_build": genome_build,
                              "max_distance": max_distance, "mask": mask, "gene_set": gene_set}
    teles: list[CallTelemetry] = []
    gene = sai_max = pang_max = consequence = None
    sai_top = pang_top = None
    partial: list[str] = []

    if isinstance(sai_res, BaseException):
        partial.append(f"spliceai_failed: {sai_res!s}"[:200])
    else:
        sai_payload, sai_tele = sai_res
        teles.append(sai_tele)
        shaped_sai = shape_spliceai(sai_payload, transcripts=transcripts, response_mode=response_mode)
        sai_max = shaped_sai.get("max_delta_score")
        consequence = shaped_sai.pop("consequence", None)  # F4: lift, do not duplicate
        if shaped_sai["transcripts"]:
            sai_top = shaped_sai["transcripts"][0]
            gene = sai_top.get("gene")
        result["spliceai"] = shaped_sai

    if isinstance(pang_res, BaseException):
        partial.append(f"pangolin_failed: {pang_res!s}"[:200])
    else:
        pang_payload, pang_tele = pang_res
        teles.append(pang_tele)
        shaped_pang = shape_pangolin(pang_payload, transcripts=transcripts, response_mode=response_mode)
        pang_max = shaped_pang.get("max_delta_score")
        if shaped_pang["transcripts"]:
            pang_top = shaped_pang["transcripts"][0]
            if gene is None:
                gene = pang_top.get("gene")
        result["pangolin"] = shaped_pang

    identity = _lift_identity(sai_top, pang_top)
    if identity:
        result["transcript"] = identity
        for sub in ("spliceai", "pangolin"):
            block = result.get(sub)
            if block and block.get("transcripts"):
                for k in _IDENTITY_KEYS:
                    block["transcripts"][0].pop(k, None)

    if consequence is not None:
        result["consequence"] = consequence
    result["agreement"] = _assess_agreement(sai_max, pang_max)
    result["headline"] = _combined_headline(gene, genome_build, sai_max, pang_max, consequence)
    cache, ups = _aggregate_cache(teles)
    result["_telemetry"] = {"cache": cache, "upstream_elapsed_ms": ups, "gene": gene,
                            "partial": partial, "resolution": prepared.resolution,
                            "resolved_consequence": prepared.consequence}
    return result


def _combined_headline(gene, build, sai_max, pang_max, consequence) -> str:
    gene_label = gene or "variant"
    parts = []
    if sai_max is not None:
        parts.append(f"SpliceAI Δ={sai_max:.2f}")
    if pang_max is not None:
        parts.append(f"Pangolin Δ={pang_max:.2f}")
    scores = "; ".join(parts) if parts else "no scores"
    aberr = None
    if consequence and consequence.get("aberrations"):
        aberr = (consequence["aberrations"][0] or {}).get("type")
    tail = f"; predicted {aberr.replace('_', ' ')}" if aberr else ""
    if sai_max is not None and pang_max is not None:
        agree = "agree" if (sai_max >= _HIGH) == (pang_max >= _HIGH) else "disagree"
        verdict = f"; models {agree}"
    else:
        verdict = ""
    return f"{gene_label} ({build}): {scores}{verdict}{tail}."
```

- [ ] **Step 5: Slim `combined.py` to use `predict_one`**

Replace the body of `combined.py` (keeping the `@mcp.tool` registration) so the `call()` delegates and builds `_meta`:

```python
        async def call() -> dict[str, Any]:
            service = service_factory()
            result = await predict_one(
                service, variant=variant, genome_build=genome_build, max_distance=max_distance,
                mask=mask, gene_set=gene_set, transcripts=transcripts, response_mode=response_mode,
                cross_build_check=cross_build_check, ctx=ctx,
            )
            tel = result.pop("_telemetry")
            meta: dict[str, Any] = {
                "next_commands": for_combined(result["variant_id"], genome_build),
                "see_also": see_also_for(result["variant_id"], genome_build, tel["gene"], response_mode),
            }
            if tel["cache"]:
                meta["cache"] = tel["cache"]
            if tel["upstream_elapsed_ms"] is not None:
                meta["upstream_elapsed_ms"] = tel["upstream_elapsed_ms"]
            if tel["resolution"] is not None:
                meta["resolved_from"] = tel["resolution"].get("raw_input")
                meta["resolved_consequence"] = tel["resolved_consequence"]
            if tel["partial"]:
                meta["partial"] = tel["partial"]
            result["_meta"] = meta
            return result
```

Add `cross_build_check: bool = True` and `ctx: Context = None` params to `predict_splicing` (import `from fastmcp import Context`), and delete the now-duplicated `_assess_agreement`/`_combined_headline` from `combined.py` (they live in `_predict.py`). Update imports.

- [ ] **Step 6: Run — expect PASS**

Run: `uv run pytest tests/unit/test_eval_fixes.py tests/unit/test_tools.py -v`
Expected: PASS. Update `test_predict_splicing_partial_when_pangolin_fails` only if it asserted `refseq_ids` inside a sub-object (it does not).

- [ ] **Step 7: Verify LOC budget + commit**

Run: `make lint-loc`
Expected: all modules < 600 (combined.py shrinks; _predict.py new).

```bash
git add spliceailookup_link/mcp/tools/ spliceailookup_link/mcp/next_commands.py tests/
git commit -m "refactor: extract predict_one; dedup + uniform next_commands (F3/F4)"
```

---

## Task 6: `see_also` policy by response_mode

**Files:**
- Modify: `spliceailookup_link/mcp/tools/_common.py`
- Modify: call sites in `spliceai.py`, `pangolin.py` (and `_predict.py`/`combined.py` already pass `response_mode`)
- Test: `tests/unit/test_eval_fixes.py`

- [ ] **Step 1: Write failing tests**

```python
async def test_see_also_omitted_in_minimal(mcp) -> None:
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G", "response_mode": "minimal"}))
    assert "see_also" not in data["_meta"]


async def test_see_also_collapsed_in_compact(mcp) -> None:
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    for hint in data["_meta"]["see_also"]:
        assert "example" not in hint and set(hint) == {"server", "hint"}


async def test_see_also_full_keeps_example(mcp) -> None:
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G", "response_mode": "full"}))
    assert any("example" in h for h in data["_meta"]["see_also"])
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Add `response_mode` to `see_also_for`** in `_common.py`:

```python
def see_also_for(
    variant_id: str, genome_build: GenomeBuild, gene: str | None, response_mode: str = "compact"
) -> list[dict[str, Any]]:
    """Cross-server hints. minimal -> []; compact -> {server,hint}; full -> + example args."""
    if response_mode == "minimal":
        return []
    full = _see_also_full(variant_id, genome_build, gene)  # the existing list-builder body
    if response_mode == "full":
        return full
    return [{"server": h["server"], "hint": h["hint"]} for h in full]
```

Rename the current body to `_see_also_full(...)`. In the registering tools, when `response_mode == "minimal"` omit the `see_also` key entirely (don't emit an empty list): build `_meta` then `if response_mode != "minimal": meta["see_also"] = ...`. Update `spliceai.py`, `pangolin.py`, and `combined.py`'s `call()` accordingly.

- [ ] **Step 4: Run — expect PASS** and confirm `test_predict_spliceai_success` (asserts `see_also` has gnomad-link in default compact) still passes.

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/tools/ tests/
git commit -m "perf: gate/collapse see_also by response_mode to cut per-call tax"
```

---

## Task 7: F5 — opportunistic cross-build probe

**Files:**
- Modify: `spliceailookup_link/mcp/tools/_common.py`
- Modify: `spliceailookup_link/mcp/tools/spliceai.py`, `pangolin.py` (combined already handled in `_predict.py`)
- Test: `tests/unit/test_eval_fixes.py`

- [ ] **Step 1: Write failing tests** (StubService grows a per-build score map):

In `tests/conftest.py`, add an opt-in mode so the stub scores only in a chosen build:

```python
        self.only_build: str | None = None  # when set, score() not_founds in the other build
```

and at the top of `score()` after recording the call:

```python
        if self.only_build is not None and build != self.only_build:
            raise DataNotFoundError("no overlapping transcript")
```

Tests:

```python
from tests.conftest import StubService, structured, DataNotFoundError


async def test_f5_cross_build_probe_upgrades_to_build_mismatch(mcp, stub_service: StubService) -> None:
    stub_service.only_build = "GRCh38"  # scores in 38, not in 37
    data = structured(await mcp.call_tool(
        "predict_spliceai", {"variant": "8-140300616-T-G", "genome_build": "GRCh37"}))
    assert data["success"] is False
    assert data["error_code"] == "build_mismatch"
    assert data["fallback_args"]["genome_build"] == "GRCh38"


async def test_f5_probe_can_be_disabled(mcp, stub_service: StubService) -> None:
    stub_service.only_build = "GRCh38"
    data = structured(await mcp.call_tool(
        "predict_spliceai",
        {"variant": "8-140300616-T-G", "genome_build": "GRCh37", "cross_build_check": False}))
    assert data["error_code"] == "not_found"
```

- [ ] **Step 2: Run — expect FAIL** (currently returns `not_found`).

- [ ] **Step 3: Add `cross_build_probe` to `_common.py`**

```python
from spliceailookup_link.api import DataNotFoundError


async def cross_build_probe(
    service: SpliceService, *, model: str, requested_build: GenomeBuild,
    variant_id: str, distance: int, mask: int, gene_set: str,
) -> GenomeBuild | None:
    """Return the OTHER build if the variant scores there (cache-backed), else None."""
    other: GenomeBuild = "GRCh37" if requested_build == "GRCh38" else "GRCh38"
    try:
        payload, _ = await service.score(
            model=model, build=other, variant_id=variant_id,
            distance=distance, mask=mask, gene_set=gene_set,
        )
    except DataNotFoundError:
        return None
    return other if payload.get("scores") else None
```

- [ ] **Step 4: Wire it into `spliceai.py` / `pangolin.py`**

Add `cross_build_check: bool = True` param. Wrap the score call:

```python
            try:
                payload, tele = await service.score(model="spliceai", **common)
            except DataNotFoundError as nf:
                if cross_build_check and prepared.resolution is None:
                    other = await cross_build_probe(
                        service, model="spliceai", requested_build=genome_build,
                        variant_id=prepared.variant_id, distance=max_distance,
                        mask=mask_to_int(mask), gene_set=gene_set)
                    if other:
                        raise BuildMismatchError(
                            variant_id=prepared.variant_id, inferred_build=other,
                            requested_build=genome_build) from nf
                raise
```

(Import `DataNotFoundError`, `BuildMismatchError`, `cross_build_probe`.) `predict_one` (Task 5) already contains the combined-tool equivalent.

- [ ] **Step 5: Run — expect PASS.** Confirm `test_predict_spliceai_success` (no `only_build`) is unaffected.

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/tools/ tests/
git commit -m "feat: auto-detect build_mismatch via cross-build probe on not_found (F5)"
```

---

## Task 8: `capabilities_version` content hash

**Files:**
- Modify: `spliceailookup_link/mcp/resources.py`
- Test: `tests/unit/test_eval_fixes.py`

- [ ] **Step 1: Write failing test**

```python
async def test_capabilities_version_is_stable(mcp) -> None:
    a = structured(await mcp.call_tool("get_server_capabilities", {}))
    b = structured(await mcp.call_tool("get_server_capabilities", {}))
    assert a["capabilities_version"] == b["capabilities_version"]
    assert len(a["capabilities_version"]) == 12
    assert isinstance(a["descriptor_chars"], int) and a["descriptor_chars"] > 0
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement in `resources.py`**

Add at module level:

```python
import hashlib
import json


def _capabilities_version(doc: dict[str, Any]) -> tuple[str, int]:
    serialized = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
    return digest, len(serialized)
```

At the END of `get_capabilities_resource()` (after the dict is built, before return), compute over a copy that excludes the version fields:

```python
    version_hash, chars = _capabilities_version(doc)
    doc["capabilities_version"] = version_hash
    doc["descriptor_chars"] = chars
    return doc
```

(Rename the local `return {...}` to `doc = {...}` so the hash is computed before returning.) Add to the doc's `response_fields` a note: `"capabilities_version": "stable content hash; re-fetch only when it changes."`

- [ ] **Step 4: Run — expect PASS** and confirm `test_capabilities_lists_tools` still passes.

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/resources.py tests/
git commit -m "feat: capabilities_version content hash + descriptor_chars"
```

---

## Task 9: `warmup` tool

**Files:**
- Modify: `spliceailookup_link/services/splice_service.py`
- Modify: `spliceailookup_link/mcp/tools/metadata.py`
- Test: `tests/unit/test_tools.py`

- [ ] **Step 1: Write failing test**

```python
async def test_warmup_pings_both_models(mcp, stub_service: StubService) -> None:
    data = structured(await mcp.call_tool("warmup", {"genome_build": "GRCh38"}))
    assert data["success"] is True
    assert data["warmed"] is True
    assert {"spliceai", "pangolin"} <= set(data["detail"])
```

`StubService` needs a `warmup` method:

```python
    async def warmup(self, build: str) -> dict[str, Any]:
        return {"spliceai": {"status": "ok", "elapsed_ms": 3},
                "pangolin": {"status": "ok", "elapsed_ms": 4}}
```

- [ ] **Step 2: Run — expect FAIL** (`Unknown tool: warmup`).

- [ ] **Step 3: Add `SpliceService.warmup` (uncached, hits the client directly)**

```python
    async def warmup(self, build: GenomeBuild) -> dict[str, Any]:
        """Wake the upstream Cloud Run containers with a known-good sentinel call."""
        sentinel = "8-140300616-T-G"
        detail: dict[str, Any] = {}
        for model in ("spliceai", "pangolin"):
            start = perf_counter()
            status = "ok"
            try:
                await self._scoring.score(
                    model=model, build=build, variant=sentinel, distance=50,
                    mask=0, gene_set="basic", raw=None, variant_consequence=None)
            except DataNotFoundError:
                status = "ok"  # a response (even not-found) means the container is warm
            except SpliceApiError:
                status = "unavailable"
            detail[model] = {"status": status, "elapsed_ms": int((perf_counter() - start) * 1000)}
        return detail
```

(Import `DataNotFoundError, SpliceApiError` from `spliceailookup_link.api`.)

- [ ] **Step 4: Register the tool in `metadata.py`**

```python
    @mcp.tool(name="warmup", title="Warm Up Upstream Scoring Containers",
              annotations=READ_ONLY_OPEN_WORLD, tags={"ops"})
    async def warmup(
        genome_build: Annotated[Literal["GRCh37", "GRCh38"],
            Field(description="Build whose scoring containers to warm. GRCh38 default.")] = "GRCh38",
    ) -> dict[str, Any]:
        """Pre-warm the SpliceAI + Pangolin Cloud Run containers before a burst so the first real call does not eat the 10-40s cold start. Returns per-model elapsed_ms. Fast when already warm. Returns <1kB."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            detail = await service.warmup(genome_build)
            warmed = all(d["status"] == "ok" for d in detail.values())
            return {"warmed": warmed, "genome_build": genome_build, "detail": detail}

        return await run_mcp_tool("warmup", call)
```

(Add `from typing import Annotated, Literal` and `from pydantic import Field` imports.)

- [ ] **Step 5: Add `warmup` to the capabilities `tools` list** in `resources.py`.

- [ ] **Step 6: Run — expect PASS + commit**

```bash
git add spliceailookup_link/ tests/
git commit -m "feat: add warmup tool to pre-warm upstream cold starts"
```

---

## Task 10: `predict_splicing_batch` tool

**Files:**
- Create: `spliceailookup_link/mcp/tools/batch.py`
- Modify: `spliceailookup_link/mcp/tools/__init__.py`
- Modify: `spliceailookup_link/mcp/tools/_common.py` (reuse `see_also_for`)
- Test: `tests/unit/test_batch.py` (new)

- [ ] **Step 1: Write failing tests** in `tests/unit/test_batch.py`:

```python
"""predict_splicing_batch fan-out."""

from __future__ import annotations

from spliceailookup_link.api import DataNotFoundError
from tests.conftest import StubService, structured


async def test_batch_scores_each_variant_once_envelope(mcp) -> None:
    res = await mcp.call_tool("predict_splicing_batch",
                              {"variants": ["chr8-140300616-T-G", "8-140300616-T-G"]})
    data = structured(res)
    assert data["success"] is True
    assert data["count"] == 2
    assert len(data["results"]) == 2
    assert "see_also" in data["_meta"]            # one block for the batch
    assert all("_meta" not in r for r in data["results"])  # per-item _meta suppressed


async def test_batch_partial_failure_does_not_fail_batch(mcp, stub_service: StubService) -> None:
    stub_service.score_error = DataNotFoundError("no overlap")
    res = await mcp.call_tool("predict_splicing_batch", {"variants": ["1-1-A-T"]})
    data = structured(res)
    assert data["success"] is True
    assert data["summary"]["failed"] == 1
    assert data["results"][0]["error_code"] == "not_found"


async def test_batch_over_cap_validation_failed(mcp) -> None:
    res = await mcp.call_tool("predict_splicing_batch", {"variants": [f"1-{i}-A-T" for i in range(26)]})
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "validation_failed"
```

- [ ] **Step 2: Run — expect FAIL** (`Unknown tool`).

- [ ] **Step 3: Create `batch.py`**

```python
"""predict_splicing_batch: score many variants in one envelope (server-side fan-out)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from pydantic import Field

from spliceailookup_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from spliceailookup_link.mcp.errors import McpErrorContext, mcp_tool_error, run_mcp_tool
from spliceailookup_link.mcp.tools._common import see_also_for
from spliceailookup_link.mcp.tools._predict import predict_one
from spliceailookup_link.services import SpliceService

_MAX_BATCH = 25


def register_batch_tools(mcp: FastMCP, *, service_factory: Callable[[], SpliceService]) -> None:
    @mcp.tool(name="predict_splicing_batch", title="Predict Splicing for Many Variants",
              annotations=READ_ONLY_OPEN_WORLD, tags={"prediction"}, task=True)
    async def predict_splicing_batch(
        variants: Annotated[list[str], Field(min_length=1, max_length=_MAX_BATCH,
            description=f"1-{_MAX_BATCH} variants (CHROM-POS-REF-ALT / HGVS / rsID).")],
        genome_build: Annotated[Literal["GRCh37", "GRCh38"], Field(description="Build. GRCh38 default.")] = "GRCh38",
        max_distance: Annotated[int, Field(ge=1, le=10000)] = 500,
        mask: Annotated[Literal["raw", "masked"], Field()] = "raw",
        gene_set: Annotated[Literal["basic", "comprehensive"], Field()] = "basic",
        transcripts: Annotated[Literal["mane", "all"], Field()] = "mane",
        response_mode: Annotated[Literal["compact", "full", "minimal"], Field()] = "compact",
        cross_build_check: Annotated[bool, Field()] = True,
        ctx: Context = None,
    ) -> dict[str, Any]:
        """Score a list of variants in ONE call. The server fans out under its concurrency cap and returns a single envelope with per-variant results (+ per-item errors that do not fail the batch) and a summary. Use this for gene panels instead of N predict_splicing calls. Returns up to ~25x a single compact result."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            results: list[dict[str, Any]] = []
            ok = failed = 0
            genes: set[str] = set()
            total = len(variants)
            for idx, variant in enumerate(variants):
                try:
                    one = await predict_one(
                        service, variant=variant, genome_build=genome_build,
                        max_distance=max_distance, mask=mask, gene_set=gene_set,
                        transcripts=transcripts, response_mode=response_mode,
                        cross_build_check=cross_build_check,
                    )
                    tel = one.pop("_telemetry")
                    if tel.get("gene"):
                        genes.add(tel["gene"])
                    one["variant"] = variant
                    results.append(one)
                    ok += 1
                except Exception as exc:  # capture per-item, never fail the batch
                    env = mcp_tool_error(exc, McpErrorContext(tool_name="predict_splicing_batch", variant=variant)).payload
                    results.append({"variant": variant, "error_code": env["error_code"],
                                    "message": env["message"], "retryable": env["retryable"]})
                    failed += 1
                if ctx is not None:
                    await ctx.report_progress(progress=idx + 1, total=total, message=f"{idx + 1}/{total}")
            concordant_high = sum(1 for r in results if r.get("agreement", {}).get("verdict") == "concordant_high")
            return {
                "count": total,
                "results": results,
                "summary": {"ok": ok, "failed": failed, "concordant_high": concordant_high},
                "_meta": {"see_also": see_also_for("", genome_build, next(iter(genes), None), response_mode)},
            }

        return await run_mcp_tool("predict_splicing_batch", call)
```

- [ ] **Step 4: Register in `mcp/tools/__init__.py`**

Add `from spliceailookup_link.mcp.tools.batch import register_batch_tools` and call `register_batch_tools(mcp, service_factory=service_factory)` inside `register_splice_tools`.

- [ ] **Step 5: Add `predict_splicing_batch` to the capabilities `tools` list + a workflow line** in `resources.py`.

- [ ] **Step 6: Run — expect PASS**

Run: `uv run pytest tests/unit/test_batch.py -v`

Note: `see_also_for("", ...)` is acceptable — for a batch the variant-specific gnomad example arg is omitted in compact/minimal anyway; in `full` mode the empty `variant_id` is benign. If a cleaner block is wanted, pass the first successful `variant_id`.

- [ ] **Step 7: LOC check + commit**

```bash
make lint-loc
git add spliceailookup_link/mcp/tools/ spliceailookup_link/mcp/resources.py tests/unit/test_batch.py
git commit -m "feat: add predict_splicing_batch (server-side fan-out)"
```

---

## Task 11: Progress notifications on scoring tools

**Files:**
- Modify: `spliceailookup_link/mcp/tools/spliceai.py`, `pangolin.py` (combined + batch already inject `ctx`)
- Test: `tests/unit/test_tools.py` (smoke — ctx optional)

- [ ] **Step 1: Add `ctx: Context = None` to `predict_spliceai` / `predict_pangolin`** and emit progress around the stages:

```python
        ctx: Context = None,
    ) -> dict[str, Any]:
        """..."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            if ctx is not None:
                await ctx.report_progress(progress=0, total=2, message="resolving")
            prepared = await prepare_variant(service, variant, genome_build)
            if ctx is not None:
                await ctx.report_progress(progress=1, total=2, message="scoring")
            ...
```

(Import `from fastmcp import Context`.) `report_progress` is a no-op without a client `progressToken`, so existing `call_tool` tests are unaffected.

- [ ] **Step 2: Run the full tool suite — expect PASS.** `uv run pytest tests/unit/test_tools.py -v`

- [ ] **Step 3: Commit**

```bash
git add spliceailookup_link/mcp/tools/
git commit -m "feat: progress notifications during long scoring calls"
```

---

## Task 12: Native MCP background Tasks

**Files:**
- Modify: `pyproject.toml`, `uv.lock`
- Modify: `spliceailookup_link/config.py`
- Modify: `spliceailookup_link/mcp/facade.py`
- Modify: `spliceailookup_link/mcp/tools/spliceai.py`, `pangolin.py`, `combined.py` (`task=True`; batch already has it)
- Test: `tests/unit/test_tools.py` (server builds with tasks enabled)

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml`: change `"fastmcp>=3.2.0,<4.0.0"` to `"fastmcp[tasks]>=3.2.0,<4.0.0"`. Then:

Run: `uv lock && uv sync --group dev`
Expected: Docket resolves and installs.

- [ ] **Step 2: Add `DOCKET_URL` to config**

In `Settings` (`config.py`):

```python
    # Background-task (FastMCP Tasks / Docket) backend. memory:// is in-process and
    # correct for the single-process unified host; set redis://... for multi-worker.
    DOCKET_URL: str = "memory://"
```

- [ ] **Step 3: Enable tasks on the server + tools**

`facade.py`:

```python
    mcp = FastMCP(
        name="spliceailookup-link",
        instructions=_INSTRUCTIONS,
        mask_error_details=True,
        tasks=True,
    )
```

Add `task=True` to the `@mcp.tool(...)` decorators of `predict_spliceai`, `predict_pangolin`, `predict_splicing` (batch already set).

- [ ] **Step 4: Guard import resilience**

If `FastMCP(tasks=True)` raises at construction when Docket is missing, wrap once:

```python
    try:
        mcp = FastMCP(name="spliceailookup-link", instructions=_INSTRUCTIONS, mask_error_details=True, tasks=True)
    except (ImportError, RuntimeError) as exc:  # tasks extra unavailable
        logger.warning("FastMCP tasks disabled (%s); falling back to synchronous tools", exc)
        mcp = FastMCP(name="spliceailookup-link", instructions=_INSTRUCTIONS, mask_error_details=True)
```

(Add `import logging` + `logger = logging.getLogger(__name__)`.) The `task=True` decorator argument must be tolerated when tasks are off; if FastMCP rejects it, set tasks via a module flag — verify in Step 6 and adjust.

- [ ] **Step 5: Run the unit suite — expect PASS** (synchronous `call_tool` behavior is unchanged with tasks enabled).

Run: `uv run pytest -q`

- [ ] **Step 6: MANUAL verification against the running container** (record results in the PR/commit body):

```bash
make docker-build && make docker-up
# 1) normal call still works:
#    use an MCP client / curl the streamable endpoint at http://127.0.0.1:8603/mcp
# 2) task-augmented predict_splicing returns a taskId, and tasks/result yields the payload
# 3) cold call emits notifications/progress
```

If task wiring is unstable, KEEP progress notifications, remove `task=True` + `tasks=True`, and note the deferral in `docs/mcp-evaluation.md` follow-up. (Progress alone still lifts the latency dimension.)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock spliceailookup_link/config.py spliceailookup_link/mcp/
git commit -m "feat: native MCP background tasks (task=True) + Docket backend"
```

---

## Task 13: CI coverage gaps + docs + version bump

**Files:**
- Modify: `tests/fixtures/api_responses.py` (`SPLICEAI_TRAPPC9_ALL` with a non-MANE transcript)
- Test: `tests/unit/test_eval_fixes.py`, `tests/unit/test_shaping.py`
- Modify: `pyproject.toml` (version), `spliceailookup_link/mcp/resources.py` (limitations/glossary notes), `README.md`

- [ ] **Step 1: Add a 2-transcript fixture** (one MANE, one non-MANE) in `api_responses.py`:

```python
SPLICEAI_TRAPPC9_ALL: dict[str, Any] = {
    **{k: v for k, v in SPLICEAI_TRAPPC9.items() if k != "scores"},
    "scores": [
        SPLICEAI_TRAPPC9["scores"][0],
        {**SPLICEAI_TRAPPC9["scores"][0], "t_id": "ENST00000522608.1",
         "t_priority": "N", "t_refseq_ids": []},
    ],
}
```

- [ ] **Step 2: Add coverage tests** (eval improvement #6) in `tests/unit/test_shaping.py` + `test_eval_fixes.py`:

```python
def test_transcripts_all_returns_non_mane():
    from spliceailookup_link.mcp.shaping import shape_spliceai
    from tests.fixtures.api_responses import SPLICEAI_TRAPPC9_ALL
    out = shape_spliceai(SPLICEAI_TRAPPC9_ALL, transcripts="all", response_mode="compact")
    priorities = {t["transcript_priority"] for t in out["transcripts"]}
    assert len(out["transcripts"]) >= 2
    assert "non-canonical" in priorities


async def test_minimal_strictly_smaller_than_compact(mcp):
    import json
    from tests.conftest import structured
    c = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G", "response_mode": "compact"}))
    m = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G", "response_mode": "minimal"}))
    assert len(json.dumps(m)) < len(json.dumps(c))


async def test_out_of_range_max_distance_is_validation_failed(mcp):
    from tests.conftest import structured
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G", "max_distance": 99999}))
    assert data["success"] is False
    assert data["error_code"] == "validation_failed"
```

- [ ] **Step 3: Run — fix any gaps** (e.g. if `minimal` is not strictly smaller, ensure `minimal` also drops `see_also` and `delta_scores` extras — it already trims transcripts to 1 and Task 6 drops `see_also`).

Run: `uv run pytest tests/unit -v`

- [ ] **Step 4: Update docs**

In `resources.py` `get_capabilities_resource()`:
- add the `mask=masked` empty-aberration note to `score_glossary.sai10k_consequence`;
- add `predict_splicing_batch` + `warmup` to `tools` and a batch workflow line;
- note `_meta.cache` / `timing` / `request_id` / `capabilities_version` in `response_fields`.

In `README.md`: add `predict_splicing_batch` and `warmup` to the Tools table; document `_meta` observability fields and the `capabilities_version` warm-client contract; mention `SPLICEAILOOKUP_LINK_DOCKET_URL`.

- [ ] **Step 5: Bump version** to `0.2.0` in `pyproject.toml`.

- [ ] **Step 6: Commit**

```bash
git add tests/ spliceailookup_link/mcp/resources.py README.md pyproject.toml
git commit -m "test+docs: coverage gaps, observability docs, v0.2.0"
```

---

## Task 14: Full CI gate + re-evaluation

- [ ] **Step 1: Run the full local CI**

Run: `make ci-local`
Expected: format-check, lint, **lint-loc** (all modules < 600), typecheck (mypy clean), tests, coverage ≥ 80% — all green. Fix any failures before proceeding.

- [ ] **Step 2: Re-run the evaluation method** from `docs/mcp-evaluation.md` against the running container (the same 5 tools + 5 resources + the new batch/warmup), and confirm:
  - F1: `resolve_variant("rs6025")` → parseable `variant_id`, `ambiguous:true`, two clean `next_commands`.
  - F2: masked `predict_spliceai` → `consequence.aberrations` present (possibly `[]`); `full` adds `transcript_info`.
  - F3: `predict_splicing._meta.next_commands` present.
  - F4: single `consequence`, single top-level `transcript`.
  - F5: GRCh38 coordinate at `genome_build=GRCh37` → `build_mismatch`.
  - Observability: `_meta.request_id` / `timing` / `cache` on every call.
  - Latency: progress notifications fire; `warmup` then `predict_splicing` is faster; task-augmented call returns a `taskId`.

- [ ] **Step 3: Record the new scores** by updating `docs/mcp-evaluation.md` (or adding `docs/mcp-evaluation-2.md`) with the re-rated dimensions and a short note per finding marking it resolved. Target: both axes > 9/10.

- [ ] **Step 4: Final commit**

```bash
git add docs/
git commit -m "docs: re-evaluation after eval-driven improvements (>9/10)"
```

---

## Self-Review

**Spec coverage:** F1 → T1; observability `request_id`/`timing` → T2; `cache`/`upstream_elapsed_ms` → T3; F2 → T4; F3+F4 → T5; `see_also` policy → T6; F5 → T7; `capabilities_version` → T8; warmup → T9; batch → T10; progress → T11; native Tasks + `DOCKET_URL` + deps → T12; CI coverage gaps + docs + version → T13; CI gate + re-eval → T14. All twelve spec scope items mapped.

**Placeholder scan:** No "TBD"/"handle edge cases"/"similar to" — each code step shows complete function bodies. The one deliberately manual step (T12.6 live task round-trip) is explicit about commands and the documented fallback.

**Type consistency:** `score()` returns `tuple[dict, CallTelemetry]` (T3) and is unpacked identically in `spliceai.py`/`pangolin.py`/`_predict.py`/`cross_build_probe`/`StubService`. `CallTelemetry(cache, upstream_elapsed_ms)` fields match everywhere. `see_also_for(variant_id, genome_build, gene, response_mode)` signature is consistent across T3/T5/T6/T10 (T3 adds the keys; T6 adds the `response_mode` arg and updates the same call sites). `predict_one(...)` keyword signature matches its callers in `combined.py` (T5) and `batch.py` (T10). `_telemetry` scratch key is set in `predict_one` and `pop`ped by both callers. `after_resolve_many`/`for_combined`/`cross_build_probe` are defined before first use.

**Note on cross-cutting edits:** Tasks 3, 6, 7, 11, 12 each touch `spliceai.py`/`pangolin.py` for a different concern (telemetry, see_also arg, cross-build, progress, task=True). Apply them in order; later tasks layer onto earlier edits rather than conflicting.
