# spliceailookup-link — Consumer-Assessment Fixes (Design)

**Date:** 2026-06-12
**Author:** MCP engineering (driven by `docs/mcp-consumer-assessment-2026-06-12.md`)
**Status:** Design — pending implementation
**Baseline:** v0.6.0 (protocol 2025-11-25) → target **v0.7.0**

## 1. Why

The 2026-06-12 LLM-consumer assessment of the live v0.6.0 server scored the
single/dual prediction + resolver tools 9–9.5/10 but found `predict_splicing_batch`
dragging the whole server to ~6.5. It enumerated one HIGH, one MEDIUM, two LOW,
and two INFO findings. This pass closes all of them so the server lands cleanly
**>9.5/10**. Every change is additive and backward-compatible (no tool renames,
no removed fields); the contract grows, it does not break.

Best-practice grounding (web research, 2026): native, explicit error codes per
failure mode let agents decide retry-vs-pivot deterministically; tools should fail
fast and cheap rather than burn a slow upstream slot; per-result payloads should be
high-signal with opt-out controls for token-sensitive callers. These principles
shape the fixes below.

## 2. Findings → changes (F-numbering continues the eval-fix convention, F18+)

| F | Sev | Assessment finding | Change |
|---|-----|--------------------|--------|
| F18 | HIGH | Batch self-saturates the 2-slot cap, misclassifies `rate_limited` vs `upstream_unavailable`, never retries, and doesn't split terminal vs retryable failures | Resilient internal batch runner: retry-once for retryable items, summary splits `terminal_failed`/`retryable_failed`, top-level `retry_variants`, `rate_budget` on per-item `rate_limited` |
| F19 | MED | MT / non-standard contigs fail slow (45 s 503) instead of fast | New non-retryable `unsupported_contig` error code; `prepare_variant` rejects non-nuclear contigs in <1 ms (covers single **and** batch; removes the F18 trigger) |
| F20 | LOW | GRCh37 GENCODE IDs carry a `_NN` re-version suffix (`ENSG...13_12`) that breaks cross-build joins | Normalize `gene_id`/`transcript_id` in shaping; preserve the raw value as `gencode_id` in `full` mode; document in glossary |
| F21 | LOW | `resolve_variant` `invalid_input` recovery prose says "call resolve_variant" (circular) | Make `_recovery_text` tool-aware; resolve-variant prose points at formats + `get_server_capabilities` |
| F22 | INFO | Standalone calls always repeat `next_commands` + `see_also` (the `_meta` tax) | `include_hints: bool = True` on standalone tools; `false` drops both blocks |
| F23 | INFO | (folded into F18) per-item batch `rate_limited` lacks the advertised `rate_budget` | Attach `rate_budget` to per-item retryable-`rate_limited` envelopes |
| F24 | — | Contract drift | Capabilities/reference doc updates + version bump 0.6.0 → 0.7.0 |

## 3. F18 — Resilient batch runner (the headline fix)

### Mechanism (grounded in code)
`base_client.BaseHTTPClient` enforces a global `asyncio.Semaphore(MAX_CONCURRENCY=2)`;
an acquire that waits past `QUEUE_WAIT_TIMEOUT=30 s` raises `RateLimitedError` →
`rate_limited`. Inside `predict_one`, each item fires
`asyncio.gather(score(spliceai), score(pangolin))` = exactly 2 concurrent slots.
A slow MT item (45 s) holds/contends slots and starves siblings; whichever loses
the queue race is reported as the *symptom* (`rate_limited`) instead of the *root
cause* (upstream 503).

### Design decision — concurrency model
With a 2-slot upstream cap and 2 calls per item, **any** item-level parallelism
oversubscribes the cap and re-creates the contention the assessment flagged. The
correct model at cap=2 is therefore **one item in flight at a time** (each item
already maxes the cap with its two model calls) — which is precisely "queue items
through the concurrency cap so valid items never `rate_limited` each other." The
runner is written to run `ceil(MAX_CONCURRENCY / 2)` items concurrently, which is
1 today and scales automatically if the cap is ever raised. No upstream-policy
violation, no contention by construction.

