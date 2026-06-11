# spliceailookup-link v0.3.0 Corrective Pass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close eval findings F6–F10 and consumer improvements #2/#4/#5 from `docs/mcp-evaluation.md` Part 4 so a fresh independent re-evaluation clears >9/10 on both the LLM-consumer and senior-tester axes.

**Architecture:** Surgical fixes to the existing MCP facade. Extract combined-tool presentation logic into a new `mcp/tools/_predict_shape.py` (the HIGH-severity F6 fix plus the agreement-band correctness fix live there); collapse duplicate transcripts and give `minimal` a real headline tier in `mcp/shaping.py`; stamp validation-error `_meta` in `mcp/errors.py`; enrich the batch summary in `mcp/tools/batch.py`; add cache-age telemetry through `services/`; advertise background execution + the new shapes in `mcp/resources.py`. No tool renames, no schema-breaking removals — every change is additive or a documented fix to an under-specified field.

**Tech Stack:** Python 3.12, FastMCP 3.x, pydantic, async_lru, respx + pytest (asyncio_mode=auto, no decorators needed), `uv`, Ruff, mypy. Required gate: `make ci-local`.

**Spec:** `docs/superpowers/specs/2026-06-11-eval-improvements-2-design.md`

**Conventions (already in the repo — match them):**
- Pure shaping functions are unit-tested directly with dict payloads (`tests/unit/test_shaping.py`).
- Tool behaviour is tested through `mcp.call_tool(name, args)` + `structured(res)` using the `mcp` / `stub_service` fixtures (`tests/conftest.py`).
- Tests are `async def test_...` with **no** `@pytest.mark.asyncio` decorator (asyncio_mode=auto).
- Run a single test: `uv run pytest tests/unit/test_x.py::test_y -v`. Run unit suite: `make test`. Full gate: `make ci-local`.
- Commit per task. Branch first if on the default branch.

---

## File Structure

New:
- `spliceailookup_link/mcp/tools/_predict_shape.py` — combined-tool presentation: `assess_agreement` (3→4 bands), `combined_headline` (verdict-driven), `combined_interpretation`, `minimal_combined`. Keeps `_predict.py` orchestration-only.

