# spliceailookup-link v0.5.0 Evaluation Pass 3 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close `docs/mcp-evaluation.md` Part 7 findings F11–F17 + #C1 and add four structural parity invariants so a fresh independent re-test (Part 8) clears **>9.2 on both axes** with `predict_splicing_batch` ≥ 9.

**Architecture:** Surgical, additive fixes to the existing MCP facade — no new modules, no tool renames, no schema-breaking removals. The batch tool (`mcp/tools/batch.py`) gets first-class per-item errors (F11) and per-item observability (F12); `predict_one` (`mcp/tools/_predict.py`) de-duplicates `threshold_basis` (F13); `_shape_consequence` (`mcp/shaping.py`) omits null aberration sub-fields (F14) and adds a masked-suppression caveat (F15); `errors.py` stamps a concurrency budget on `rate_limited` (#C1, **concurrency-unit not time-window**); descriptions/capabilities disambiguate the tools (F16/F17). A new `tests/unit/test_eval_fixes_3.py` holds the regressions plus four durability invariants that forbid the *class* of "second-class path" divergence future independent testers keep finding.

**Tech Stack:** Python 3.12, FastMCP 3.x, pydantic, respx + pytest (`asyncio_mode=auto`, no decorators), `uv`, Ruff, mypy. Required gate: `make ci-local` (includes `lint-loc`, the 600-LOC/file budget).

**Spec:** `docs/superpowers/specs/2026-06-12-eval-improvements-3-design.md` (reviewed & corrected 2026-06-12 — read §4 #C1/F13/F14/F15 corrections and §8 durability before starting).

**Conventions (already in the repo — match them):**
- Pure shaping functions are unit-tested directly with dict payloads (`tests/unit/test_shaping.py`).
- Tool behaviour is tested through `await mcp.call_tool(name, args)` + `structured(res)` using the `mcp` / `stub_service` fixtures (`tests/conftest.py`).
- Tests are `async def test_...` with **no** `@pytest.mark.asyncio` decorator (asyncio_mode=auto).
- Run one test: `uv run pytest tests/unit/test_x.py::test_y -v`. Unit suite: `make test`. Full gate: `make ci-local`.
- Commit per task. Branch first (the repo is currently not a git repo per the environment note — if `git status` fails, run `git init` first, see Task 0).

---

## File Structure

New:
- `tests/unit/test_eval_fixes_3.py` — regression tests for F11–F17/#C1 **and** the four §8 durability invariants. Mirrors the `test_eval_fixes.py` / `test_eval_fixes_2.py` convention.

Modified:
- `spliceailookup_link/mcp/tools/batch.py` — F11 full per-item error scaffold (built with a `predict_splicing` context for parity); F12 slim per-item `_meta`.
- `spliceailookup_link/mcp/tools/_predict.py` — F13 pop `threshold_basis` from the two sub-blocks (combined path only).
- `spliceailookup_link/mcp/shaping.py` — F14 omit-when-null aberration sub-fields; F15 masked-suppression caveat in `_shape_consequence`.
- `spliceailookup_link/mcp/errors.py` — #C1 `rate_budget` (concurrency quota) on the `rate_limited` envelope only.
- `spliceailookup_link/mcp/tools/resolve.py` — F16 "normalized, not validated" caveat in the description docstring.
- `spliceailookup_link/mcp/tools/spliceai.py` / `pangolin.py` / `combined.py` — F17 lead with ONE / BOTH.
- `spliceailookup_link/mcp/resources.py` — F16 caveat, F17 "which tool?", #C1 field semantics, F14/F15 glossary notes; `capabilities_version` re-derives automatically from the doc change.
- `spliceailookup_link/__init__.py`, `pyproject.toml` — version → `0.5.0`.
- `tests/fixtures/api_responses.py` — add `SPLICEAI_MASKED_NO_EFFECT` (mask=1, all-zero deltas, empty aberrations) for the F15 no-over-fire test.
- `tests/unit/test_batch.py` — flip the now-wrong `all("_meta" not in r ...)` assertion (F12).
- `docs/mcp-evaluation.md` — Part 8 re-evaluation appendix (offline-verified).

---

## Task 0: Branch + version bump to 0.5.0

**Files:**
- Modify: `spliceailookup_link/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Ensure a git repo + working branch exist**

Run:
```bash
cd /home/bernt-popp/development/spliceailookup-link
git rev-parse --is-inside-work-tree 2>/dev/null || git init
git checkout -b eval-improvements-3 2>/dev/null || git checkout eval-improvements-3
```
Expected: on branch `eval-improvements-3`.

- [ ] **Step 2: Find the current version strings**

Run:
```bash
grep -rn "0.4.0" spliceailookup_link/__init__.py pyproject.toml
```
Expected: a `__version__ = "0.4.0"` line and a `version = "0.4.0"` line.

- [ ] **Step 3: Bump both to 0.5.0**

Edit `spliceailookup_link/__init__.py`: change `__version__ = "0.4.0"` → `__version__ = "0.5.0"`.
Edit `pyproject.toml`: change `version = "0.4.0"` → `version = "0.5.0"`.

- [ ] **Step 4: Verify import works**

Run: `uv run python -c "import spliceailookup_link; print(spliceailookup_link.__version__)"`
Expected: `0.5.0`

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/__init__.py pyproject.toml
git commit -m "chore: bump version to 0.5.0 (eval pass 3)"
```

---

## Task 1: F13 — de-duplicate `threshold_basis` (combined path only)

The static `THRESHOLD_BASIS` string appears 3× in a `predict_splicing` payload: in each model sub-block's `interpretation` (from `shape_spliceai`/`shape_pangolin`) and once at the top level (from `combined_interpretation`). Standalone single-model tools correctly carry exactly one copy — **do not touch `shaping.py` for this**. Strip the two sub-block copies inside `predict_one`, keeping each sub-block's `band`.

**Files:**
- Test: `tests/unit/test_eval_fixes_3.py`
- Modify: `spliceailookup_link/mcp/tools/_predict.py:184-186` (right after the pangolin sub-block is assigned, before `_lift_identity`)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_eval_fixes_3.py` with:
```python
"""Regression tests for docs/mcp-evaluation.md Part 7 (F11-F17 + #C1) and the
§8 durability invariants."""

from __future__ import annotations

import json

from spliceailookup_link.api import DataNotFoundError, RateLimitedError
from spliceailookup_link.mcp.shaping import THRESHOLD_BASIS
from tests.conftest import StubService, structured


async def test_f13_threshold_basis_emitted_once_in_combined(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    # Exactly one threshold_basis in the whole combined payload (top-level only).
    assert json.dumps(data).count("threshold_basis") == 1
    assert data["interpretation"]["threshold_basis"] == THRESHOLD_BASIS
    # Each model sub-block keeps its decision-relevant band but drops the static string.
    assert "band" in data["spliceai"]["interpretation"]
    assert "threshold_basis" not in data["spliceai"]["interpretation"]
    assert "band" in data["pangolin"]["interpretation"]
    assert "threshold_basis" not in data["pangolin"]["interpretation"]


async def test_f13_single_model_still_has_one_threshold_basis(mcp) -> None:
    # Standalone single-model tools are self-contained: they keep their one copy.
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    assert data["interpretation"]["threshold_basis"] == THRESHOLD_BASIS
    assert json.dumps(data).count("threshold_basis") == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -v`
Expected: `test_f13_threshold_basis_emitted_once_in_combined` FAILS (count == 3, not 1); `test_f13_single_model_still_has_one_threshold_basis` PASSES already (single-model is correct today — it guards against over-fixing).

- [ ] **Step 3: Strip the sub-block copies in `predict_one`**

In `spliceailookup_link/mcp/tools/_predict.py`, immediately after the pangolin block is assigned (`result["pangolin"] = shaped_pang`) and before the `identity = _lift_identity(...)` line, insert:
```python
    # F13: threshold_basis is a static glossary string; emit it once (top-level
    # combined_interpretation). Keep each sub-block's decision-relevant band.
    for _sub in ("spliceai", "pangolin"):
        _interp = (result.get(_sub) or {}).get("interpretation")
        if _interp:
            _interp.pop("threshold_basis", None)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -v`
Expected: both PASS.

- [ ] **Step 5: Verify no existing test regressed**

Run: `uv run pytest tests/unit/test_predict_shape.py tests/unit/test_eval_fixes_2.py -v`
Expected: all PASS (F6 verdict/headline tests do not read `threshold_basis`; `test_interpretation_band_on_combined` asserts top-level `threshold_basis` still present — unchanged).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_eval_fixes_3.py spliceailookup_link/mcp/tools/_predict.py
git commit -m "fix(F13): emit threshold_basis once per combined payload"
```

---

## Task 2: F14 — omit null aberration sub-fields (no null leaves in full mode)

The aberration mapping in `_shape_consequence` already reads `status` / `size_is_coding` / `introduces_stop_codon` (they populate in the `SPLICEAI_TRAPPC9` fixture). The Part 7 live observation was a *sparse* upstream response where they were `null`. Fix: keep the fields when present, drop the key when the upstream value is `null`.

**Files:**
- Test: `tests/unit/test_eval_fixes_3.py`
- Modify: `spliceailookup_link/mcp/shaping.py:215-226` (`_shape_consequence`, the `out["aberrations"]` comprehension)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_eval_fixes_3.py`:
```python
from spliceailookup_link.mcp.shaping import shape_spliceai
from tests.fixtures.api_responses import SPLICEAI_TRAPPC9


def test_f14_populated_aberration_fields_are_kept() -> None:
    shaped = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="full")
    ab = shaped["consequence"]["aberrations"][0]
    # The fixture populates these -> they must survive.
    assert ab["type"] == "exon_skipping"
    assert ab["status"] == "frameshift"
    assert ab["size_is_coding"] is True
    assert ab["introduces_stop_codon"] is True


def test_f14_null_aberration_fields_are_omitted_not_null() -> None:
    sparse = {
        **SPLICEAI_TRAPPC9,
        "sai10kPredictions": {
            "aberrations": [
                {
                    "aberration_type": "exon_skipping",
                    "affected_region": {"region_type": "intron"},
                    "status": None,
                    "size_is_coding": None,
                    "introduces_stop_codon": None,
                }
            ]
        },
    }
    shaped = shape_spliceai(sparse, response_mode="full")
    ab = shaped["consequence"]["aberrations"][0]
    assert ab["type"] == "exon_skipping"
    assert "status" not in ab  # omitted, not null
    assert "size_is_coding" not in ab
    assert "introduces_stop_codon" not in ab
```

- [ ] **Step 2: Run to verify the null test fails**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py::test_f14_null_aberration_fields_are_omitted_not_null tests/unit/test_eval_fixes_3.py::test_f14_populated_aberration_fields_are_kept -v`
Expected: `test_f14_null_..._omitted` FAILS (keys present as `None`); `test_f14_populated_...` PASSES already.

- [ ] **Step 3: Make the aberration dict omit-when-null**

In `spliceailookup_link/mcp/shaping.py`, replace the `out["aberrations"] = [...]` comprehension in `_shape_consequence` with:
```python
    out["aberrations"] = [
        {
            k: v
            for k, v in {
                "type": ab.get("aberration_type"),
                "affected_region": ab.get("affected_region"),
                "status": ab.get("status"),
                "size_is_coding": ab.get("size_is_coding"),
                "introduces_stop_codon": ab.get("introduces_stop_codon"),
            }.items()
            if v is not None
        }
        for ab in (raw_aberr or [])
    ]
```

- [ ] **Step 4: Run to verify both pass**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -k f14 -v`
Expected: both PASS.

- [ ] **Step 5: Verify shaping/eval suites still green**

Run: `uv run pytest tests/unit/test_shaping.py tests/unit/test_eval_fixes.py -v`
Expected: all PASS (the `SPLICEAI_TRAPPC9` aberration still carries all keys; the empty-aberration fixtures are unaffected).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_eval_fixes_3.py spliceailookup_link/mcp/shaping.py
git commit -m "fix(F14): omit null SAI-10k aberration sub-fields instead of shipping null"
```

---

## Task 3: F15 — masked-suppression caveat (non-asserting, score-gated)

When `mask='masked'` yields an empty aberration list but there is a real splice signal (max delta ≥ 0.2), add a caveat note explaining masking can suppress an aberration raw mode would show. It must NOT fire on raw mode, and must NOT fire on genuinely no-effect variants.

**Files:**
- Modify: `tests/fixtures/api_responses.py` (add `SPLICEAI_MASKED_NO_EFFECT`)
- Test: `tests/unit/test_eval_fixes_3.py`
- Modify: `spliceailookup_link/mcp/shaping.py` (`_shape_consequence` — needs the masked flag and the overall max score; both are derivable in `shape_spliceai`, so pass them in)

- [ ] **Step 1: Add the no-effect fixture**

In `tests/fixtures/api_responses.py`, after `SPLICEAI_MASKED_EMPTY_ABERR`, add:
```python
# Masked payload with NO splice signal (all deltas 0) and empty aberrations -- the
# F15 caveat must NOT fire here (this is a genuinely no-effect variant).
SPLICEAI_MASKED_NO_EFFECT: dict[str, Any] = {
    "variant": "8-140300616-T-G",
    "hg": "38",
    "bc": "basic",
    "distance": 500,
    "mask": 1,
    "scores": [
        {
            **SPLICEAI_TRAPPC9["scores"][0],
            "DS_AG": 0.0,
            "DS_AL": 0.0,
            "DS_DG": 0.0,
            "DS_DL": 0.0,
        }
    ],
    "sai10kPredictions": {"aberrations": []},
}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_eval_fixes_3.py`:
```python
from tests.fixtures.api_responses import (
    SPLICEAI_MASKED_EMPTY_ABERR,
    SPLICEAI_MASKED_NO_EFFECT,
)


def test_f15_masked_suppression_note_fires_on_real_signal() -> None:
    shaped = shape_spliceai(SPLICEAI_MASKED_EMPTY_ABERR, response_mode="full")
    cons = shaped["consequence"]
    assert cons["aberrations"] == []
    assert "note" in cons
    assert "mask='raw'" in cons["note"]


def test_f15_no_note_on_no_effect_masked_variant() -> None:
    shaped = shape_spliceai(SPLICEAI_MASKED_NO_EFFECT, response_mode="full")
    cons = shaped.get("consequence")
    # Either no consequence object, or one without a note -- never a misleading note.
    assert not (cons and cons.get("note"))


def test_f15_no_note_in_raw_mode() -> None:
    raw = {**SPLICEAI_MASKED_EMPTY_ABERR, "mask": 0}
    shaped = shape_spliceai(raw, response_mode="full")
    cons = shaped.get("consequence") or {}
    assert "note" not in cons
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -k f15 -v`
Expected: `test_f15_masked_suppression_note_fires_on_real_signal` FAILS (no `note`); the other two PASS (no note today).

- [ ] **Step 4: Thread the masked flag + max score into `_shape_consequence` and emit the caveat**

In `spliceailookup_link/mcp/shaping.py`:

(a) Change the `_shape_consequence` signature and add the caveat. Replace the function header line:
```python
def _shape_consequence(payload: dict[str, Any], mode: ResponseMode) -> dict[str, Any] | None:
```
with:
```python
def _shape_consequence(
    payload: dict[str, Any], mode: ResponseMode, max_score: float | None = None
) -> dict[str, Any] | None:
```

(b) At the end of `_shape_consequence`, just before `return out`, insert:
```python
    masked = str(payload.get("mask")) in ("1", "True", "true")
    if masked and not out["aberrations"] and (max_score or 0.0) >= _MODERATE:
        out["note"] = (
            "mask='masked' computes aberrations on masked scores and can suppress an "
            "aberration that mask='raw' would predict; this site has a non-trivial "
            "delta (>=0.2) but no masked aberration -- re-run with mask='raw' to check."
        )
```

(c) In `shape_spliceai`, update the call site (currently `consequence = _shape_consequence(payload, response_mode)`) to pass the overall max:
```python
        consequence = _shape_consequence(payload, response_mode, max_overall)
```

- [ ] **Step 5: Run to verify all three pass**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -k f15 -v`
Expected: all three PASS.

- [ ] **Step 6: Verify the combined path carries the note too**

Append and run:
```python
async def test_f15_note_present_in_combined_masked(mcp, stub_service: StubService) -> None:
    # Force the stub to return the masked-empty payload for spliceai.
    import tests.fixtures.api_responses as fx

    orig = fx.SPLICEAI_TRAPPC9
    # predict_one lifts consequence from the spliceai sub-block; the masked note
    # rides along on consequence. (Stub returns SPLICEAI_TRAPPC9 by default, which
    # has an aberration, so assert via the shaping unit tests above; this combined
    # check just confirms no crash and consequence present.)
    data = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "mask": "masked"}
        )
    )
    assert data["success"] is True