### Runner contract
New module `spliceailookup_link/mcp/tools/_batch_runner.py` exposing
`async def run_batch(service, *, variants, params, ctx, retry_backoff_s) -> dict`.
`batch.py` becomes the thin tool wrapper (registration + schema), keeping both
files well under the 600-LOC budget.

Per item:
1. `await predict_one(...)` (cheap terminal failures — `invalid_input`,
   `build_mismatch`, `ref_mismatch`, `ambiguous`, `unsupported_contig` — already
   raise *before* any scoring call, so they cost no slot).
2. On exception, classify via the existing error layer
   (`mcp_tool_error(...).payload`) → `(error_code, retryable)`.
3. If `retryable` (`rate_limited` / `upstream_unavailable`) and not yet retried
   and within the batch budget: short jittered backoff (`retry_backoff_s`,
   default ~1 s, **0 in tests**), retry **once**.
4. Final classification:
   - success → `ok`
   - terminal failure → per-item error envelope, counted in `terminal_failed`
   - retryable failure (after the one retry) → per-item error envelope **with
     `rate_budget` when `rate_limited`**, counted in `retryable_failed`, and the
     variant string appended to `retry_variants`.

Reuse the existing per-item envelope shape from `batch.py` (which already routes
the fallback through `resolve_variant`, giving parity with single-call errors).

### Summary shape (additive)
```jsonc
"summary": {
  "ok": 4, "failed": 1,
  "terminal_failed": 0,        // NEW — invalid_input/ref_mismatch/build_mismatch/ambiguous/unsupported_contig/not_found
  "retryable_failed": 1,       // NEW — rate_limited/upstream_unavailable after one in-batch retry
  "retried": 1,                // NEW — how many items were auto-retried
  ...verdict_counts
}
// top-level, only when non-empty:
"retry_variants": ["MT-3243-A-G"]   // NEW — resubmit these (e.g. as a background task)
```
`failed == terminal_failed + retryable_failed` (invariant asserted in tests).

### Background-task path unchanged
`running_as_task(ctx)` still bypasses the per-item soft deadline (`enforce_item_deadline`)
so large/comprehensive panels run unbounded under a task. The retry layer also
applies in task mode.

## 4. F19 — Fast-fail unsupported contigs

**Fact:** SpliceAI and Pangolin are trained on nuclear chromosomes (chr1-22, X, Y)
only; MT and non-standard contigs are out of model scope (confirmed: Pangolin
training set is chr2,4,6,8,10–22,X,Y; SpliceAI likewise nuclear). `variant.py`
currently lists `M`/`MT` in `_VALID_CHROMS`, so an MT coordinate parses and is sent
upstream, burning a ~45 s slot before a 503.

**Change:**
- Add `SCORING_CONTIGS = {"1".."22", "X", "Y"}` and
  `unsupported_contig_reason(variant_id) -> str | None` in `variant.py`.
- New exception `UnsupportedContigError(VariantParseError)` carries the precise
  message. `errors._classify` gains an `isinstance(exc, UnsupportedContigError)`
  branch **before** the `VariantParseError`/`UpstreamInputError → invalid_input`
  branch (subclass-first ordering, mirroring the existing
  DataNotFoundError-before-SpliceApiError pattern), mapping it to a new
  non-retryable `unsupported_contig` code with recovery prose:
  > "Mitochondrial / non-standard contig is not supported by the SpliceAI/Pangolin
  > models (nuclear chr1-22, X, Y only). For mitochondrial variants, use gnomad-link
  > `get_mitochondrial_variant`. Do not retry unchanged."
- `prepare_variant` (in `_common.py`, used by `predict_one` → both single and batch)
  raises `UnsupportedContigError` for non-nuclear contigs **before** any scoring
  call. Result: <1 ms fast-fail for `predict_*` and per-item in batch; no slot
  consumed; removes the F18 starvation trigger.