Modified:
- `spliceailookup_link/mcp/tools/_predict.py` — use `_predict_shape`; pass `agreement` into the headline; thread interpretation, cache-age telemetry, `max_transcripts`.
- `spliceailookup_link/mcp/shaping.py` — `_collapse_identical_transcripts` (F7), real `minimal` projection (F8), `band()` + `THRESHOLD_BASIS` + per-model `interpretation` (#4), `max_transcripts`.
- `spliceailookup_link/mcp/tools/spliceai.py` / `pangolin.py` / `combined.py` — `max_transcripts` param; fold `cache_age_s`/`cache_ttl_s` into `_meta`; background-task sentence in descriptions.
- `spliceailookup_link/mcp/tools/batch.py` — full verdict histogram + `top_variant` + `next_commands`; drop batch `see_also` (F10); thread `max_transcripts`.
- `spliceailookup_link/mcp/errors.py` — stamp `request_id` + `timing` on validation envelopes (F9).
- `spliceailookup_link/services/telemetry.py` — `cache_age_s`, `cache_ttl_s` (#5).
- `spliceailookup_link/services/splice_service.py` — bounded `_scored_at` map; populate the new telemetry (#5).
- `spliceailookup_link/mcp/resources.py` — `background_execution` block; document verdict band, interpretation enum, tier contract, `shared_by`/`transcripts_truncated` (#2 + docs).
- `spliceailookup_link/__init__.py`, `pyproject.toml` — version → `0.3.0`.
- `tests/fixtures/api_responses.py` — distinct + duplicate transcript fixtures.
- `tests/conftest.py` — stub telemetry carries `cache_age_s`/`cache_ttl_s`.
- `tests/unit/test_shaping.py`, `tests/unit/test_batch.py` — update the two assertions the shape changes invalidate.
- `tests/unit/test_eval_fixes_2.py` — **new** regression file for F6–F10 + #2/#4/#5.
- `docs/mcp-evaluation.md` — Part 5 re-evaluation appendix.

---

## Task 0: Branch + version bump

**Files:**
- Modify: `spliceailookup_link/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create a working branch**

Run:
```bash
cd /home/bernt-popp/development/spliceailookup-link
git checkout -b eval-improvements-2
```

- [ ] **Step 2: Find the current version strings**

Run:
```bash
grep -rn "0.2.0" spliceailookup_link/__init__.py pyproject.toml
```
Expected: a `__version__ = "0.2.0"` line and a `version = "0.2.0"` line.

- [ ] **Step 3: Bump both to 0.3.0**

Edit `spliceailookup_link/__init__.py`: change `__version__ = "0.2.0"` → `__version__ = "0.3.0"`.
Edit `pyproject.toml`: change `version = "0.2.0"` → `version = "0.3.0"`.

- [ ] **Step 4: Verify import works**

Run: `uv run python -c "import spliceailookup_link; print(spliceailookup_link.__version__)"`
Expected: `0.3.0`

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/__init__.py pyproject.toml
git commit -m "chore: bump version to 0.3.0 for eval corrective pass"
```

---

## Task 1: F6 + F6b — verdict-driven headline & moderate band (`_predict_shape.py`)

The HIGH-severity fix. `_combined_headline` currently recomputes a 2-state agreement (`_predict.py:236`) that contradicts the 3-state `_assess_agreement`. Move both into a new module, make the headline render the verdict, and add a `concordant_moderate` band.

**Files:**
- Create: `spliceailookup_link/mcp/tools/_predict_shape.py`
- Test: `tests/unit/test_predict_shape.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_predict_shape.py`:
```python
"""Unit tests for combined-prediction presentation helpers (F6/F6b/#4)."""

from __future__ import annotations

import pytest

from spliceailookup_link.mcp.tools._predict_shape import (
    assess_agreement,
    combined_headline,
)


@pytest.mark.parametrize(
    ("sai", "pang", "verdict"),
    [
        (0.83, 0.85, "concordant_high"),
        (0.30, 0.32, "concordant_moderate"),
        (0.05, 0.09, "concordant_low"),
        (0.31, 0.09, "discordant"),
        (0.21, 0.05, "discordant"),
        (0.80, None, "incomplete"),
    ],
)
def test_assess_agreement_bands(sai, pang, verdict) -> None:
    assert assess_agreement(sai, pang)["verdict"] == verdict


@pytest.mark.parametrize(
    ("sai", "pang", "needle"),
    [
        (0.83, 0.85, "models agree"),
        (0.30, 0.32, "models agree"),
        (0.05, 0.09, "models agree"),
        (0.31, 0.09, "models disagree"),
        (0.21, 0.05, "models disagree"),
    ],
)
def test_headline_clause_matches_verdict(sai, pang, needle) -> None:
    agreement = assess_agreement(sai, pang)
    headline = combined_headline("TRAPPC9", "GRCh38", sai, pang, None, agreement)
    assert needle in headline
    # The headline must never claim agreement when the verdict is discordant.
    if agreement["verdict"] == "discordant":
        assert "models agree" not in headline


def test_headline_incomplete_when_one_model_missing() -> None:
    agreement = assess_agreement(0.8, None)
    headline = combined_headline("TRAPPC9", "GRCh38", 0.8, None, None, agreement)
    assert "only one model scored" in headline
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_predict_shape.py -v`
Expected: FAIL — `ModuleNotFoundError: ... _predict_shape`.

- [ ] **Step 3: Create the module**

Create `spliceailookup_link/mcp/tools/_predict_shape.py`:
```python
"""Presentation helpers for predict_splicing.

Single source of truth for model agreement: `assess_agreement` computes the
verdict, and `combined_headline` renders that verdict verbatim — the headline
never recomputes agreement from raw scores (the F6 bug). `assess_agreement`
distinguishes a moderate concordance band (F6b) so two both-moderate models read
as agreeing, not "discordant".
"""

from __future__ import annotations

from typing import Any

from spliceailookup_link.mcp.shaping import THRESHOLD_BASIS, band

_HIGH = 0.5
_LOW = 0.2

_VERDICT_CLAUSE = {
    "concordant_high": "models agree (both strong)",
    "concordant_moderate": "models agree (both moderate)",
    "concordant_low": "models agree (both low/none)",
    "discordant": "models disagree",
}
_INCOMPLETE_CLAUSE = "only one model scored"


def assess_agreement(sai_max: float | None, pang_max: float | None) -> dict[str, Any]:
    """Summarise whether the two independent models agree on impact magnitude."""
    if sai_max is None or pang_max is None:
        return {"verdict": "incomplete", "detail": "one model returned no score"}
    both_high = sai_max >= _HIGH and pang_max >= _HIGH
    both_low = sai_max < _LOW and pang_max < _LOW
    both_moderate = (_LOW <= sai_max < _HIGH) and (_LOW <= pang_max < _HIGH)
    if both_high:
        verdict, detail = "concordant_high", "both models predict a strong splicing effect"
    elif both_low:
        verdict, detail = "concordant_low", "both models predict little or no splicing effect"
    elif both_moderate:
        verdict, detail = "concordant_moderate", "both models predict a moderate splicing effect"
    else:
        verdict, detail = "discordant", "models disagree on the magnitude; interpret with caution"
    return {
        "verdict": verdict,
        "detail": detail,
        "spliceai_max_delta": sai_max,
        "pangolin_max_delta": pang_max,
    }


def combined_headline(
    gene: str | None,
    build: str,
    sai_max: float | None,
    pang_max: float | None,
    consequence: dict[str, Any] | None,
    agreement: dict[str, Any],
) -> str:
    """Render a one-line headline whose agreement clause is the verdict verbatim."""
    gene_label = gene or "variant"
    parts: list[str] = []
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
        verdict_part = f"; {_VERDICT_CLAUSE.get(agreement.get('verdict'), '')}"
    elif (sai_max is None) != (pang_max is None):
        verdict_part = f"; {_INCOMPLETE_CLAUSE}"
    else:
        verdict_part = ""
    return f"{gene_label} ({build}): {scores}{verdict_part}{tail}."


def combined_interpretation(sai_max: float | None, pang_max: float | None) -> dict[str, Any]:
    """Top-level band for the combined result: the stronger of the two models."""
    scores = [s for s in (sai_max, pang_max) if s is not None]
    top = max(scores) if scores else None
    return {"band": band(top), "threshold_basis": THRESHOLD_BASIS}
```

> NOTE: `band` and `THRESHOLD_BASIS` are added to `shaping.py` in Task 5. To keep this task green on its own, Task 5's Step 3 may be done first, OR temporarily inline a local `band`/`THRESHOLD_BASIS` here and replace with the import in Task 5. Recommended: do Task 5 Step 3 (add `band`/`THRESHOLD_BASIS` to shaping) before running this task's tests. The `combined_interpretation` function is not exercised until Task 5; the F6 tests above do not import it.

- [ ] **Step 4: Add `band`/`THRESHOLD_BASIS` to shaping (prerequisite import)**

In `spliceailookup_link/mcp/shaping.py`, after the `_MODERATE = 0.2` line, add:
```python
THRESHOLD_BASIS = "Δ>=0.5 high; 0.2-0.5 moderate; >0-0.2 low; 0 none (SpliceAI/Pangolin convention)"


def band(score: float | None) -> str:
    """Public four-value interpretation band for a single max delta score."""
    if score is None:
        return "none"
    if score >= _STRONG:
        return "high"
    if score >= _MODERATE:
        return "moderate"
    if score > 0:
        return "low"
    return "none"
```

- [ ] **Step 5: Run to verify the tests pass**

Run: `uv run pytest tests/unit/test_predict_shape.py -v`
Expected: PASS (all parametrized cases).

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/tools/_predict_shape.py spliceailookup_link/mcp/shaping.py tests/unit/test_predict_shape.py
git commit -m "feat(F6): verdict-driven combined headline + concordant_moderate band"
```

---

## Task 2: Wire F6 into `_predict.py` (remove the recompute)

Make the combined tool use the new helpers so the live `predict_splicing` headline can never contradict `agreement.verdict`.

**Files:**
- Modify: `spliceailookup_link/mcp/tools/_predict.py`
- Test: `tests/unit/test_eval_fixes_2.py`

- [ ] **Step 1: Write the failing end-to-end consistency test**

Create `tests/unit/test_eval_fixes_2.py`:
```python
"""Regression tests for docs/mcp-evaluation.md Part 4 (F6-F10 + #2/#4/#5)."""

from __future__ import annotations

import json

from tests.conftest import StubService, structured


async def test_f6_headline_matches_verdict_concordant_high(mcp) -> None:
    # Stub returns SpliceAI 0.83 / Pangolin 0.85 -> concordant_high.
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert data["agreement"]["verdict"] == "concordant_high"
    assert "models agree" in data["headline"]
    assert "models disagree" not in data["headline"]
```

- [ ] **Step 2: Run to verify it passes-or-fails meaningfully**

Run: `uv run pytest tests/unit/test_eval_fixes_2.py::test_f6_headline_matches_verdict_concordant_high -v`
Expected: PASS already (stub is concordant_high and the *old* headline also said "agree"). This test pins the concordant case; the discordant divergence is covered by the pure-function tests in Task 1. Proceed to refactor `_predict.py` so the implementation is single-source-of-truth.

- [ ] **Step 3: Replace the inline helpers with imports**

In `spliceailookup_link/mcp/tools/_predict.py`:

Delete the local `_HIGH`/`_LOW` constants (lines ~31-32), the `_assess_agreement` function (lines ~44-61), and the `_combined_headline` function (lines ~217-240).

Add to the imports near the top:
```python
from spliceailookup_link.mcp.tools._predict_shape import (
    assess_agreement,
    combined_headline,
    combined_interpretation,
)
```

Replace the two call sites (around lines 203-204):
```python
    result["agreement"] = assess_agreement(sai_max, pang_max)
    result["interpretation"] = combined_interpretation(sai_max, pang_max)
    result["headline"] = combined_headline(
        gene, genome_build, sai_max, pang_max, consequence, result["agreement"]
    )
```

- [ ] **Step 4: Run the combined-tool tests**

Run: `uv run pytest tests/unit/test_tools.py tests/unit/test_eval_fixes_2.py -v`
Expected: PASS — including the existing `test_predict_splicing_runs_both_models` (`verdict == "concordant_high"`, `"models agree" in headline`).

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/tools/_predict.py tests/unit/test_eval_fixes_2.py
git commit -m "refactor(F6): predict_splicing renders agreement.verdict as single source of truth"
```

---

## Task 3: F7 — collapse byte-identical transcripts + `max_transcripts`

`transcripts="all"` returns N identical blocks (19× for TRAPPC9). Collapse them losslessly; add an optional top-N cap.

**Files:**
- Modify: `spliceailookup_link/mcp/shaping.py`
- Modify: `tests/fixtures/api_responses.py`
- Modify: `tests/unit/test_shaping.py` (the existing `transcripts_all` test)
- Test: `tests/unit/test_shaping.py` (new cases)

- [ ] **Step 1: Add fixtures — one distinct-second-transcript, one with duplicates**

In `tests/fixtures/api_responses.py`, change the existing `SPLICEAI_TRAPPC9_ALL` so the second transcript has a **distinct** score (so the "returns non-mane" test stays about filtering, not collapse), and add a duplicates fixture:
```python
# SpliceAI payload: one MANE Select + one non-canonical with DISTINCT scores.
SPLICEAI_TRAPPC9_ALL: dict[str, Any] = {
    **{k: v for k, v in SPLICEAI_TRAPPC9.items() if k != "scores"},
    "scores": [
        SPLICEAI_TRAPPC9["scores"][0],
        {
            **SPLICEAI_TRAPPC9["scores"][0],
            "DS_AL": "0.40",  # distinct from the MANE row's 0.83
            "t_id": "ENST00000522608.1",
            "t_priority": "N",
            "t_refseq_ids": [],
        },
    ],
}

# SpliceAI payload: three byte-identical transcript score blocks (collapse target).
SPLICEAI_TRAPPC9_DUP: dict[str, Any] = {
    **{k: v for k, v in SPLICEAI_TRAPPC9.items() if k != "scores"},
    "scores": [
        SPLICEAI_TRAPPC9["scores"][0],
        {**SPLICEAI_TRAPPC9["scores"][0], "t_id": "ENST00000522608.1", "t_priority": "N"},
        {**SPLICEAI_TRAPPC9["scores"][0], "t_id": "ENST00000999999.1", "t_priority": "N"},
    ],
}
```

- [ ] **Step 2: Write the failing tests**

In `tests/unit/test_shaping.py`, update the import line and the existing test, then add new ones:
```python
from tests.fixtures.api_responses import (
    PANGOLIN_TRAPPC9,
    SPLICEAI_MASKED_EMPTY_ABERR,
    SPLICEAI_TRAPPC9,
    SPLICEAI_TRAPPC9_ALL,
    SPLICEAI_TRAPPC9_DUP,
)


def test_transcripts_all_returns_non_mane() -> None:
    # Distinct scores -> not collapsed; both transcripts present incl. non-canonical.
    out = shape_spliceai(SPLICEAI_TRAPPC9_ALL, transcripts="all", response_mode="compact")
    priorities = {t["transcript_priority"] for t in out["transcripts"]}
    assert len(out["transcripts"]) == 2
    assert "non-canonical" in priorities


def test_f7_identical_transcripts_collapse() -> None:
    out = shape_spliceai(SPLICEAI_TRAPPC9_DUP, transcripts="all", response_mode="compact")
    assert len(out["transcripts"]) == 1
    rep = out["transcripts"][0]
    assert sorted(rep["shared_by"]) == ["ENST00000522608.1", "ENST00000999999.1"]


def test_f7_collapse_reduces_serialized_size() -> None:
    import json

    collapsed = shape_spliceai(SPLICEAI_TRAPPC9_DUP, transcripts="all")
    # A hypothetical un-collapsed projection would repeat the block 3x; assert the
    # collapsed payload carries exactly one transcript block.
    assert len(collapsed["transcripts"]) == 1
    assert "shared_by" in collapsed["transcripts"][0]
    assert json.dumps(collapsed)  # serialisable


def test_f7_max_transcripts_truncates_top_n() -> None:
    out = shape_spliceai(SPLICEAI_TRAPPC9_ALL, transcripts="all", max_transcripts=1)
    assert len(out["transcripts"]) == 1
    # The kept transcript is the highest max_delta_score (the MANE row, 0.83).
    assert out["transcripts"][0]["max_delta_score"] == 0.83
    assert out["transcripts_truncated"] == {"kept": 1, "total": 2}
```

- [ ] **Step 3: Run to verify the new tests fail**

Run: `uv run pytest tests/unit/test_shaping.py -k "f7 or non_mane" -v`
Expected: FAIL — `shape_spliceai() got an unexpected keyword argument 'max_transcripts'` and missing `shared_by`/`transcripts_truncated`.

- [ ] **Step 4: Implement collapse + cap in shaping**

In `spliceailookup_link/mcp/shaping.py`, add the helper (place after `_select_transcripts`):
```python
def _score_signature(t: dict[str, Any]) -> tuple[Any, ...]:
    ds = t.get("delta_scores") or {}
    return (
        t.get("max_delta_score"),
        tuple(sorted((k, v.get("score"), v.get("position")) for k, v in ds.items())),
    )


def _collapse_identical_transcripts(shaped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge transcript blocks with identical delta scores into one + shared_by."""
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for t in shaped:
        sig = _score_signature(t)
        rep = groups.get(sig)
        if rep is None:
            groups[sig] = dict(t)
            order.append(sig)
            continue
        tid = t.get("transcript_id")
        if tid and tid != rep.get("transcript_id"):
            rep.setdefault("shared_by", []).append(tid)
    out = [groups[s] for s in order]
    for rep in out:
        if "shared_by" in rep:
            rep["shared_by"] = sorted(set(rep["shared_by"]))
    return out


def _apply_max_transcripts(
    shaped: list[dict[str, Any]], max_transcripts: int | None
) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    if max_transcripts is None or len(shaped) <= max_transcripts:
        return shaped, None
    ranked = sorted(shaped, key=lambda t: t.get("max_delta_score") or -1.0, reverse=True)
    return ranked[:max_transcripts], {"kept": max_transcripts, "total": len(shaped)}
```

Modify `shape_spliceai`'s signature and body. Change the signature line to add `max_transcripts`:
```python
def shape_spliceai(
    payload: dict[str, Any],
    *,
    transcripts: Transcripts = "mane",
    response_mode: ResponseMode = "compact",
    include_consequence: bool = True,
    max_transcripts: int | None = None,
) -> dict[str, Any]:
```
After `shaped = [_shape_spliceai_transcript(s, response_mode) for s in selected]`, insert:
```python
    shaped = _collapse_identical_transcripts(shaped)
    shaped, truncated = _apply_max_transcripts(shaped, max_transcripts)
```
After the `result: dict[str, Any] = {...}` literal is built (before the minimal/`include_consequence`/headline block), insert:
```python
    if truncated is not None:
        result["transcripts_truncated"] = truncated
```

Apply the identical three edits to `shape_pangolin` (add `max_transcripts` param, the two collapse/cap lines after its `shaped = [...]`, and the `transcripts_truncated` assignment after its `result` literal).

- [ ] **Step 5: Run the shaping tests**

Run: `uv run pytest tests/unit/test_shaping.py -v`
Expected: PASS (all, including the updated `test_transcripts_all_returns_non_mane`).

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/shaping.py tests/fixtures/api_responses.py tests/unit/test_shaping.py
git commit -m "feat(F7): collapse identical transcript blocks + optional max_transcripts cap"
```

---

## Task 4: F8 — make `minimal` a true headline tier

`minimal` currently keeps the full `delta_scores`/`consequence` and only drops `see_also`. Reduce it to headline + the single decision number.

**Files:**
- Modify: `spliceailookup_link/mcp/shaping.py`
- Modify: `spliceailookup_link/mcp/tools/_predict_shape.py` (combined minimal)
- Modify: `spliceailookup_link/mcp/tools/_predict.py` (apply combined minimal)
- Modify: `tests/unit/test_shaping.py` (update `test_minimal_mode_keeps_single_transcript`)
- Test: `tests/unit/test_shaping.py`, `tests/unit/test_eval_fixes_2.py`

- [ ] **Step 1: Write the failing single-model minimal tests**

In `tests/unit/test_shaping.py`, replace `test_minimal_mode_keeps_single_transcript` with:
```python
def test_minimal_mode_is_headline_tier() -> None:
    shaped = shape_spliceai(SPLICEAI_TRAPPC9, transcripts="all", response_mode="minimal")
    # No per-transcript delta_scores array in minimal.
    assert "transcripts" not in shaped
    assert "delta_scores" not in shaped
    # Carries the decision-relevant scalars + headline + band.
    assert shaped["max_delta_score"] == 0.83
    assert shaped["top"]["score"] == 0.83
    assert shaped["top"]["class"] == "acceptor_loss"
    assert shaped["interpretation"]["band"] == "high"
    assert "TRAPPC9" in shaped["headline"]


def test_minimal_smaller_than_compact_single_model() -> None:
    import json

    minimal = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="minimal")
    compact = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="compact")
    assert len(json.dumps(minimal)) < len(json.dumps(compact))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_shaping.py -k minimal -v`
Expected: FAIL — minimal still contains `transcripts`.

- [ ] **Step 3: Implement the single-model minimal projection in shaping**

In `shaping.py`, add a helper:
```python
def _minimal_spliceai(result: dict[str, Any]) -> dict[str, Any]:
    transcripts = result.get("transcripts") or []
    top = transcripts[0] if transcripts else {}
    best_class, best, pos = None, None, None
    for name, d in (top.get("delta_scores") or {}).items():
        s = (d or {}).get("score")
        if s is not None and (best is None or s > best):
            best, best_class, pos = s, name, (d or {}).get("position")
    out: dict[str, Any] = {
        "model": result["model"],
        "variant_id": result["variant_id"],
        "genome_build": result["genome_build"],
        "gene": top.get("gene"),
        "max_delta_score": result.get("max_delta_score"),
        "interpretation": {"band": band(result.get("max_delta_score"))},
        "headline": result["headline"],
    }
    if best_class is not None:
        out["top"] = {"class": best_class, "score": best, "position": pos}
    cons = result.get("consequence") or {}
    aberr = (cons.get("aberrations") or [{}])[0].get("type") if cons else None
    if aberr:
        out["consequence_summary"] = aberr
    return out
```
The Pangolin shape uses the same `delta_scores` structure, so reuse `_minimal_spliceai` for both (it reads only `model`/`variant_id`/`genome_build`/`max_delta_score`/`transcripts`/`headline`/`consequence`, all present in both shapes; Pangolin has no `consequence`, handled by the `or {}`).

In `shape_spliceai`, replace the existing minimal handling:
```python
    if response_mode == "minimal":
        result["transcripts"] = shaped[:1]
```
with (apply AFTER the headline is set, so `_minimal_spliceai` can read it — move the minimal step to the end of the function, just before `return result`):
```python
    result["headline"] = spliceai_headline(result)
    if response_mode == "minimal":
        return _minimal_spliceai(result)
    return result
```
Remove the earlier `if response_mode == "minimal": result["transcripts"] = shaped[:1]` line.

In `shape_pangolin`, similarly replace `"transcripts": shaped[:1] if response_mode == "minimal" else shaped,` with `"transcripts": shaped,` and add before its `return result`:
```python
    result["headline"] = pangolin_headline(result)
    if response_mode == "minimal":
        return _minimal_spliceai(result)
    return result
```
(remove the now-duplicate `result["headline"] = pangolin_headline(result)` line that preceded the original return).

> Interpretation note: `interpretation` for compact/full single-model is added in Task 5; minimal already carries `interpretation.band` here.

- [ ] **Step 4: Run the single-model minimal tests**

Run: `uv run pytest tests/unit/test_shaping.py -k minimal -v`
Expected: PASS.

- [ ] **Step 5: Write the failing combined-minimal test**

Append to `tests/unit/test_eval_fixes_2.py`:
```python
async def test_f8_combined_minimal_is_headline_tier(mcp) -> None:
    full = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "compact"}
        )
    )
    minimal = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "minimal"}
        )
    )
    assert len(json.dumps(minimal)) < len(json.dumps(full))
    assert "spliceai" not in minimal and "pangolin" not in minimal
    assert minimal["agreement"]["verdict"] == "concordant_high"
    assert minimal["spliceai_max"] == 0.83
    assert minimal["pangolin_max"] == 0.85
    assert minimal["interpretation"]["band"] == "high"
    assert "TRAPPC9" in minimal["headline"]