```
Run: `uv run pytest tests/unit/test_eval_fixes_3.py -k f15 -v`
Expected: PASS (the unit-level fixtures are the authoritative F15 coverage; the combined test guards against a crash when `mask=masked` flows end-to-end).

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_eval_fixes_3.py tests/fixtures/api_responses.py spliceailookup_link/mcp/shaping.py
git commit -m "fix(F15): add non-asserting masked-suppression caveat, score-gated"
```

---

## Task 4: F11 — batch per-item errors carry the full recovery scaffold

The batch `except` branch builds the full envelope via `mcp_tool_error` but appends only 4 fields. Append the recovery scaffold, and build the envelope with a `predict_splicing` context (not `predict_splicing_batch`) so the per-item fallback points at `resolve_variant {variant}` — matching the standalone error.

**Files:**
- Test: `tests/unit/test_eval_fixes_3.py`
- Modify: `spliceailookup_link/mcp/tools/batch.py:83-95` (the `except` branch)

- [ ] **Step 1: Write the failing test (with the parity assertion)**

Append to `tests/unit/test_eval_fixes_3.py`:
```python
_RECOVERY_KEYS = (
    "error_code",
    "message",
    "retryable",
    "recovery_action",
    "fallback_tool",
    "fallback_args",
    "recovery",
    "next_commands",
)


async def test_f11_batch_error_item_has_full_scaffold(mcp, stub_service: StubService) -> None:
    stub_service.score_error = DataNotFoundError("no overlap")
    res = await mcp.call_tool("predict_splicing_batch", {"variants": ["1-1-A-T"]})
    data = structured(res)
    assert data["success"] is True
    assert data["summary"]["failed"] == 1
    item = data["results"][0]
    for key in _RECOVERY_KEYS:
        assert key in item, f"batch error item missing {key}"
    assert item["error_code"] == "not_found"
    # Per-item recovery must point at resolve_variant for THIS variant (parity with
    # a standalone predict_splicing error), not get_server_capabilities.
    assert item["next_commands"][0]["tool"] == "resolve_variant"
    assert item["next_commands"][0]["arguments"]["variant"] == "1-1-A-T"


async def test_f11_batch_error_matches_standalone(mcp, stub_service: StubService) -> None:
    stub_service.score_error = DataNotFoundError("no overlap")
    standalone = structured(await mcp.call_tool("predict_splicing", {"variant": "1-1-A-T"}))
    batch = structured(await mcp.call_tool("predict_splicing_batch", {"variants": ["1-1-A-T"]}))
    item = batch["results"][0]
    for key in ("error_code", "retryable", "recovery_action", "fallback_tool", "recovery"):
        assert item[key] == standalone[key], f"scaffold mismatch on {key}"
    assert item["next_commands"] == standalone["_meta"]["next_commands"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -k f11 -v`