- `resolve_variant` keeps normalizing MT coordinates (it never scores), but its
  result gains a `scoring_supported: false` + short note for non-nuclear contigs so
  an agent learns the limitation at resolve time. `_VALID_CHROMS` stays permissive
  (parsing is separate from scorability).

**Why a dedicated code, not `invalid_input`:** the assessment accepted
`invalid_input`/`not_found`, but a distinct `unsupported_contig` is more honest
(the input is well-formed, just out of model scope), gives an unambiguous
non-retryable signal, and carries a *useful* cross-server redirect — aligning with
the 2026 best practice of explicit per-failure-mode codes. `next_commands` stays
on-server (`get_server_capabilities`); the gnomad redirect lives in `recovery`
prose (cross-server pointers are hints, never callable `next_commands`).

## 5. F20 — Normalize GENCODE `_NN` IDs

**Where:** `shaping.py` `_shape_spliceai_transcript` / `_shape_pangolin_transcript`.

Add `_normalize_ensembl_id(value) -> str`: strip a trailing `_\d+` that follows a
versioned Ensembl id (regex `^(ENS[A-Z]+\d+\.\d+)_\d+$` → group 1); leave clean
GRCh38 ids and any non-matching value untouched. Apply to `gene_id` and
`transcript_id`. In `full` mode only, when normalization changed the value, keep the
raw under `gencode_id` (lossless for power users, zero token cost in compact/minimal).
Document the normalization in the field glossary.

## 6. F21 — De-circularize resolve_variant recovery

**Where:** `errors.py`. `_recovery_text(error_code, fallback_tool)` becomes
`_recovery_text(error_code, fallback_tool, *, tool_name)`. For
`error_code == "invalid_input"` **and** `tool_name == "resolve_variant"`, emit
non-circular prose:
> "The input could not be parsed into any supported variant form. Do not retry
> unchanged. Provide CHROM-POS-REF-ALT, transcript/genomic HGVS, or an rsID
> (call get_server_capabilities for accepted formats and examples)."

All other paths keep today's prose (the prediction-tool `invalid_input` text that
points at `resolve_variant` remains correct there). `fallback_tool` already
correctly points to `get_server_capabilities` for resolve_variant — only the prose
was wrong.

## 7. F22 — `include_hints` opt-out (token efficiency)

Add `include_hints: Annotated[bool, Field(...)] = True` to `predict_splicing`,
`predict_spliceai`, `predict_pangolin`, and `resolve_variant`. When `false`, the
tool omits both `_meta.next_commands` and `_meta.see_also`. Default `true` preserves
the current discoverability/chaining behavior; token-sensitive callers (or warm
agents that already know the workflow) opt out. This matches the batch tool, which
already drops these per item. `minimal` mode continues to omit `see_also`
regardless. `unsafe_for_clinical_use` and `capabilities_version` are **retained**
on every envelope (safety mandate from AGENTS.md + drift detection outweigh their
tiny cost; they were never the dominant `_meta` tax — `next_commands`/`see_also`
were).

## 8. F23 — `rate_budget` on per-item batch errors

Folded into the F18 runner: when a per-item final failure is `rate_limited`, copy
`_meta.rate_budget` (`{limit, remaining, unit:'concurrent_requests'}`) from the
classified envelope onto the per-item dict, matching the contract the capabilities
doc advertises.

## 9. F24 — Docs + version

- `resources.py` capabilities/reference:
  - add `unsupported_contig` to `error_codes` and the reference `codes` taxonomy;
  - document the batch `retry_variants` + split summary under a new
    `batch_semantics` note;
  - document `include_hints`;
  - glossary note on GRCh37 GENCODE `_NN` normalization (+ `gencode_id` in full mode);
  - note `scoring_supported` on resolve for non-nuclear contigs.
- `pyproject.toml` + `__init__.py`: 0.6.0 → 0.7.0.
- `capabilities_version` hash will shift (expected); pinned tests assert it is
  *stable + 12-char + echoed*, not a literal value, so they keep passing.