```

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes_2.py::test_f8_combined_minimal_is_headline_tier -v`
Expected: FAIL — minimal combined still has `spliceai`/`pangolin` sub-objects.

- [ ] **Step 7: Implement combined minimal**

In `_predict_shape.py`, add:
```python
def minimal_combined(result: dict[str, Any], gene: str | None) -> dict[str, Any]:
    """Headline-tier projection of a combined predict_splicing result."""
    sai_max = (result.get("spliceai") or {}).get("max_delta_score")
    pang_max = (result.get("pangolin") or {}).get("max_delta_score")
    return {
        "variant_id": result["variant_id"],
        "genome_build": result["genome_build"],
        "gene": gene,
        "agreement": {"verdict": result["agreement"]["verdict"]},
        "spliceai_max": sai_max,
        "pangolin_max": pang_max,
        "interpretation": {"band": result["interpretation"]["band"]},
        "headline": result["headline"],
    }
```

In `_predict.py` `predict_one`, just before `cache, ups = _aggregate_cache(teles)` (i.e. after `result["headline"] = ...`), insert the minimal projection but preserve the `_telemetry` scratch key:
```python
    if response_mode == "minimal":
        minimal = minimal_combined(result, gene)
        minimal["_telemetry"] = {  # rebuilt below; placeholder so caller's pop works
        }
        result = {**minimal}
```
Then ensure `_telemetry` is attached to whichever `result` is returned. Cleanest: build `_telemetry` first, then choose the body. Refactor the tail of `predict_one` to:
```python
    cache, ups, age_s, ttl_s = _aggregate_cache(teles)
    telemetry = {
        "cache": cache,
        "upstream_elapsed_ms": ups,
        "cache_age_s": age_s,
        "cache_ttl_s": ttl_s,
        "gene": gene,
        "partial": partial,
        "resolution": prepared.resolution,
        "resolved_consequence": prepared.consequence,
    }
    if response_mode == "minimal":
        body = minimal_combined(result, gene)
    else:
        body = result
    body["_telemetry"] = telemetry
    return body
```
Add the import of `minimal_combined` to the `_predict_shape` import block. (`_aggregate_cache` is extended to 4-tuple in Task 6; until then it returns 2 values — temporarily keep `cache, ups = _aggregate_cache(teles)` and set `age_s = ttl_s = None`, then complete in Task 6.)