Expected: both FAIL (item only has `variant`/`error_code`/`message`/`retryable`).

- [ ] **Step 3: Expand the batch `except` branch**

In `spliceailookup_link/mcp/tools/batch.py`, replace the `except Exception as exc:` block (the one that appends `{variant, error_code, message, retryable}`) with:
```python
                except Exception as exc:  # capture per-item, never fail the batch
                    # Build the per-item error as a standalone predict_splicing on
                    # this variant so _fallback_for routes recovery to
                    # resolve_variant{variant} (parity with the single-call error),
                    # not the batch-context get_server_capabilities fallback.
                    env = mcp_tool_error(
                        exc,
                        McpErrorContext(tool_name="predict_splicing", variant=variant),
                    ).payload
                    results.append(
                        {
                            "variant": variant,
                            "error_code": env["error_code"],
                            "message": env["message"],
                            "retryable": env["retryable"],
                            "recovery_action": env["recovery_action"],
                            "fallback_tool": env["fallback_tool"],
                            "fallback_args": env["fallback_args"],
                            "recovery": env["recovery"],
                            "next_commands": env["_meta"]["next_commands"],
                        }
                    )
                    failed += 1
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -k f11 -v`
Expected: both PASS.

- [ ] **Step 5: Verify the existing batch suite still passes**