## 10. Module map (600-LOC budget)

| Module | now | after | note |
|---|---|---|---|
| `mcp/tools/batch.py` | 161 | ~110 | thinned: delegates to runner |
| `mcp/tools/_batch_runner.py` | — | ~140 (new) | loop + retry + classify + summary |
| `mcp/tools/_common.py` | 179 | ~200 | unsupported-contig guard in `prepare_variant` |
| `variant.py` | 134 | ~165 | `SCORING_CONTIGS` + reason helper + exception |
| `mcp/errors.py` | 436 | ~470 | `unsupported_contig` code + tool-aware recovery |
| `mcp/shaping.py` | 418 | ~445 | `_normalize_ensembl_id` + `gencode_id` (watch the cap) |
| `mcp/tools/combined.py` / `spliceai.py` / `pangolin.py` / `resolve.py` | — | +small | `include_hints` param |
| `mcp/resources.py` | 412 | ~445 | doc updates |

`shaping.py` is the tightest (418 → ~445); if it would cross 600 the ID normalizer
moves to a 1-function helper in `_predict_shape.py`. `errors.py` (436 → ~470) stays
under budget. `make lint-loc` is the gate.

## 11. Testing (respx-mocked / stubbed; deterministic, no live calls)

New `tests/unit/test_eval_fixes_4.py` (F18–F24), plus targeted additions to
`test_batch.py`, `test_variant_parse.py`, `test_shaping.py`, `test_errors.py`,
`test_tools.py`:

- **F18:** a stub item that raises `RateLimitedError` once then succeeds ends `ok`
  with `retried==1`; an item that always raises `upstream_unavailable` lands in
  `retryable_failed` + `retry_variants` (not `terminal_failed`); a terminal
  (`invalid_input`) item lands in `terminal_failed`, never retried; invariant
  `failed == terminal_failed + retryable_failed`; sibling valid items are never
  misclassified as `rate_limited`. `retry_backoff_s=0` in tests.
- **F19:** `MT-3243-A-G` → `predict_*` returns `unsupported_contig` with **no**
  scoring call made (assert the stub scorer was not invoked); same item in a batch
  is a per-item `unsupported_contig` and consumes no slot; `1-…-…-…` is unaffected.
- **F20:** a GRCh37 fixture transcript with `g_id=ENSG…13_12`, `t_id=ENST…9_9`
  yields normalized `gene_id`/`transcript_id`; `full` mode carries `gencode_id`
  with the raw value; GRCh38 clean ids are untouched.
- **F21:** `resolve_variant("not a variant")` recovery prose does **not** contain
  "call resolve_variant"; a prediction-tool `invalid_input` still does.
- **F22:** `include_hints=false` drops `next_commands` + `see_also`; default keeps
  both; `minimal` still omits `see_also`.
- **F23:** per-item batch `rate_limited` carries `_meta`-style `rate_budget`.
- **F24:** capabilities lists `unsupported_contig`; documents `retry_variants`,
  `include_hints`, GENCODE normalization; `capabilities_version` stable + 12-char.
- `make ci-local` green; coverage ≥ 80%; every module < 600 LOC.

## 12. Acceptance

- `make ci-local` green; all F18–F24 tests pass; LOC budget respected.
- A mixed batch with a slow/failing item returns valid siblings unharmed, splits
  terminal vs retryable failures, and emits `retry_variants`.
- `MT-3243-A-G` fails fast (<1 s) as `unsupported_contig`, pointing at gnomad-link,
  in both single and batch contexts — no 45 s slot burn.
- GRCh37 and GRCh38 predictions return join-compatible normalized Ensembl IDs.
- `resolve_variant` invalid-input recovery is non-circular.
- `include_hints=false` measurably trims standalone `_meta`.
- Capabilities documents every new behavior; version is 0.7.0.

*Research use only; not for clinical decision support. Splice predictions are
computational and must be interpreted alongside orthogonal evidence.*