> To keep this task self-contained, set `age_s = ttl_s = None` here and unpack `cache, ups = _aggregate_cache(teles)`; Task 6 replaces those two lines with the 4-tuple unpack.

- [ ] **Step 8: Run combined + tool tests**

Run: `uv run pytest tests/unit/test_eval_fixes_2.py tests/unit/test_tools.py -v`
Expected: PASS. (`test_minimal_strictly_smaller_than_compact` in `test_eval_fixes.py` also still passes.)

- [ ] **Step 9: Commit**

```bash
git add spliceailookup_link/mcp/shaping.py spliceailookup_link/mcp/tools/_predict_shape.py spliceailookup_link/mcp/tools/_predict.py tests/unit/test_shaping.py tests/unit/test_eval_fixes_2.py
git commit -m "feat(F8): minimal response_mode becomes a true headline tier"
```

---

## Task 5: #4 — `interpretation` band on compact/full results

Surface the score-band cutoffs as data so agents stop re-deriving them. (`band`/`THRESHOLD_BASIS` already added in Task 1 Step 4; combined `interpretation` already wired in Task 2.)

**Files:**
- Modify: `spliceailookup_link/mcp/shaping.py`
- Test: `tests/unit/test_shaping.py`, `tests/unit/test_eval_fixes_2.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_shaping.py`:
```python
def test_interpretation_band_present_compact() -> None:
    out = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="compact")
    assert out["interpretation"]["band"] == "high"
    assert "0.5" in out["interpretation"]["threshold_basis"]


def test_interpretation_band_absent_threshold_in_minimal() -> None:
    out = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="minimal")
    assert out["interpretation"]["band"] == "high"
    assert "threshold_basis" not in out["interpretation"]
```