Run: `uv run pytest tests/unit/test_batch.py -v`
Expected: PASS (`test_batch_partial_failure_does_not_fail_batch` asserts only `error_code` — still true).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_eval_fixes_3.py spliceailookup_link/mcp/tools/batch.py
git commit -m "fix(F11): batch per-item errors carry full standalone recovery scaffold"
```

---

## Task 5: F12 — batch per-item slim observability `_meta`

Each success item currently discards `_telemetry`. Project a slim per-item `_meta` (`cache`, `upstream_elapsed_ms`, `cache_age_s`) so warm-vs-cold items are distinguishable. This **reverses** an existing test assertion — update it, don't just add.

**Files:**
- Test: `tests/unit/test_eval_fixes_3.py`
- Modify: `spliceailookup_link/mcp/tools/batch.py:79-81` (the success append, replacing `one.pop("_telemetry")`)
- Modify: `tests/unit/test_batch.py` (`test_batch_scores_each_variant_once_envelope`)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_eval_fixes_3.py`:
```python
async def test_f12_batch_items_carry_slim_meta(mcp) -> None:
    # Same string twice (batch does not dedup): item 0 misses, item 1 hits cache.
    # This is the proven pattern from test_eval_fixes_2::test_cache_ttl_and_age_in_meta
    # and does not assume chr-prefix normalization.
    res = await mcp.call_tool(
        "predict_splicing_batch",
        {"variants": ["8-140300616-T-G", "8-140300616-T-G"]},
    )
    data = structured(res)
    first, second = data["results"][0], data["results"][1]
    assert first["_meta"]["cache"] == "miss"
    assert second["_meta"]["cache"] == "hit"
    assert first["_meta"]["upstream_elapsed_ms"] is not None
    # Slim only: the verbose fields stay out of per-item _meta.
    assert "gene" not in first["_meta"]
    assert "resolution" not in first["_meta"]
    # Aggregate envelope _meta is unchanged (next_commands present).
    assert data["_meta"]["next_commands"][0]["tool"] == "predict_splicing"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py::test_f12_batch_items_carry_slim_meta -v`
