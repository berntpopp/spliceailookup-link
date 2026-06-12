# spliceailookup-link — Evaluation-Driven Improvements, Pass 3 (Design)

**Date:** 2026-06-12
**Author:** MCP engineering (driven by `docs/mcp-evaluation.md` Part 7)
**Status:** Design — **reviewed & corrected** (engineering review 2026-06-12; see §8)
**Baseline:** v0.4.0 (deployed) · MCP protocol 2025-11-25 · FastMCP 3.x
**Target version:** v0.5.0
**Plan:** `docs/superpowers/plans/2026-06-12-eval-improvements-3.md`

> **Review note (2026-06-12).** A code-grounded engineering review confirmed the
> Part 7 findings against the live source and corrected four items before
> implementation: **#C1** must report a concurrency quota, not a fabricated time
> window (the server enforces an `asyncio.Semaphore`, never a rate window — see
> §4 #C1 and the IETF ratelimit-headers `qu=concurrent-requests` model); **F13**
> is combined-path-only (standalone single-model tools legitimately keep one
> `threshold_basis`); **F14** needs no live spike (the sub-fields are already
> plumbed at `shaping.py:217-226` — it is a fixtures/contract question, default
> omit-when-null); **F15** ships as a non-asserting *caveat* gated on a real
> score, not a claim that raw "would" show an aberration. A new §8 adds the
> **durability margin** that the projection-vs-independent-retest gap (Part 3→4,
> Part 6→7) shows is required to actually clear >9 on a fresh pass.

## 1. Why

`docs/mcp-evaluation.md` Part 6 projected ~9.5 on both axes *offline*. Part 7 — a
fresh **live black-box re-test of the deployed v0.4.0** (7/7 tools, 5/5 resources,
3/7 error codes triggered live, cross-tool numeric consistency checked) — lands
honestly at **~9.0 / ~9.0**. This is not a regression: every prior fix (F1–F10,
G1/G2, the minimal crash) holds up live and the numbers are clean. The gap is a
cluster Parts 1–6 never scoped:

> **`predict_splicing_batch` is a second-class citizen** relative to the
> single-call path — its per-item errors and per-item observability are
> degraded — plus static-string duplication and a few null / interpretation gaps.

This pass closes that cluster so a fresh independent re-test clears **>9.2 on both
axes**, without touching the load-bearing constraints (thin FastAPI host, MCP
facade is the product, aggressive caching, low `MAX_CONCURRENCY`, 600-LOC/file
budget, research-use-only framing, HTTP-200-with-`error` upstream contract).

### Part 7 evidence (load-bearing)

- **F11 root cause is code-confirmed.** `mcp/tools/batch.py:84` already builds the
  *full* error envelope via `mcp_tool_error(...)` — including `recovery_action`,
  `fallback_tool`, `fallback_args`, `recovery`, and `_meta.next_commands` — then
  `:88-93` copies **only** `{variant, error_code, message, retryable}` into the
  result, discarding the rest. The standalone `predict_spliceai("not-a-variant")`
  returns the full recovery scaffold; the same input inside the batch does not.
- **F12.** `batch.py:79` does `one.pop("_telemetry")` and the envelope carries one
  aggregate `_meta` only (`:116-134`); per-item `cache` / `cache_age_s` /
  `upstream_elapsed_ms` are not surfaced, so warm-vs-cold items are
  indistinguishable in a 25-variant panel.
- **F13.** In one live `predict_splicing` payload the static string
  `"Δ>=0.5 high; 0.2-0.5 moderate; >0-0.2 low; 0 none (...)"` appeared **3×**
  (spliceai block, pangolin block, top-level `interpretation`). Sources:
  `mcp/tools/_predict_shape.py`, `mcp/shaping.py`.
- **F15.** `mask=masked` returned `consequence.aberrations:[]` while
  `max_delta_score` stayed `0.83` (identical to raw) — a consumer keying on the
  score gets no signal the exon-skip prediction was suppressed by masking.

## 2. Goal & score model

**Target the projection at ~9.5, not 9.2** — because the goal is for the *next
independent re-test* to clear >9, and the maintainer's self-projections have
consistently overshot independent passes by ~0.4–1.0 (Part 3 self 9.1–9.2 →
Part 4 independent 8.0–8.5; Part 6 self 9.5 → Part 7 independent 9.0). Projecting
9.2 here would, by that pattern, land an independent Part 8 at ~8.7–8.9 — under
goal. The fixes below (the named Part 7 findings) plus the §8 durability margin
(parity invariants + pre-empting the next "second-class path" cluster) are what
buy the headroom. No new latency, no new upstream dependency; all changes additive.

Senior-tester axis (per-tool; Part 7b):

| Tool | v0.4.0 | Target | Lever |
|---|---|---|---|
| `predict_splicing_batch` | 7.5 | 9 | **F11** full per-item error + **F12** per-item observability |
| `predict_splicing` | 9 | 9.5 | **F13** dedup `threshold_basis` |
| `predict_spliceai` | 9 | 9.5 | **F15** masking-suppression note |
| `resolve_variant` | 9 | 9 | **F16** documented caveat (no behavior change) |
| `get_server_capabilities` | 9.5 | 9.5 | **F17** description disambiguation + **#C1** doc |
| `predict_pangolin` | 9 | 9 | unchanged |
| `warmup` | 9 | 9 | unchanged |

Projected tester mean: (9.5+9.5+9.5+9+9+9+9)/7 ≈ **9.07**.

LLM-consumer axis (per-dimension; Part 7a):

| Dimension | v0.4.0 | Target | Lever |
|---|---|---|---|
| Token efficiency | 8 | 9 | **F13** dedup + **F14** null-trim |
| Composability | 9 | 9.5 | **F11** batch errors actionable again |
| Observability | 9 | 9.5 | **F12** per-item + **#C1** rate budget |
| Schema / decision-completeness | 9 | 9.5 | **F15** masking note |
| Error / recovery | 10 | 10 | unchanged (F11 restores batch parity) |
| Speed / latency | 8 | 8 | upstream-bound ceiling; no server lever |

Projected consumer mean lands ~9.2. Latency stays the honest ceiling
(interactive-use-only Cloud Run, 13–40 s cold); the background-task path remains
the only mitigation and is already advertised (Part 6 / #2).

## 3. Scope (decided)

In scope (v0.5.0):

1. **F11 (MED)** — batch per-item errors carry the full standalone envelope.
2. **F12 (LOW-MED)** — batch per-item slim observability (`cache`, `upstream_elapsed_ms`).
3. **F13 (LOW)** — emit `threshold_basis` once per payload, not per block.
4. **F15 (LOW)** — note when `mask=masked` suppresses a raw-mode aberration.
5. **F16 (LOW)** — document the `resolve_variant` coordinate-passthrough caveat.
6. **F17 (ERGONOMIC, non-breaking half)** — sharpen the `predict_spliceai` vs
   `predict_splicing` tool descriptions + capabilities to make the choice
   unmissable. **Rename is out of scope** (breaking; AGENTS.md preserves tool names).
7. **#C1** — live rate budget in `_meta` (at minimum on `rate_limited`).

Investigate before fixing:

- **F14 (INVESTIGATE)** — determine whether `consequence.aberrations[].status` /
  `size_is_coding` / `introduces_stop_codon` ever populate from the SAI-10k
  payload. If they do, it is a data-plumbing gap (fix); if they never do for this
  call shape, omit-when-null. A 1-hour spike against the live SAI-10k response
  decides; do **not** blind-fix.

Out of scope (deferred to backlog, lower value):

- **#C2** pre-call cache visibility (a `cached` probe) — defer; the post-hoc
  `cache`/`cache_age_s` already exists and #C2 mainly helps the sync-vs-background
  decision, which background-tasks already covers.
- **#C3** `lite` capabilities tier — defer; the `spliceailookup://usage` resource
  already serves the compact-discovery need.
- Tool rename (F17 breaking half), new prediction models, allele-frequency /
  ClinVar / expression (delegated to sibling `-link` servers), any clinical
  framing, REST beyond `/health`, multi-worker task backend.

## 4. Findings → fixes

### F11 — batch per-item errors are second-class (MED)

**Where:** `spliceailookup_link/mcp/tools/batch.py:83-94`.

**Now:** the `except` branch computes `env = mcp_tool_error(...).payload` (the full
envelope) but appends only four fields.

**Fix:** carry the recovery scaffold the envelope already holds. Append a per-item
error block of the same shape standalone callers get, minus the redundant
top-level `success`:

```python
except Exception as exc:  # capture per-item, never fail the batch
    # IMPORTANT: build the per-item error as if it were a standalone
    # predict_splicing on this variant, NOT a "predict_splicing_batch" context.
    # _fallback_for() routes prediction-tool errors to resolve_variant{variant}
    # (the actionable per-variant recovery) but routes a "predict_splicing_batch"
    # context to get_server_capabilities. Using "predict_splicing" here is what
    # makes the per-item scaffold equal the standalone one (the §8.1 parity
    # invariant) and gives a panel-runner the right next step.
    env = mcp_tool_error(
        exc, McpErrorContext(tool_name="predict_splicing", variant=variant)
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

Token cost is bounded (errors are the minority of a panel) and the value is exactly
where a panel-runner is stuck. No new computation — the data is already built.

**Spec grounding.** This is also the protocol-correct home for the data. MCP
2025-11-25 (`server/tools`, Error Handling) reserves the result-level `isError:true`
mechanism for *whole-call* failures; a batch must stay `success:true` while some
items fail, so per-item errors **must** live inside the structured result — and the
spec's guidance is that execution errors "contain actionable feedback that language
models can use to self-correct," which is precisely the `recovery_action` /
`fallback_*` / `recovery` / `next_commands` scaffold standalone callers already get.
Mirroring the single-call error object inside each item gives the consumer **one
error shape** across both call styles (the durability invariant in §8).

### F12 — batch loses per-item observability (LOW-MED)

**Where:** `batch.py:79` (`one.pop("_telemetry")`) and the per-item success append.

**Fix:** instead of discarding `_telemetry`, project a slim per-item
`_meta = {"cache": ..., "upstream_elapsed_ms": ..., "cache_age_s": ...}` onto each
success result (drop the verbose fields, keep the few that answer "did this item
hit the upstream?"). Keep the aggregate envelope `_meta` as-is. This restores
warm-vs-cold visibility without re-inflating the batch to N full `_meta` blocks.

**Test impact (must update, not just add):** the existing
`tests/unit/test_batch.py::test_batch_scores_each_variant_once_envelope` asserts
`all("_meta" not in r for r in data["results"])`. That assertion encodes the *old*
"per-item `_meta` suppressed" behaviour and is exactly what F12 reverses — flip it
to assert each success item now carries a slim `_meta` with `cache`. (A naive
"add a test" pass would leave a now-wrong assertion green-by-omission and fail CI.)

### F13 — `threshold_basis` triplicated (LOW) — **CORRECTED (combined-path only)**

**Where:** `mcp/tools/_predict.py` (`predict_one`), **not** `mcp/shaping.py`.

**Confirmed source of the 3 copies.** In a `predict_splicing` (combined) payload,
`threshold_basis` appears in (1) `result["spliceai"]["interpretation"]`
(`shaping.py:263`), (2) `result["pangolin"]["interpretation"]` (`shaping.py:374`),
and (3) the top-level `result["interpretation"]` from `combined_interpretation`
(`_predict_shape.py:87`). **Standalone `predict_spliceai` / `predict_pangolin`
carry exactly one copy** (their own `result["interpretation"]`) and are correct as
they are — they are self-contained single-model answers.

**Fix (scoped to the combined assembly).** Do **not** remove `threshold_basis`
from `shape_spliceai` / `shape_pangolin` (that would strip the legitimate single
copy from the standalone tools). Instead, in `predict_one`, after the sub-blocks
are shaped, pop the redundant string from the two sub-blocks while keeping their
`band`:

```python
for sub in ("spliceai", "pangolin"):
    interp = (result.get(sub) or {}).get("interpretation")
    if interp:
        interp.pop("threshold_basis", None)   # keep band; basis lives top-level once
```

The top-level `combined_interpretation` keeps the single `threshold_basis`. This
fixes both `predict_splicing` and `predict_splicing_batch` (both route through
`predict_one`) in one place. F6 verdict/headline regression tests stay green
(headline does not read `threshold_basis`).

### F14 — null SAI-10k aberration sub-fields (INVESTIGATE) — **CORRECTED (not a plumbing gap)**

**Where:** `mcp/shaping.py:217-226` (`_shape_consequence`).

**Correction.** The original draft hypothesised a "data-plumbing gap." Code review
**falsifies** that: `_shape_consequence` already reads `ab.get("status")`,
`ab.get("size_is_coding")`, and `ab.get("introduces_stop_codon")` straight off each
upstream aberration object. The plumbing exists; if the values are `null`, the
upstream SAI-10k object simply did not carry those keys for that call shape. So
**(a) "plumb them through" is a no-op** — there is nothing to plumb.

**Resolved from in-tree evidence (decision locked).** The investigation is done —
`tests/fixtures/api_responses.py` `SPLICEAI_TRAPPC9.sai10kPredictions.aberrations[0]`
already carries `status: "frameshift"`, `size_is_coding: True`,
`introduces_stop_codon: True`. So the fields **are real and do populate** for an
`exon_skipping` aberration; the Part 7 live observation that they were `null` means
that *specific* upstream response simply omitted/nulled them for that variant. Two
things both true → one fix:

- **Keep the fields** (do not delete them — they carry genuine coding-impact signal
  when present).
- **Omit them per-aberration when the upstream value is `null`** in
  `_shape_consequence`, so a sparse live response ships `{type, affected_region}`
  rather than three `null` leaves:

```python
out["aberrations"] = [
    {k: v for k, v in {
        "type": ab.get("aberration_type"),
        "affected_region": ab.get("affected_region"),
        "status": ab.get("status"),
        "size_is_coding": ab.get("size_is_coding"),
        "introduces_stop_codon": ab.get("introduces_stop_codon"),
    }.items() if v is not None}
    for ab in (raw_aberr or [])
]
```

This satisfies the §8.4 "no null leaf in `full` mode" invariant, keeps the
`SPLICEAI_TRAPPC9` fixture's populated fields, and needs **no live call**. Glossary
gains one line: SAI-10k populates `status`/`size_is_coding`/`introduces_stop_codon`
only for coding-relevant aberration classes; absent keys mean upstream did not
compute them, not "false".

### F15 — masked suppression is silent (LOW) — **CORRECTED (caveat, not a claim)**

**Where:** `mcp/shaping.py` (`_shape_consequence`) — emit the note where the
`aberrations` list is built, so it covers combined **and** single-model paths from
one site.

**Correction.** The original note asserted masking *"suppressed a raw-mode
aberration"* — a claim we have not verified (we did not call raw). A variant can
have an empty `aberrations` list under masked **and** under raw; asserting raw
"would" show one is sometimes false, and a confidently-wrong note is worse than
none. We also must not fire on genuinely no-effect variants (empty `aberrations`
is the *normal* case there).

**Fix.** Emit a non-asserting **caveat**, gated on a real splice signal so it never
fires on no-effect variants and never claims a fact we did not compute:

```python
# in _shape_consequence, after building out["aberrations"]
masked = str(payload.get("mask")) in ("1", "True", "true")
max_score = ...  # already computed by the caller / re-derivable from scores
if masked and not out["aberrations"] and (max_score or 0) >= _MODERATE:
    out["note"] = (
        "mask='masked' computes aberrations on masked scores and can suppress an "
        "aberration that mask='raw' would predict; this site has a non-trivial "
        "delta (>=0.2) but no masked aberration — re-run with mask='raw' to check."
    )
```

Properties: (1) raw mode never carries the note (`masked` gate); (2) no second
upstream call; (3) no over-fire on no-effect variants (`>=_MODERATE` gate); (4) the
wording is a caveat about *how masking works*, true regardless of what raw would
return. Tests assert it appears for a masked+empty+high-score fixture and is absent
for a masked no-effect fixture and for any raw-mode result.

### F16 — `resolve_variant` coordinate passthrough (LOW, doc-only)

**Where:** `mcp/tools/resolve.py` + capabilities glossary.

**Fix:** no behavior change (no local genome to validate against). Document in the
`resolve_variant` description and the glossary that coordinate inputs are
**normalized, not validated** — a wrong ref allele passes resolution and only fails
at prediction time. Removes false confidence.

### F17 — tool-name collision (ERGONOMIC, non-breaking half)

**Where:** `mcp/tools/spliceai.py`, `combined.py` tool descriptions; capabilities.

**Fix:** lead each description with the contrast in caps —
`predict_splicing`: "BOTH models (SpliceAI + Pangolin), the default one-call
answer"; `predict_spliceai` / `predict_pangolin`: "ONE model only". Add a
"which tool?" one-liner to `recommended_workflows`. Rename deferred (breaking).

### #C1 — live concurrency budget (additive) — **CORRECTED**

**Where:** `mcp/errors.py` — `mcp_tool_error` / `_classify`, on the `rate_limited`
branch. (Not the service layer; see below.)

**Correction (load-bearing).** The original draft proposed
`_meta.rate_budget: {remaining, window_s}`. That is **wrong for this server** and
would ship a misleading field. The server does **not** enforce a time-windowed
rate limit: `api/base_client.py:68` is a bare `asyncio.Semaphore(MAX_CONCURRENCY)`
(default 2), and `RateLimitedError` is raised either when that semaphore acquire
times out (`_acquire_slot`, local saturation) or on a persistent upstream 429.
There is **no window** to reset against, so emitting a `window_s` implies a
token-bucket refill that never happens. The IETF `draft-ietf-httpapi-ratelimit-
headers` defines exactly this case as `qu=concurrent-requests` (a quota in
*concurrent requests*, window deliberately omitted).

**Fix.** On the `rate_limited` envelope, stamp:

```python
"_meta": {
    ...,
    "rate_budget": {
        "limit": settings.MAX_CONCURRENCY,   # static, always known
        "remaining": 0,                       # a saturation error means 0 free slots
        "unit": "concurrent_requests",        # NOT a time window
    },
}
```

`MAX_CONCURRENCY` is a static config value, so no semaphore introspection or new
computation is needed — this is a pure `errors.py` change on the `rate_limited`
branch (and only there). Do **not** fabricate a window; do **not** reach into the
private `_semaphore._value`. On success envelopes, **omit** `rate_budget` rather
than report an instantaneously-stale free-slot count — the field's only honest job
is to tell a saturated caller the cap it just hit and the unit to pace by.
The `recovery` prose for `rate_limited` already says "reduce concurrent calls";
this makes that machine-readable. Note `rate_limited` is the one finding Part 7
could not trigger live, so this is also the first time the envelope is exercised.

## 5. Module map (600-LOC budget)

All edits small and additive; re-check `make lint-loc` stays < 600 after each
(current ceilings: `shaping.py` 405, `errors.py` 362, `resources.py` 321,
`batch.py` 136 — all comfortable):

- `mcp/tools/batch.py` (136 → ~155) — F11 per-item error fields, F12 slim `_meta`.
- `mcp/tools/_predict.py` (226 → ~235) — F13 pop `threshold_basis` from the two
  sub-blocks (combined-only); no change to single-model.
- `mcp/shaping.py` (405 → ~420) — F14 omit-when-null (if fixtures confirm) + F15
  caveat in `_shape_consequence`; both single-site, covers combined + single-model.
- `mcp/tools/resolve.py` — F16 description caveat only (doc string).
- `mcp/tools/spliceai.py` / `pangolin.py` / `combined.py` — F17 description
  sharpening (lead with ONE / BOTH).
- `mcp/errors.py` (362 → ~370) — #C1 `rate_budget` on the `rate_limited` branch.
- `mcp/resources.py` — capabilities: F16 caveat, F17 "which tool?", #C1 field +
  `qu=concurrent_requests` semantics, glossary note for the F14 outcome and the F15
  masked caveat; bump `capabilities_version`.
- `tests/unit/test_eval_fixes_3.py` — **new** regression file (matches the
  `test_eval_fixes.py` / `test_eval_fixes_2.py` convention).
- `tests/conftest.py` / `tests/fixtures/` — masked-empty-high-score fixture (F15),
  cached-vs-uncached batch fixture (F12) if not already expressible via the stub.
- `__init__.py` / `pyproject.toml` — version → 0.5.0.

## 6. Testing (respx-mocked, deterministic)

- **F11:** a batch with one invalid item asserts the error block now contains
  `recovery_action`, `fallback_tool`, `fallback_args`, `recovery`, `next_commands`
  — and that they equal the standalone `predict_spliceai` envelope's values for the
  same input (parity test). Batch still `success:true`, `summary.failed == 1`.
- **F12:** a batch over a cached + an uncached variant asserts per-item
  `_meta.cache` differs (`hit` vs `miss`) and `upstream_elapsed_ms` present on the
  miss; aggregate envelope `_meta` unchanged.
- **F13:** a `predict_splicing` payload contains exactly **one** `threshold_basis`
  string (assert count == 1) and a `band` in each model block. F6
  verdict/headline regression tests stay green.
- **F15:** masked input whose raw counterpart predicts an aberration carries
  `consequence.note`; a genuinely no-effect masked variant does **not** (no
  over-fire). Raw mode never carries the note.
- **F16:** capabilities glossary + `resolve_variant` description mention
  "normalized, not validated" for coordinate input.
- **F17:** capabilities `recommended_workflows` contains the "which tool?" line;
  tool descriptions lead with BOTH / ONE.
- **#C1:** a forced concurrency-saturation `rate_limited` envelope carries
  `_meta.rate_budget == {"limit": 2, "remaining": 0, "unit": "concurrent_requests"}`
  and **no** `window_s` key; a success envelope has **no** `rate_budget`.
- **Capabilities:** `capabilities_version` changes from the v0.4.0 hash and is
  stable across calls.
- **Durability invariants (§8):** the four parity/structural tests pass.
- `make ci-local` green, coverage ≥ 80%, every module < 600 LOC.

## 7. Acceptance

- `make ci-local` green; all F11/F12/F13/F15/F16/F17/#C1 tests pass.
- A live `predict_splicing_batch(["<valid>", "not-a-variant"])` returns a failed
  item whose recovery scaffold matches the standalone error, plus per-item
  `cache`/`upstream_elapsed_ms` on the successes.
- A live `predict_splicing("<HGVS>")` payload contains `threshold_basis` once.
- F14 resolved from in-tree evidence: either the sub-fields are kept (a fixture
  populates them) or `_shape_consequence` omits-when-null and the glossary states
  why; **no live spike** unless fixtures and contract are both silent.
- The four §8 durability invariants pass (batch⇄single parity, cross-tool error
  parity, single-`THRESHOLD_BASIS`, no-null-leaf-in-full).
- A fresh independent Part-8 re-test scores **>9.2 on both axes** (projected ~9.5
  to absorb the historical projection gap), with `predict_splicing_batch` ≥ 9.

## 8. Durability margin — break the projection-vs-retest gap

Every independent re-test so far has found a *new* "second-class path" cluster the
prior self-projection did not scope (Part 4: headline vs verdict; Part 7: batch vs
single-call). Fixing only the named findings leaves the next tester free to find
the next such cluster. The durable lever is **structural parity invariants** that
forbid the *class* of divergence — they add no payload weight and turn "we fixed
these instances" into "the suite rejects the pattern." Add these to
`test_eval_fixes_3.py`:

1. **Batch ⇄ single-call shape parity (locks F11/F12 permanently).** For a valid
   variant, assert a `predict_splicing_batch([v])` item equals the standalone
   `predict_splicing(v)` result minus the outer `success`/`_meta` envelope (same
   keys: `agreement`, `interpretation`, `molecular_consequence` when present,
   `consequence`, `transcript`, plus the slim per-item `_meta`). For an invalid
   variant, assert the batch error item's recovery keys equal the standalone error
   envelope's (the F11 parity test, generalised).

2. **Cross-tool error-envelope parity.** Drive one invalid input through every
   tool that resolves/predicts (`resolve_variant`, `predict_spliceai`,
   `predict_pangolin`, `predict_splicing`, and one batch item) and assert every
   error carries the same key set (`error_code`, `message`, `retryable`,
   `recovery_action`, `fallback_tool`, `fallback_args`, `recovery`,
   `_meta.next_commands`). Catches any future path that drops the scaffold.

3. **No duplicated static string in a payload.** Assert a `predict_splicing` (and a
   batch over 2 variants) payload contains `THRESHOLD_BASIS` at most **once**.
   Generalises F13 so a re-introduced duplicate fails CI rather than an eval.

4. **No null leaf in `full` mode.** Walk a `response_mode="full"` payload and assert
   no leaf value is `None` (omit-when-null is the contract). Generalises F14 so the
   next sparse upstream field can't silently ship as `null`.

Pre-emptive single-model parity check (do during the F13/F15 work, not a separate
task): confirm `predict_spliceai` / `predict_pangolin` already carry
`molecular_consequence` (G2) and the F15 caveat the same way the combined tool
does — the §8.1 and §8.2 invariants will surface any gap, and closing it now denies
the next independent pass its "single-model is second-class" finding.

**Optional latency stretch (only if FastMCP 3.x exposes it cleanly):** on the
task-augmented path, set the CreateTaskResult `_meta`
`io.modelcontextprotocol/model-immediate-response` to a short "scoring started; poll
in ~Ns" string so an opted-in client gets an immediate, useful turn instead of a
bare task handle (MCP 2025-11-25 Tasks). Skip if it requires patching FastMCP
internals — `task=True` already removes the turn-blocking penalty; this only
polishes the latency dimension (8 → ~8.5) that is otherwise upstream-bound.

*Research use only; not for clinical decision support. Splice predictions are
computational and must be interpreted alongside orthogonal evidence.*