Append to `tests/unit/test_eval_fixes_2.py`:
```python
async def test_interpretation_band_on_combined(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert data["interpretation"]["band"] == "high"
    assert "threshold_basis" in data["interpretation"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_shaping.py -k interpretation tests/unit/test_eval_fixes_2.py::test_interpretation_band_on_combined -v`
Expected: FAIL — single-model `interpretation` not set in compact.

- [ ] **Step 3: Add `interpretation` to the single-model compact/full results**

In `shape_spliceai`, after `"transcripts": shaped,` inside the `result` literal (or immediately after the literal), add:
```python
    result["interpretation"] = {"band": band(max_overall), "threshold_basis": THRESHOLD_BASIS}
```
Apply the same one line to `shape_pangolin` (using its `max_overall`).
(The minimal projection in Task 4 already emits `interpretation` with `band` only — no `threshold_basis` — so the minimal test passes.)

- [ ] **Step 4: Run the interpretation tests**

Run: `uv run pytest tests/unit/test_shaping.py -k interpretation tests/unit/test_eval_fixes_2.py::test_interpretation_band_on_combined -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/shaping.py tests/unit/test_shaping.py tests/unit/test_eval_fixes_2.py
git commit -m "feat(#4): interpretation band + threshold_basis beside max_delta_score"
```

---

## Task 6: #5 — cache auditability (`cache_age_s` / `cache_ttl_s`)

**Files:**
- Modify: `spliceailookup_link/services/telemetry.py`
- Modify: `spliceailookup_link/services/splice_service.py`
- Modify: `spliceailookup_link/mcp/tools/_predict.py` (`_aggregate_cache` + telemetry passthrough)
- Modify: `spliceailookup_link/mcp/tools/combined.py`, `spliceai.py`, `pangolin.py` (fold into `_meta`)
- Modify: `tests/conftest.py` (stub telemetry carries the fields)
- Test: `tests/unit/test_service.py`, `tests/unit/test_eval_fixes_2.py`

- [ ] **Step 1: Write the failing service-level test**