Expected: FAIL (`KeyError: '_meta'` on items).

- [ ] **Step 3: Project the slim `_meta` in the success append**

In `spliceailookup_link/mcp/tools/batch.py`, replace these lines in the success path:
```python
                    one.pop("_telemetry")
                    one["variant"] = variant
                    results.append(one)
                    ok += 1
```
with:
```python
                    tele = one.pop("_telemetry")
                    one["variant"] = variant
                    item_meta: dict[str, Any] = {
                        "cache": tele.get("cache"),
                        "upstream_elapsed_ms": tele.get("upstream_elapsed_ms"),
                    }
                    if tele.get("cache_age_s") is not None:
                        item_meta["cache_age_s"] = tele["cache_age_s"]
                    one["_meta"] = item_meta
                    results.append(one)
                    ok += 1
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py::test_f12_batch_items_carry_slim_meta -v`
Expected: PASS.

- [ ] **Step 5: Fix the now-wrong existing assertion**

In `tests/unit/test_batch.py`, in `test_batch_scores_each_variant_once_envelope`, replace:
```python
    assert all("_meta" not in r for r in data["results"])  # per-item _meta suppressed
```
with:
```python
    # F12: each success item now carries a slim per-item _meta (cache visibility).
    assert all(r["_meta"]["cache"] in ("hit", "miss") for r in data["results"])
```

- [ ] **Step 6: Run the full batch suite**

Run: `uv run pytest tests/unit/test_batch.py tests/unit/test_eval_fixes_3.py -k "f11 or f12 or batch" -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_eval_fixes_3.py spliceailookup_link/mcp/tools/batch.py tests/unit/test_batch.py
git commit -m "fix(F12): batch items carry slim per-item _meta (cache visibility)"
```

---

## Task 6: #C1 — concurrency budget on the `rate_limited` envelope

The server enforces a concurrency cap (`asyncio.Semaphore(MAX_CONCURRENCY)`), not a time window. Stamp `_meta.rate_budget = {limit, remaining, unit: "concurrent_requests"}` on `rate_limited` errors only. **No `window_s`** (it would imply a token-bucket refill that does not exist).

**Files:**
- Test: `tests/unit/test_eval_fixes_3.py`
- Modify: `spliceailookup_link/mcp/errors.py` (`mcp_tool_error`, after the payload is built, on the `rate_limited` branch)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_eval_fixes_3.py`:
```python
from spliceailookup_link.config import settings


async def test_c1_rate_limited_carries_concurrency_budget(mcp, stub_service: StubService) -> None:
    stub_service.score_error = RateLimitedError("Local concurrency limit saturated")
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    assert data["error_code"] == "rate_limited"
    budget = data["_meta"]["rate_budget"]
    assert budget["limit"] == settings.MAX_CONCURRENCY
    assert budget["remaining"] == 0
    assert budget["unit"] == "concurrent_requests"
    assert "window_s" not in budget  # never fabricate a window we don't enforce


async def test_c1_success_envelope_has_no_rate_budget(mcp) -> None:
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"}))
    assert data["success"] is True
    assert "rate_budget" not in data["_meta"]
```

- [ ] **Step 2: Run to verify the first fails**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -k c1 -v`
Expected: `test_c1_rate_limited_carries_concurrency_budget` FAILS (`KeyError: 'rate_budget'`); `test_c1_success_envelope_has_no_rate_budget` PASSES already.

- [ ] **Step 3: Stamp `rate_budget` in `mcp_tool_error`**

In `spliceailookup_link/mcp/errors.py`, `mcp_tool_error`, after `payload = {...}` is constructed and before `return McpToolError(payload)`, insert:
```python
    if error_code == "rate_limited":
        # The server enforces a concurrency cap (asyncio.Semaphore), not a time
        # window -- model this as the IETF qu=concurrent-requests quota, with NO
        # window_s (there is no bucket to reset).
        payload["_meta"]["rate_budget"] = {
            "limit": settings.MAX_CONCURRENCY,
            "remaining": 0,
            "unit": "concurrent_requests",
        }
```
Add the import at the top of `errors.py` if absent:
```python
from spliceailookup_link.config import settings
```

- [ ] **Step 4: Run to verify both pass**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -k c1 -v`
Expected: both PASS.

- [ ] **Step 5: Verify error suite green**

Run: `uv run pytest tests/unit/test_errors.py -v`
Expected: PASS (existing rate_limited tests assert `error_code`/`retryable` only; the new `_meta` key is additive).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_eval_fixes_3.py spliceailookup_link/mcp/errors.py
git commit -m "feat(#C1): stamp concurrency budget on rate_limited envelope (no fake window)"
```

---

## Task 7: F16 — `resolve_variant` "normalized, not validated" caveat (doc-only)

**Files:**
- Test: `tests/unit/test_eval_fixes_3.py`
- Modify: `spliceailookup_link/mcp/tools/resolve.py:64` (the `resolve_variant` docstring)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_eval_fixes_3.py`:
```python
async def test_f16_resolve_description_states_normalized_not_validated(mcp) -> None:
    tools = await mcp.list_tools()
    desc = next(t.description for t in tools if t.name == "resolve_variant")
    low = desc.lower()
    assert "normalized" in low and "not validated" in low
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py::test_f16_resolve_description_states_normalized_not_validated -v`
Expected: FAIL.

- [ ] **Step 3: Add the caveat to the docstring**

In `spliceailookup_link/mcp/tools/resolve.py`, append to the end of the `resolve_variant` docstring (before the closing `"""`):
` Coordinate inputs are normalized, not validated: a wrong REF allele passes resolution and only fails at prediction time.`

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py::test_f16_resolve_description_states_normalized_not_validated -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_eval_fixes_3.py spliceailookup_link/mcp/tools/resolve.py
git commit -m "docs(F16): note resolve_variant normalizes coordinates, does not validate ref"
```

---

## Task 8: F17 — disambiguate `predict_splicing` vs `predict_spliceai` (ONE vs BOTH)

**Files:**
- Test: `tests/unit/test_eval_fixes_3.py`
- Modify: `spliceailookup_link/mcp/tools/combined.py:67`, `spliceailookup_link/mcp/tools/spliceai.py:77`, `spliceailookup_link/mcp/tools/pangolin.py:73` (docstrings)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_eval_fixes_3.py`:
```python
async def test_f17_descriptions_disambiguate_one_vs_both(mcp) -> None:
    tools = {t.name: t.description for t in await mcp.list_tools()}
    assert "BOTH models" in tools["predict_splicing"]
    assert "ONE model" in tools["predict_spliceai"]
    assert "ONE model" in tools["predict_pangolin"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py::test_f17_descriptions_disambiguate_one_vs_both -v`
Expected: FAIL.

- [ ] **Step 3: Lead each docstring with the contrast**

In `spliceailookup_link/mcp/tools/combined.py`, prefix the `predict_splicing` docstring with:
`BOTH models (SpliceAI + Pangolin) in one call -- the default "what does this variant do to splicing?" answer. `

In `spliceailookup_link/mcp/tools/spliceai.py`, prefix the `predict_spliceai` docstring with:
`ONE model only (SpliceAI); use predict_splicing for BOTH models with an agreement verdict. `

In `spliceailookup_link/mcp/tools/pangolin.py`, prefix the `predict_pangolin` docstring with:
`ONE model only (Pangolin); use predict_splicing for BOTH models with an agreement verdict. `

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py::test_f17_descriptions_disambiguate_one_vs_both -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_eval_fixes_3.py spliceailookup_link/mcp/tools/combined.py spliceailookup_link/mcp/tools/spliceai.py spliceailookup_link/mcp/tools/pangolin.py
git commit -m "docs(F17): lead prediction tool descriptions with ONE vs BOTH"
```

---

## Task 9: §8 Durability invariants — forbid the "second-class path" class

Four structural/parity tests that fail CI if any future change re-introduces the *class* of divergence independent testers keep finding. These add no payload weight.

**Files:**
- Test: `tests/unit/test_eval_fixes_3.py`

- [ ] **Step 1: Write the four invariant tests**

Append to `tests/unit/test_eval_fixes_3.py`:
```python
# ---------------- §8 durability invariants ----------------


async def test_inv_batch_item_matches_single_call(mcp) -> None:
    """§8.1 success parity: a batch item == standalone result minus outer envelope."""
    single = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    batch = structured(
        await mcp.call_tool("predict_splicing_batch", {"variants": ["chr8-140300616-T-G"]})
    )
    item = batch["results"][0]
    shared = ("agreement", "interpretation", "consequence", "transcript", "headline")
    for key in shared:
        if key in single:
            assert item.get(key) == single[key], f"batch/single divergence on {key}"
    if "molecular_consequence" in single:
        assert item.get("molecular_consequence") == single["molecular_consequence"]


async def test_inv_cross_tool_error_envelope_parity(mcp, stub_service: StubService) -> None:
    """§8.2: every resolve/predict tool emits the same error key set."""
    stub_service.score_error = DataNotFoundError("no overlap")
    stub_service.resolve_error = DataNotFoundError("no overlap")
    required = {
        "error_code",
        "message",
        "retryable",
        "recovery_action",
        "fallback_tool",
        "fallback_args",
        "recovery",
    }
    for tool in ("predict_spliceai", "predict_pangolin", "predict_splicing", "resolve_variant"):
        data = structured(await mcp.call_tool(tool, {"variant": "8-140300616-T-G"}))
        assert data["success"] is False
        assert required <= set(data), f"{tool} dropped error keys: {required - set(data)}"
        assert "next_commands" in data["_meta"]
    # And the batch error item (built with a predict_splicing context) carries them.
    batch = structured(await mcp.call_tool("predict_splicing_batch", {"variants": ["8-140300616-T-G"]}))
    item = batch["results"][0]
    assert (required - {"message"}) <= set(item)


async def test_inv_no_duplicated_threshold_basis(mcp) -> None:
    """§8.3: the static THRESHOLD_BASIS string appears at most once per payload."""
    combined = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert json.dumps(combined).count(THRESHOLD_BASIS) <= 1
    batch = structured(
        await mcp.call_tool(
            "predict_splicing_batch",
            {"variants": ["chr8-140300616-T-G", "8-140300616-T-G"]},
        )
    )
    # One per item is acceptable (each item is a standalone-equivalent result), but
    # never more than one within a single item.
    for item in batch["results"]:
        assert json.dumps(item).count(THRESHOLD_BASIS) <= 1


async def test_inv_no_null_leaf_in_full_mode(mcp) -> None:
    """§8.4: full-mode payloads omit-when-null rather than ship null leaves."""

    def walk(node: object, path: str = "") -> list[str]:
        bad: list[str] = []
        if isinstance(node, dict):
            for k, v in node.items():
                bad += walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                bad += walk(v, f"{path}[{i}]")
        elif node is None:
            bad.append(path)
        return bad

    data = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
        )
    )
    # Drop the conventional nullable telemetry/fallback fields that are legitimately
    # null on a success envelope, then assert nothing else is null.
    nulls = [p for p in walk(data) if p.rsplit(".", 1)[-1] not in {
        "fallback_tool", "fallback_args", "cache_age_s", "upstream_elapsed_ms", "signed_score",
    }]
    assert nulls == [], f"unexpected null leaves in full mode: {nulls}"
```

- [ ] **Step 2: Run the invariants**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -k inv -v`
Expected: all PASS. If `test_inv_no_null_leaf_in_full_mode` reports a null path, that is a real omit-when-null gap — fix the producing shaper to drop the key (do not add the field to the allowlist unless it is a documented nullable like the ones listed). If `test_inv_cross_tool_error_envelope_parity` fails for a tool, that tool is dropping the scaffold — fix the tool, not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_eval_fixes_3.py
git commit -m "test(§8): add batch/single, cross-tool error, dedup, no-null-leaf invariants"
```

---

## Task 10: Capabilities + glossary updates (F16/F17/#C1/F14/F15)

The `capabilities_version` hash is derived from the document content (`resources.py:32` `_capabilities_version`), so editing the doc changes the hash automatically. Document the new contracts so discovery reflects them.

**Files:**
- Test: `tests/unit/test_eval_fixes_3.py`
- Modify: `spliceailookup_link/mcp/resources.py` (`recommended_workflows`, glossary, error-taxonomy/rate sections)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_eval_fixes_3.py`:
```python
async def test_caps_document_new_contracts(mcp) -> None:
    caps = structured(await mcp.call_tool("get_server_capabilities", {}))
    blob = json.dumps(caps).lower()
    # F17 which-tool guidance
    assert "which tool" in blob or ("both models" in blob and "one model" in blob)
    # F16 caveat
    assert "normalized, not validated" in blob
    # #C1 concurrency unit (never a fabricated window)
    assert "concurrent_requests" in blob
    assert "window_s" not in blob
    # F14 aberration sub-field note
    assert "size_is_coding" in blob


async def test_caps_version_changes_and_is_stable(mcp) -> None:
    a = structured(await mcp.call_tool("get_server_capabilities", {}))
    b = structured(await mcp.call_tool("get_server_capabilities", {}))
    assert a["capabilities_version"] == b["capabilities_version"]  # stable
    assert isinstance(a["capabilities_version"], str) and len(a["capabilities_version"]) >= 8
```

- [ ] **Step 2: Run to verify the contract test fails**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -k caps -v`
Expected: `test_caps_document_new_contracts` FAILS; `test_caps_version_changes_and_is_stable` PASSES.

- [ ] **Step 3: Edit `resources.py`**

In `spliceailookup_link/mcp/resources.py`:

(a) Add a which-tool line to the `recommended_workflows` list (near line 61):
```python
            "Which tool? predict_splicing = BOTH models (default); "
            "predict_spliceai / predict_pangolin = ONE model only.",
```

(b) In the glossary section, add an entry documenting the aberration sub-fields and the masked caveat (near the existing `sai10k_consequence` entry, ~line 97):
```python
            "aberration_fields": (
                "consequence.aberrations[].status / size_is_coding / introduces_stop_codon "
                "are SAI-10k coding-impact fields, populated only for coding-relevant "
                "aberration classes; absent keys mean upstream did not compute them (not "
                "'false'). Under mask='masked' an empty aberrations list with a non-trivial "
                "delta carries a consequence.note that mask='raw' may reveal a suppressed "
                "aberration."
            ),
```

(c) In the glossary / resolver section, add the F16 caveat (near the resolver description):
```python
            "resolve_caveat": (
                "Coordinate inputs are normalized, not validated: a wrong REF allele passes "
                "resolution and only fails at prediction time."
            ),
```

(d) In the error-taxonomy / concurrency section (near `max_concurrent_requests`, ~line 154), document the rate budget shape:
```python
            "rate_budget": (
                "On a rate_limited error, _meta.rate_budget reports "
                "{limit, remaining, unit:'concurrent_requests'} -- a concurrency cap, not a "
                "time window (there is no window_s to wait out; reduce concurrent calls)."
            ),
```

- [ ] **Step 4: Run to verify both pass**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -k caps -v`
Expected: both PASS.

- [ ] **Step 5: Verify the resources/tools suites still pass**

Run: `uv run pytest tests/unit/test_tools.py -v`
Expected: PASS (if a test pins the exact `capabilities_version` hash, update it to the new value printed by the failure — the hash legitimately changes when the doc changes).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_eval_fixes_3.py spliceailookup_link/mcp/resources.py
git commit -m "docs: document which-tool, resolve caveat, rate_budget, aberration fields in capabilities"
```

---

## Task 11: Full gate + Part 8 evaluation appendix

**Files:**
- Modify: `docs/mcp-evaluation.md` (append Part 8)

- [ ] **Step 1: Run the full local CI gate**

Run: `make ci-local`
Expected: format clean, Ruff clean, `lint-loc` green (every module < 600 LOC — re-check `batch.py`, `shaping.py`, `errors.py`, `_predict.py` after the edits), mypy clean, full pytest suite green, coverage ≥ 80%.

If `lint-loc` reports a file over budget, split the smallest cohesive helper out (e.g. move the F15 caveat string to a module constant) — do not add to `.loc-allowlist`.

- [ ] **Step 2: Confirm the new regression file ran**

Run: `uv run pytest tests/unit/test_eval_fixes_3.py -v`
Expected: every F11/F12/F13/F14/F15/F16/F17/#C1 test and the four `inv` invariants PASS.

- [ ] **Step 3: Append Part 8 to the evaluation doc**

Add to the end of `docs/mcp-evaluation.md`:
```markdown
## Part 8 -- Corrective pass for Part 7 findings (v0.5.0)

**Date:** 2026-06-12 · **Server:** spliceailookup-link **v0.5.0**
**Basis:** deterministic unit suite (`make ci-local` green, coverage >=80%);
findings F11-F17 + #C1 from Part 7 closed and verified offline. A live
re-exercise against the rate-limited upstream is recommended once deployed.

### Part 7 findings -- resolved

| # | Sev | Status | Fix + proof |
|---|---|---|---|
| F11 | MED | Fixed | Batch per-item errors carry the full standalone scaffold (`recovery_action`/`fallback_*`/`recovery`/`next_commands`), built with a `predict_splicing` context so per-item recovery targets `resolve_variant{variant}`. Tests: `test_f11_*`, parity `test_f11_batch_error_matches_standalone`. |
| F12 | LOW-MED | Fixed | Each batch success item carries a slim `_meta` (`cache`/`upstream_elapsed_ms`/`cache_age_s`). Test: `test_f12_batch_items_carry_slim_meta`. |
| F13 | LOW | Fixed | `threshold_basis` emitted once per combined payload (sub-block copies stripped in `predict_one`; single-model unchanged). Tests: `test_f13_*`, invariant `test_inv_no_duplicated_threshold_basis`. |
| F14 | INVESTIGATE | Resolved | Aberration sub-fields are real (fixture populates them); shaped output omits them per-field when upstream sends null. Tests: `test_f14_*`, invariant `test_inv_no_null_leaf_in_full_mode`. |
| F15 | LOW | Fixed | Score-gated, non-asserting masked-suppression caveat on `consequence.note`. Tests: `test_f15_*` (fires on real signal; silent on no-effect and raw). |
| F16 | LOW | Fixed (doc) | `resolve_variant` description + glossary state coordinates are normalized, not validated. Test: `test_f16_*`. |
| F17 | ERGONOMIC | Fixed (non-breaking) | Descriptions lead with ONE vs BOTH; capabilities `recommended_workflows` adds a which-tool line. Rename deferred. Test: `test_f17_*`. |
| #C1 | additive | Added | `rate_limited` envelope carries `_meta.rate_budget {limit, remaining, unit:'concurrent_requests'}` -- a concurrency quota, not a fabricated time window. Test: `test_c1_*`. |

### Durability margin (new)

Four structural invariants now forbid the "second-class path" class that every prior
independent re-test surfaced: batch⇄single shape parity, cross-tool error-envelope
parity, single-`threshold_basis`, and no-null-leaf-in-full. These convert one-off
fixes into a standing contract the suite enforces.

*Research use only; not for clinical decision support. Splice predictions are
computational and must be interpreted alongside orthogonal evidence.*
```

- [ ] **Step 4: Final commit**

```bash
git add docs/mcp-evaluation.md
git commit -m "docs: add Part 8 v0.5.0 re-evaluation appendix"
```

- [ ] **Step 5: Confirm completion**

Run: `make ci-local && git log --oneline -12`
Expected: gate green; 11 task commits on `eval-improvements-3`.

---

## Self-Review (run before handoff)

**Spec coverage:** F11 (Task 4), F12 (Task 5), F13 (Task 1), F14 (Task 2), F15 (Task 3), F16 (Task 7), F17 (Task 8), #C1 (Task 6), §8 invariants (Task 9), capabilities/glossary (Task 10), version + gate + Part 8 (Tasks 0/11). Every spec §4 finding and the §8 durability margin maps to a task.

**Type/name consistency:** `_shape_consequence(payload, mode, max_score=None)` is defined and called with `max_overall` in Task 3; `THRESHOLD_BASIS` imported from `spliceailookup_link.mcp.shaping` in Task 1 and reused in Task 9; `McpErrorContext(tool_name="predict_splicing", ...)` used identically in Task 4 (batch) and matches the standalone context the parity test compares against; `settings.MAX_CONCURRENCY` imported in Task 6 and asserted in the same task's test.

**Ordering rationale:** pure-shaping fixes first (F13/F14/F15 — unit-testable without the upstream), then batch wiring (F11/F12 — depend on the shaped per-item result), then envelope/#C1, then doc-only (F16/F17), then the invariants (which assume all prior fixes), then capabilities + gate. Each task is independently committable and leaves the suite green.

---

*Research use only; not for clinical decision support. Splice predictions are computational and must be interpreted alongside orthogonal evidence.*