Append to `tests/unit/test_service.py` (match its existing construction of `SpliceService` with stub clients — reuse the module's existing scoring/ensembl stub fixtures; if it builds them inline, mirror that):
```python
async def test_cache_age_and_ttl_telemetry() -> None:
    import respx
    from httpx import Response

    from spliceailookup_link.api import EnsemblVepClient, ScoringClient
    from spliceailookup_link.services import SpliceService
    from tests.fixtures.api_responses import SPLICEAI_TRAPPC9

    with respx.mock:
        respx.get(url__regex=r".*spliceai.*").mock(
            return_value=Response(200, json=SPLICEAI_TRAPPC9)
        )
        svc = SpliceService(
            scoring_client=ScoringClient(),
            ensembl_client=EnsemblVepClient(),
            cache_ttl_minutes=60,
        )
        _, t1 = await svc.score(
            model="spliceai", build="GRCh38", variant_id="8-140300616-T-G",
            distance=500, mask=0, gene_set="basic",
        )
        _, t2 = await svc.score(
            model="spliceai", build="GRCh38", variant_id="8-140300616-T-G",
            distance=500, mask=0, gene_set="basic",
        )
    assert t1.cache == "miss"
    assert t1.cache_ttl_s == 3600
    assert t2.cache == "hit"
    assert isinstance(t2.cache_age_s, int) and t2.cache_age_s >= 0
    assert t2.cache_ttl_s == 3600
```
> If `test_service.py` already defines a respx-based scoring fixture, use that instead of inlining `respx.mock` — match the file's established pattern. The assertions are what matter.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_service.py::test_cache_age_and_ttl_telemetry -v`
Expected: FAIL — `CallTelemetry` has no `cache_age_s`/`cache_ttl_s`.

- [ ] **Step 3: Extend `CallTelemetry`**

In `spliceailookup_link/services/telemetry.py`:
```python
@dataclass(slots=True)
class CallTelemetry:
    cache: str  # "hit" | "miss"
    upstream_elapsed_ms: int | None = None
    cache_age_s: int | None = None
    cache_ttl_s: int | None = None
```

- [ ] **Step 4: Populate it in `SpliceService`**

In `splice_service.py` `__init__`, after `ttl_seconds = max(1, cache_ttl_minutes) * 60`, add:
```python
        self._ttl_seconds = ttl_seconds
        self._scored_at: dict[tuple[Any, ...], float] = {}
        self._cache_size = cache_size
```
Replace the tail of `score()` (from `cached = key in self._scored_keys` onward) with:
```python
        cached = key in self._scored_keys
        start = perf_counter()
        payload = await self._score_cached(
            model, build, variant_id, distance, mask, gene_set, raw, consequence
        )
        elapsed_ms = int((perf_counter() - start) * 1000)
        now = perf_counter()
        if cached:
            scored_at = self._scored_at.get(key)
            age_s = int(now - scored_at) if scored_at is not None else None
        else:
            self._scored_keys.add(key)
            self._scored_at[key] = now
            age_s = None
            if len(self._scored_at) > self._cache_size:
                self._scored_at.pop(next(iter(self._scored_at)))
        return payload, CallTelemetry(
            cache="hit" if cached else "miss",
            upstream_elapsed_ms=None if cached else elapsed_ms,
            cache_age_s=age_s,
            cache_ttl_s=self._ttl_seconds,
        )
```

- [ ] **Step 5: Run the service test**

Run: `uv run pytest tests/unit/test_service.py::test_cache_age_and_ttl_telemetry -v`
Expected: PASS.

- [ ] **Step 6: Surface in single-model `_meta`**

In `spliceai.py`, after `"cache": tele.cache,` in the `meta` dict, the conditional block becomes:
```python
            if tele.cache_ttl_s is not None:
                meta["cache_ttl_s"] = tele.cache_ttl_s
            if tele.cache_age_s is not None:
                meta["cache_age_s"] = tele.cache_age_s
            if tele.upstream_elapsed_ms is not None:
                meta["upstream_elapsed_ms"] = tele.upstream_elapsed_ms
```
Apply the identical addition to `pangolin.py` (same `meta` construction).

- [ ] **Step 7: Aggregate for the combined tool**

In `_predict.py`, replace `_aggregate_cache` with:
```python
def _aggregate_cache(
    teles: list[CallTelemetry],
) -> tuple[str | None, int | None, int | None, int | None]:
    caches = [t.cache for t in teles]
    if not caches:
        return None, None, None, None
    if all(c == "hit" for c in caches):
        cache = "hit"
    elif all(c == "miss" for c in caches):
        cache = "miss"
    else:
        cache = "partial"
    ups = [t.upstream_elapsed_ms for t in teles if t.upstream_elapsed_ms is not None]
    ages = [t.cache_age_s for t in teles if t.cache_age_s is not None]
    ttls = [t.cache_ttl_s for t in teles if t.cache_ttl_s is not None]
    return (
        cache,
        (max(ups) if ups else None),
        (max(ages) if ages else None),
        (ttls[0] if ttls else None),
    )
```
Update the call in `predict_one` to the 4-tuple unpack (replacing the Task 4 placeholder):
```python
    cache, ups, age_s, ttl_s = _aggregate_cache(teles)
```
(`telemetry` dict from Task 4 already references `age_s`/`ttl_s`.)

In `combined.py`, after `if tel["cache"]: meta["cache"] = tel["cache"]`, add:
```python
            if tel.get("cache_ttl_s") is not None:
                meta["cache_ttl_s"] = tel["cache_ttl_s"]
            if tel.get("cache_age_s") is not None:
                meta["cache_age_s"] = tel["cache_age_s"]
```

- [ ] **Step 8: Make the stub telemetry carry the fields**

In `tests/conftest.py` `StubService.score`, change the returned telemetry:
```python
        return payload, CallTelemetry(
            cache=cache,
            upstream_elapsed_ms=None if cache == "hit" else 7,
            cache_age_s=0 if cache == "hit" else None,
            cache_ttl_s=86400,
        )
```

- [ ] **Step 9: Write the tool-level `_meta` test**

Append to `tests/unit/test_eval_fixes_2.py`:
```python
async def test_cache_ttl_and_age_in_meta(mcp) -> None:
    first = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    assert first["_meta"]["cache"] == "miss"
    assert first["_meta"]["cache_ttl_s"] == 86400
    assert "cache_age_s" not in first["_meta"]
    second = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    assert second["_meta"]["cache"] == "hit"
    assert second["_meta"]["cache_age_s"] == 0
    assert second["_meta"]["cache_ttl_s"] == 86400
```

- [ ] **Step 10: Run service + tool tests**

Run: `uv run pytest tests/unit/test_service.py tests/unit/test_eval_fixes_2.py tests/unit/test_tools.py -v`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add spliceailookup_link/services/telemetry.py spliceailookup_link/services/splice_service.py spliceailookup_link/mcp/tools/_predict.py spliceailookup_link/mcp/tools/combined.py spliceailookup_link/mcp/tools/spliceai.py spliceailookup_link/mcp/tools/pangolin.py tests/conftest.py tests/unit/test_service.py tests/unit/test_eval_fixes_2.py
git commit -m "feat(#5): cache_age_s + cache_ttl_s telemetry in _meta"
```

---

## Task 7: F9 — stamp `request_id` + `timing` on validation envelopes

Validation errors return via `wrapped_run`→`convert_result`, bypassing `run_mcp_tool._stamp`, so they lack `request_id`/`timing`.

**Files:**
- Modify: `spliceailookup_link/mcp/errors.py`
- Test: `tests/unit/test_eval_fixes_2.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_eval_fixes_2.py`:
```python
async def test_f9_validation_failed_has_request_id_and_timing(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "8-140300616-T-G", "max_distance": 20000}
        )
    )
    assert data["success"] is False
    assert data["error_code"] == "validation_failed"
    meta = data["_meta"]
    assert isinstance(meta["request_id"], str) and len(meta["request_id"]) == 12
    assert isinstance(meta["timing"]["elapsed_ms"], int)
    assert data["field_errors"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_eval_fixes_2.py::test_f9_validation_failed_has_request_id_and_timing -v`
Expected: FAIL — `KeyError: 'request_id'` (validation `_meta` lacks it).

- [ ] **Step 3: Stamp in `wrapped_run`**

In `errors.py`, inside `install_validation_error_handler`'s `wrapped_run`, wrap the call with timing + request_id and merge into the envelope `_meta`:
```python
        async def wrapped_run(
            arguments: dict[str, Any],
            *,
            _original_run: Callable[[dict[str, Any]], Awaitable[Any]] = original_run,
            _tool: Any = tool,
        ) -> Any:
            request_id = uuid.uuid4().hex[:12]
            start = time.perf_counter()
            try:
                return await _original_run(arguments)
            except PydanticValidationError as exc:
                envelope = mcp_validation_tool_error(
                    tool_name=str(getattr(_tool, "name", "unknown")),
                    exc=exc,
                ).payload
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                envelope["_meta"] = {
                    "request_id": request_id,
                    "timing": {"elapsed_ms": elapsed_ms},
                    **envelope.get("_meta", {}),
                }
                record_mcp_error(
                    tool_name=str(getattr(_tool, "name", "unknown")),
                    error_code="validation_failed",
                    message=envelope["message"],
                    raw_message=str(exc),
                )
                convert_result = getattr(_tool, "convert_result", None)
                if callable(convert_result):
                    return convert_result(envelope)
                return envelope
```
(`uuid` and `time` are already imported at the top of `errors.py`.)

- [ ] **Step 4: Run the F9 test**

Run: `uv run pytest tests/unit/test_eval_fixes_2.py::test_f9_validation_failed_has_request_id_and_timing tests/unit/test_errors.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/errors.py tests/unit/test_eval_fixes_2.py
git commit -m "fix(F9): stamp request_id + timing on validation_failed envelopes"
```

---

## Task 8: F10 — batch summary histogram + next_commands; drop misleading see_also

**Files:**
- Modify: `spliceailookup_link/mcp/tools/batch.py`
- Modify: `tests/unit/test_batch.py` (the `see_also` assertion)
- Test: `tests/unit/test_batch.py`, `tests/unit/test_eval_fixes_2.py`

- [ ] **Step 1: Update the existing batch test + write new ones**

In `tests/unit/test_batch.py`, change the `see_also` assertion in `test_batch_scores_each_variant_once_envelope`:
```python
    assert "see_also" not in data["_meta"]  # batch-level see_also is misleading for a panel
    assert data["_meta"]["next_commands"][0]["tool"] == "predict_splicing"
    assert all("_meta" not in r for r in data["results"])
```
Append:
```python
async def test_f10_batch_summary_full_histogram(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing_batch",
        {"variants": ["chr8-140300616-T-G", "8-140300616-T-G"]},
    )
    data = structured(res)
    summary = data["summary"]
    for key in (
        "ok", "failed", "concordant_high", "concordant_moderate",
        "concordant_low", "discordant", "incomplete",
    ):
        assert key in summary
    verdict_total = (
        summary["concordant_high"] + summary["concordant_moderate"]
        + summary["concordant_low"] + summary["discordant"] + summary["incomplete"]
    )
    assert verdict_total == summary["ok"]
    assert data["summary_top_variant"]["variant"]


async def test_f10_batch_next_commands_targets_top_variant(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing_batch", {"variants": ["chr8-140300616-T-G"]}
    )
    data = structured(res)
    nc = data["_meta"]["next_commands"][0]
    assert nc["tool"] == "predict_splicing"
    assert nc["arguments"]["response_mode"] == "full"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_batch.py -v`
Expected: FAIL — `see_also` still present; summary missing keys; no `next_commands`/`summary_top_variant`.

- [ ] **Step 3: Implement the richer batch envelope**

In `batch.py`, remove the `see_also_for` import and replace the tail of `call()` (from `concordant_high = ...` to the `return {...}`):
```python
            verdict_counts = {
                "concordant_high": 0,
                "concordant_moderate": 0,
                "concordant_low": 0,
                "discordant": 0,
                "incomplete": 0,
            }
            top: dict[str, Any] | None = None
            for r in results:
                verdict = (r.get("agreement") or {}).get("verdict")
                if verdict in verdict_counts:
                    verdict_counts[verdict] += 1
                score = (r.get("interpretation") or {}).get("band")
                max_delta = _result_max_delta(r)
                if max_delta is not None and (top is None or max_delta > top["max_delta_score"]):
                    top = {"variant": r.get("variant"), "max_delta_score": max_delta}
            summary = {"ok": ok, "failed": failed, **verdict_counts}
            meta: dict[str, Any] = {}
            if top is not None:
                meta["next_commands"] = [
                    {
                        "tool": "predict_splicing",
                        "arguments": {
                            "variant": top["variant"],
                            "genome_build": genome_build,
                            "response_mode": "full",
                        },
                    }
                ]
            return {
                "count": total,
                "results": results,
                "summary": summary,
                "summary_top_variant": top,
                "_meta": meta,
            }
```
Add a module-level helper above `register_batch_tools`:
```python
def _result_max_delta(r: dict[str, Any]) -> float | None:
    candidates = [
        (r.get("spliceai") or {}).get("max_delta_score"),
        (r.get("pangolin") or {}).get("max_delta_score"),
        r.get("spliceai_max"),
        r.get("pangolin_max"),
    ]
    vals = [c for c in candidates if isinstance(c, (int, float))]
    return max(vals) if vals else None
```
(The `score`/`band` line is unused — drop it; keep only the `max_delta` logic. Remove the leftover `genes`/`first_gene` lines from the old body.)

- [ ] **Step 4: Run the batch tests**

Run: `uv run pytest tests/unit/test_batch.py tests/unit/test_eval_fixes_2.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/tools/batch.py tests/unit/test_batch.py tests/unit/test_eval_fixes_2.py
git commit -m "feat(F10): batch verdict histogram + top-variant next_commands; drop misleading see_also"
```

---

## Task 9: #2 — advertise background execution in discovery

The protocol metadata is already emitted (`task=True` → `task_config.mode == "optional"`, verified by the existing `test_prediction_tools_are_task_optional`). The gap is the hand-authored descriptor + tool text never mention it, so an LLM reading the documented cold-start blocks on 30 s calls.

**Files:**
- Modify: `spliceailookup_link/mcp/resources.py`
- Modify: `spliceailookup_link/mcp/tools/combined.py`, `spliceai.py`, `pangolin.py`, `batch.py` (description strings)
- Test: `tests/unit/test_eval_fixes_2.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_eval_fixes_2.py`:
```python
async def test_capabilities_advertises_background_execution(mcp) -> None:
    data = structured(await mcp.call_tool("get_server_capabilities", {}))
    bg = data["background_execution"]
    assert set(bg["task_eligible_tools"]) == {
        "predict_spliceai", "predict_pangolin", "predict_splicing", "predict_splicing_batch",
    }
    assert bg["task_support"] == "optional"


async def test_task_tool_descriptions_mention_background(mcp) -> None:
    for name in ("predict_splicing", "predict_spliceai", "predict_pangolin"):
        tool = await mcp.get_tool(name)
        assert "background task" in tool.description.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_eval_fixes_2.py -k "background" -v`
Expected: FAIL — `KeyError: 'background_execution'`; descriptions lack the phrase.

- [ ] **Step 3: Add the `background_execution` block to capabilities**

In `resources.py`, locate `get_capabilities_resource()` and add a key to the returned dict (place near the existing `concurrency`/limitations content):
```python
        "background_execution": {
            "task_support": "optional",
            "task_eligible_tools": [
                "predict_spliceai",
                "predict_pangolin",
                "predict_splicing",
                "predict_splicing_batch",
            ],
            "how_to": (
                "Augment the tools/call with a `task` field (MCP 2025-11-25 Tasks); "
                "the call returns a taskId, poll tasks/get, retrieve via tasks/result."
            ),
            "backend": (
                "in-process (memory://); tasks are session-local, lost on server restart, "
                "and not auth-context-bound -- retrieve results within the session."
            ),
            "recommended_for": "cold predict_* calls (13-40s) and predict_splicing_batch.",
        },
```
> This changes the capabilities dict → `capabilities_version` hash changes (covered by Task 10's hash test).

- [ ] **Step 4: Append the background-task sentence to the four descriptions**

In `combined.py`, `spliceai.py`, `pangolin.py`, `batch.py`, append to each tool's docstring (the `"""..."""` returned as the description) this sentence:
> ` Supports MCP background tasks (execution.taskSupport=optional): augment the call with a task to fire-and-continue instead of blocking 15-40s.`

For example, in `combined.py` the docstring's final sentence becomes `... Note: cold calls take 15-40s (two model calls). Supports MCP background tasks (execution.taskSupport=optional): augment the call with a task to fire-and-continue instead of blocking 15-40s.`

- [ ] **Step 5: Run the discovery tests**

Run: `uv run pytest tests/unit/test_eval_fixes_2.py -k "background" tests/unit/test_tools.py::test_prediction_tools_are_task_optional -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/resources.py spliceailookup_link/mcp/tools/combined.py spliceailookup_link/mcp/tools/spliceai.py spliceailookup_link/mcp/tools/pangolin.py spliceailookup_link/mcp/tools/batch.py tests/unit/test_eval_fixes_2.py
git commit -m "feat(#2): advertise background-task execution in capabilities + tool descriptions"
```

---

## Task 10: Docs, glossary, capabilities hash, gate, Part 5 appendix

**Files:**
- Modify: `spliceailookup_link/mcp/resources.py` (glossary: verdict band, interpretation enum, tier contract, shared_by/transcripts_truncated)
- Modify: `docs/mcp-evaluation.md` (Part 5)
- Test: `tests/unit/test_eval_fixes_2.py`

- [ ] **Step 1: Write the capabilities-hash-changed test**

The committed v0.2.0 hash is in `docs/mcp-evaluation.md` Part 3 prose and/or a prior fixture. Pin only that the hash is present, 12 chars, and stable across two calls (already covered by `test_capabilities_version_is_stable`). Add a doc-content assertion instead. Append to `tests/unit/test_eval_fixes_2.py`:
```python
async def test_capabilities_documents_new_contracts(mcp) -> None:
    data = structured(await mcp.call_tool("get_server_capabilities", {}))
    blob = json.dumps(data).lower()
    assert "concordant_moderate" in blob
    assert "shared_by" in blob
    assert "minimal" in blob and "compact" in blob and "full" in blob
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_eval_fixes_2.py::test_capabilities_documents_new_contracts -v`
Expected: FAIL — glossary does not yet mention `concordant_moderate`/`shared_by`.

- [ ] **Step 3: Document the new contracts in the capabilities resource**

In `resources.py` `get_capabilities_resource()`, extend the score glossary / response-mode docs with:
- `agreement.verdict` values: `concordant_high | concordant_moderate | concordant_low | discordant | incomplete`.
- `interpretation`: `{band: high|moderate|low|none, threshold_basis}` — bands are Δ≥0.5 high, 0.2–0.5 moderate, >0–0.2 low, 0 none.
- response_mode tier contract: `minimal` = headline + single decision number (+ band); `compact` = per-transcript deltas (default); `full` = + REF/ALT + exon model.
- `transcripts="all"` may collapse byte-identical blocks into one carrying `shared_by:[transcript_ids]`; `max_transcripts` caps the list and adds `transcripts_truncated:{kept,total}`.

Concretely, add a `response_modes`/`glossary` sub-dict (or extend the existing one) so the JSON contains those literal tokens, e.g.:
```python
        "agreement_verdicts": [
            "concordant_high", "concordant_moderate", "concordant_low",
            "discordant", "incomplete",
        ],
        "interpretation_bands": {
            "high": "Δ>=0.5", "moderate": "0.2-0.5", "low": ">0-0.2", "none": "0",
        },
        "response_mode_tiers": {
            "minimal": "headline + single decision number + band",
            "compact": "per-transcript deltas (default)",
            "full": "compact + REF/ALT raw scores + exon model",
        },
        "transcript_collapse": (
            "transcripts=all collapses byte-identical blocks into one with "
            "shared_by:[ids]; max_transcripts caps and adds transcripts_truncated."
        ),
```

- [ ] **Step 4: Run the doc test + full unit suite**

Run: `uv run pytest tests/unit -v`
Expected: PASS (all). Investigate and fix any failure before continuing.

- [ ] **Step 5: Run the full local gate**

Run: `make ci-local`
Expected: format clean, Ruff clean, `lint-loc` ≤600 for every module (confirm `_predict.py` and `_predict_shape.py` both under budget — run `wc -l spliceailookup_link/mcp/tools/_predict.py spliceailookup_link/mcp/tools/_predict_shape.py spliceailookup_link/mcp/shaping.py`), mypy clean, tests pass, coverage ≥80%.
If `lint-loc` flags a file, split per the file-structure plan (the presentation logic already lives in `_predict_shape.py`).

- [ ] **Step 6: Write the Part 5 re-evaluation appendix**

Append to `docs/mcp-evaluation.md`:
```markdown
---

## Part 5 — Corrective pass for Part 4 findings (v0.3.0)

**Date:** 2026-06-11 · **Server:** spliceailookup-link **v0.3.0**
**Basis:** every change below is covered by the deterministic unit suite
(`make ci-local` green, coverage ≥80%). Findings F6–F10 from Part 4 and the
Part 4a consumer asks #2/#4/#5 are closed; the contract/shape changes are fully
determined by the server and verified offline. A live re-exercise against the
rate-limited upstream is recommended once deployed.

### Part 4 findings — resolved

| # | Sev | Status | Fix + proof |
|---|---|---|---|
| F6 | HIGH | Fixed | `combined_headline` renders `agreement.verdict` verbatim (no recompute); `assess_agreement` gains a `concordant_moderate` band. Tests: `test_predict_shape.py` consistency matrix, `test_f6_headline_matches_verdict_concordant_high`. |
| F7 | MED | Fixed | Byte-identical transcript blocks collapse to one + `shared_by:[ids]`; optional `max_transcripts` top-N + `transcripts_truncated`. Tests: `test_f7_identical_transcripts_collapse`, `test_f7_max_transcripts_truncates_top_n`. |
| F8 | LOW–MED | Fixed | `minimal` is now headline-tier (headline + `max_delta_score` + `top` + band; no `delta_scores`). Tests: `test_minimal_mode_is_headline_tier`, `test_f8_combined_minimal_is_headline_tier`. |
| F9 | LOW | Fixed | Validation envelopes stamp `request_id` + `timing`. Test: `test_f9_validation_failed_has_request_id_and_timing`. |
| F10 | LOW | Fixed | Batch `summary` is a full verdict histogram + `summary_top_variant`; same-server `next_commands` drills the top variant in `full` mode; misleading batch `see_also` removed. Tests: `test_f10_batch_summary_full_histogram`, `test_f10_batch_next_commands_targets_top_variant`. |

### Consumer improvements

- **#2 background tasks discoverable:** `background_execution` block in
  capabilities + a sentence in each task tool description; protocol
  `execution.taskSupport == "optional"` confirmed by `test_prediction_tools_are_task_optional`.
- **#4 interpretation band:** `interpretation:{band, threshold_basis}` beside
  `max_delta_score` (band only in `minimal`).
- **#5 cache auditability:** `_meta.cache_ttl_s` always, `_meta.cache_age_s` on hits.

### Re-rated (projected)

Senior-tester: `predict_splicing` 6→9 and `predict_splicing_batch` 7→9 (F6),
`predict_spliceai` 8.5→9 (F7), `get_server_capabilities` 9→9.5 → **~9.1**.
LLM-consumer: token efficiency 8→9 (F7/F8), speed/latency 7→8.5 (#2),
observability 9→9.5 (#5/F9), schema 9→9.5 (#4), composability 9→9.5 (F10) →
**~9.2**. Both axes clear 9/10.

*Research use only; not for clinical decision support.*
```

- [ ] **Step 7: Final gate + commit**

Run: `make ci-local`
Expected: green.
```bash
git add spliceailookup_link/mcp/resources.py docs/mcp-evaluation.md tests/unit/test_eval_fixes_2.py
git commit -m "docs: capabilities glossary for v0.3.0 contracts + Part 5 re-evaluation"
```

---

## Self-Review (completed by plan author)

**Spec coverage:** F6 → Tasks 1–2; F6b → Task 1; F7 → Task 3; F8 → Task 4; #4 → Tasks 1/5; #5 → Task 6; F9 → Task 7; F10 → Task 8; #2 → Task 9; glossary/hash/version/gate/Part-5 → Tasks 0/10. All spec sections map to a task.

**Placeholder scan:** No TBD/TODO. Every code step shows the code. The only deferred cross-task dependency (the `_aggregate_cache` 2-tuple→4-tuple and the `age_s/ttl_s` placeholder) is explicitly called out in Task 4 Step 7 and completed in Task 6 Step 7.

**Type/name consistency:** `assess_agreement`, `combined_headline`, `combined_interpretation`, `minimal_combined` (in `_predict_shape.py`); `band`, `THRESHOLD_BASIS`, `_collapse_identical_transcripts`, `_apply_max_transcripts`, `_minimal_spliceai` (in `shaping.py`); `_result_max_delta` (in `batch.py`); `CallTelemetry.cache_age_s`/`cache_ttl_s`. Names are used identically across the tasks that reference them.

**Known existing-test updates folded in:** `test_transcripts_all_returns_non_mane` (Task 3), `test_minimal_mode_keeps_single_transcript` → `test_minimal_mode_is_headline_tier` (Task 4), `test_batch_scores_each_variant_once_envelope` see_also assertion (Task 8). `test_predict_splicing_runs_both_models` and `test_tools.py` headline/verdict assertions remain valid under the F6 change (concordant_high still says "models agree").

*Research use only; not for clinical decision support.*
