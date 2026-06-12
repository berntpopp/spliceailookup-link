# Design: spliceailookup-link v0.7.0 assessment fixes (target > 9.5 / 10)

**Date:** 2026-06-12
**Source assessment:** `docs/mcp-assessment-v0.7.0-2026-06-12.md` (scored 8.5 / 10)
**Status:** approved-to-execute (autonomous end-to-end per /goal directive)

## Goal

Close every defect and consumer recommendation in the v0.7.0 evaluation so a
re-run scores **> 9.5 / 10**, without breaking MCP tool names, response schemas,
the 600-LOC/file budget, or the research-use safety posture.

## Scope (what the assessment asked for)

| Item | Assessment | Severity | This design |
|---|---|---|---|
| **D1** | `build_mismatch` misclassifies a wrong-REF as wrong-build, producing a dead-end redirect | Medium | Fixed by the pre-flight ref-base check + removing the ref-base→build_mismatch branch |
| **D2** | `ref_mismatch` costs ~17s (check runs after upstream dispatch) | Medium | Fixed by moving the ref-base check **before** scoring |
| **D3** | `ambiguous` resolve returns `success:true` + a populated singular `variant_id` (first allele) | Low–Med | `variant_id=null` when ambiguous |
| **D4** | `_meta` not trimmed on minimal/hints-off paths | Low | Lean `_meta` (drop redundant provenance), keep the safety flag |
| **D5** | `transcript_info.tx_start/tx_end` null in full mode | Low | Derive from the exon model |
| **C3** | batch has no stated size/truncation contract | — | Document the hard `max_items=25` reject-beyond contract; self-describe in the envelope |
| **C4** | no "was this warm?" signal | — | `served_warm` boolean in every `_meta` |
| **C5** | resource URIs absent from lean capabilities | — | Add `resources` to the lean doc |
| **Rec #5** | no coverage for comprehensive-503 and rate_limited budget | — | Add deterministic (respx-mocked) unit tests |

Out of scope: eliminating genuine **upstream** cold-start latency for *valid*
variants (13–40s is Cloud-Run-bound; already mitigated by cache + warmup +
background tasks + the new `served_warm` signal). We fix the *failure-path*
latency (D2) and give clients a cheap warm/cold signal to choose blocking vs
background.

## Key architectural insight: D1 and D2 are one fix

The server already owns a cheap, cached Ensembl reference-base check
(`SpliceService.reference_base` → `EnsemblVepClient.reference_base`, ~100–300ms,
cached 24h). Today it runs **only after** both models fail (`predict_one` /
`predict_*` call `diagnose_coordinate_failure` on the `not_found` path) — i.e.
after the ~17s scoring round-trip. That single ordering choice causes **both**
defects:

- **D2 (latency):** the 17s is the scoring call; the ref check itself is cheap.
- **D1 (misclassification):** `diagnose_coordinate_failure` raises
  `build_mismatch` whenever the typo'd REF *coincidentally* matches the **other**
  build's base at that coordinate (`other_base == ref`), even though the
  requested-build coordinate is itself valid and scorable. The redirect to the
  other build then dead-ends at `not_found` (the REF is a typo, not a real
  other-build variant).

De-risking fact (verified): the concurrency semaphore lives in
`BaseHTTPClient`, and `ScoringClient` and `EnsemblVepClient` are **separate
instances with separate semaphores**. A pre-flight Ensembl call therefore does
**not** consume the scarce scoring budget (`MAX_CONCURRENCY=2`).

### The corrected model of the two error classes

- **`build_mismatch`** ⟺ the coordinate **does not fit the requested build**.
  Signals (both unambiguous, never a coincidence):
  1. position out of the requested build's chromosome range
     (`detect_build_mismatch`, offline, deterministic — already in
     `prepare_variant`); or
  2. the variant genuinely **scores on the other build**
     (`cross_build_probe`, the existing expensive post-dispatch fallback — only
     fires when the redirect is guaranteed productive).
- **`ref_mismatch`** ⟺ the position **fits** the requested build but the REF
  base is wrong there. This is the *common* error (swapped REF/ALT, wrong
  strand, a typo) and its recovery (`resolve_variant`) is **always** productive.

The bug was that a third, unreliable signal — "the other build's reference base
equals the typed REF" — was mapped to `build_mismatch`. We **delete that
mapping**. Reference-base comparison now yields only `ref_mismatch` (or "genuine
not_found"), never `build_mismatch`.

### Pre-flight ref check (new, in `prepare_variant`)

For coordinate inputs, after the existing offline `detect_build_mismatch`:

1. Parse `chrom, pos, ref, alt` (`split_variant_id`). Skip non-ACGT / N /
   symbolic / multi-base-with-N REFs (only clean `^[ACGT]+$`).
2. `requested_base = await service.reference_base(chrom, pos, len(ref), build)`.
   - `None` (Ensembl unavailable / no sequence): **proceed to score** — never
     regress. The post-dispatch `diagnose_coordinate_failure` remains the safety
     net.
   - `requested_base == ref`: proceed to score (genuine variant; may legitimately
     be `not_found` if no transcript overlap).
   - `requested_base != ref`: raise **`RefMismatchError`** (fast, < 1s). To
     enrich recovery, do one more cheap cached lookup on the other build; if it
     matches the typed REF, attach a **secondary** hint
     (`other_build_hint = {build, note}`) — *mentioned*, never a redirect.
3. Gating: the pre-flight check runs when `cross_build_check` is true (the
   existing per-call lever, default true) **and** a new env flag
   `PREFLIGHT_REF_CHECK_ENABLED` (default true) is set. `cross_build_check=false`
   continues to mean "just score it" (no pre-flight, no post-dispatch
   diagnosis). No new **tool** parameter — the input schema is unchanged.

`prepare_variant` gains a `cross_build_check: bool = True` argument, threaded
from the four call sites (`predict_spliceai`, `predict_pangolin`, `predict_one`).

### Post-dispatch `diagnose_coordinate_failure` (simplified, D1)

Remove the `other_base == ref → BuildMismatchError` branch. After scoring
returns `not_found` (and `detect_build_mismatch` already returned None, so the
position is in-range), a REF that does not match the requested build is a
`ref_mismatch`, full stop (with the same optional secondary other-build hint).
`build_mismatch` from this path comes only via `_probe_fallback`
(`cross_build_probe`, which confirms the variant *scores* on the other build).
The pre-flight check makes this path rare (Ensembl-was-down case), but keeping it
consistent prevents divergence. Shared ref-check logic is factored into one
helper used by both pre-flight and post-dispatch.

### `RefMismatchError` change

Add an optional `other_build_hint: dict | None`. `_recovery_text("ref_mismatch")`
appends, when present: *"The REF matches the {other} reference here; if you
intended {other}, re-run with genome_build={other}, or call resolve_variant for
canonical coordinates."* `fallback_args` stays pointed at `resolve_variant`
(always productive) — we do **not** redirect to the other build.

## D3 — ambiguous resolve consistency

`resolve_variant` returns the resolver result verbatim, including
`variant_id=candidates[0]` when ambiguous. Fix in `resolve.py` (tool layer, so
`prepare_variant`'s `AmbiguousVariantError` path for predict_* is untouched):
when `result.get("ambiguous")`, set `result["variant_id"] = None`. Keep
`ambiguous=true`, `variant_ids[]`, the `note`, and the per-allele
`next_commands`. `success` stays `true` — resolution genuinely succeeded at the
locus and enumerated the alleles; nulling the singular id closes the actual
foot-gun (an agent reading `.variant_id` now gets `null` and must consult
`variant_ids[]`). Output schema: `variant_id` → `{"type": ["string", "null"]}`,
still required (present, possibly null).

Rationale for null over a full error envelope: resolve_variant's *purpose* is to
surface candidates; `success:false` would read as "resolution failed." predict_*
/ batch still error (they cannot predict without one allele) — a coherent split
("resolver enumerates; predictors require one"), not the bug the assessment
flagged (the bug was the *populated* singular id).

## D4 + C4 — lean `_meta` + `served_warm`

**Lean trigger:** `lean_meta = (response_mode == "minimal") or (not
include_hints)` — any explicit "trim my tokens" signal.

**Lean `_meta` (kept):** `request_id`, `timing.elapsed_ms`, `cache`,
`served_warm`, `unsafe_for_clinical_use`.
**Lean `_meta` (dropped):** `capabilities_version`, `cache_ttl_s`,
`cache_age_s`, `upstream_elapsed_ms`, `next_commands`, `see_also`,
`resolved_from`.

**Deviation from the literal D4 text, by policy:** AGENTS.md requires the
research-use disclaimer on *every* payload, and the minimal body
(`_minimal_single_model`, `minimal_combined`) carries no disclaimer of its own —
the only marker is `_meta.unsafe_for_clinical_use`. That one tiny flag is
therefore **kept** in lean mode; we instead strip the genuinely
redundant/bulky provenance (`capabilities_version` ≈ 35 chars, plus the cache
TTL/age fields). This satisfies both the safety rule (instruction priority:
user/AGENTS.md > assessment) and the token-saving intent.

**`capabilities_version` "only on change":** a stateless server cannot track per
client what was last seen. Pragmatic interpretation: emit it in compact/full
`_meta`; omit in lean. Clients fetch it from `get_server_capabilities` when
needed. Documented as such.

**Mechanism:** `run_mcp_tool` gains `lean_meta: bool = False`; its `_stamp`
closure adds `capabilities_version` only when not lean (and always adds
`unsafe_for_clinical_use`). Each tool body computes `lean_meta` and (a) passes it
to `run_mcp_tool`, (b) skips the bulky telemetry fields when lean. The
validation-error wrapper keeps full provenance (validation errors are rare and
not on the high-volume path).

**`served_warm` (C4):** added in **all** modes (one bool, decision-critical).
- single model: `served_warm = tele.cache == "hit" or (tele.upstream_elapsed_ms
  is not None and tele.upstream_elapsed_ms < WARM_THRESHOLD_MS)`.
- combined / batch item: warm only if **every** sub-call was warm (a partial
  with a slow miss is not warm); computed from the aggregated telemetry.
New config `WARM_THRESHOLD_MS: int = 5000` (cold-start floor is ~13s; warm calls
are sub-second, so 5s cleanly separates them). `CallTelemetry` gains a
`served_warm(threshold)` helper. Documented in capabilities `observability`.

## D5 — tx_start / tx_end

In `shaping.py`:
- `_shape_spliceai_transcript` (full mode `exon_model`): add
  `tx_start = min(EXON_STARTS)`, `tx_end = max(EXON_ENDS)` (None when arrays
  absent) — robust to strand/order.
- `_shape_consequence` (full mode `transcript_info`): when `transcript_info` is
  present and its `tx_start`/`tx_end` are null/absent, fill them from the
  consequence transcript's exon arrays. Source: the MANE/top scored transcript in
  `payload["scores"]` (t_priority MS/MP, else `scores[0]`) via a small
  `_tx_bounds(scores)` helper. Never overwrite a non-null upstream value.

## C3 — batch size contract

The cap already exists as a **hard** Pydantic `max_length=_MAX_BATCH (25)` →
`>25` returns `validation_failed` (not silent truncation). Gap: it is not
documented as a contract and the tool description says "up to ~25x" without the
limit's behavior. Changes:
- `batch_semantics` (capabilities) + `reference` resource: state
  `max_items=25`, ">25 → validation_failed (not truncated)", and the per-item
  output bound ("each item ≈ one compact predict_splicing result").
- batch envelope self-describes: add `max_items` and `items_submitted` to the
  batch `_meta` (and keep `count`). No behavior change, just a discoverable,
  honest contract (mirrors gnomad-link's explicit truncation semantics, adapted
  to an input cap).
- Tool docstring: "1–25 variants; >25 → validation_failed."

## C5 — resources in lean capabilities

`_lean_capabilities` gains `"resources": full["resources"]` (the 5 URIs). Cheap,
directly addresses "list resource URIs in the lean output," and removes the need
to chase the buried `params_by_reference` note.

## Rec #5 — deterministic coverage (no live calls)

Add respx-mocked unit tests (keep live calls out of default CI per AGENTS.md):
- comprehensive `gene_set` upstream **503** → `upstream_unavailable` (retryable),
  via mocked 5xx.
- `rate_limited` → assert `_meta.rate_budget = {limit, remaining:0,
  unit:'concurrent_requests'}` shape and per-item `rate_budget` in batch.

## Files touched (all within 600-LOC budget)

- `config.py` — `WARM_THRESHOLD_MS`, `PREFLIGHT_REF_CHECK_ENABLED`.
- `services/telemetry.py` — `served_warm` helper.
- `api/ensembl_client.py` — (no change; `reference_base` already present).
- `mcp/tools/_common.py` — pre-flight ref check in `prepare_variant`
  (+ `cross_build_check` arg); shared ref-check helper.
- `mcp/tools/_diagnose.py` — remove ref-base→build_mismatch branch; use shared
  helper; secondary hint.
- `mcp/errors.py` — `RefMismatchError.other_build_hint`; `ref_mismatch` recovery
  text; `run_mcp_tool(lean_meta=...)` + `_stamp` provenance gating. (Currently
  460 LOC — watch the budget; additions are small.)
- `mcp/shaping.py` — D5 tx bounds (currently 446 LOC).
- `mcp/tools/spliceai.py`, `pangolin.py`, `combined.py` — `served_warm`,
  `lean_meta` gating.
- `mcp/tools/_predict.py` — aggregate `served_warm`/warm telemetry.
- `mcp/tools/_batch_runner.py`, `batch.py` — per-item `served_warm`,
  `items_submitted`/`max_items`, lean per-item meta.
- `mcp/tools/resolve.py` — D3 null id.
- `mcp/resources.py` — lean `resources` (C5), `batch_semantics` (C3),
  `observability`/`ref_mismatch`/`build_mismatch` wording, `served_warm` doc
  (currently 437 LOC — watch the budget).
- `docs/API.md` — reflect `served_warm`, lean `_meta`, ref/build semantics, batch
  cap.
- `tests/unit/` — new/extended tests for every item above.

## Testing strategy (TDD)

Unit, deterministic, respx-mocked. Per defect: a failing test that reproduces the
assessment behavior, then the fix.
- **D1/D2:** `prepare_variant` with a wrong-REF coordinate (Ensembl mock returns
  the true base) raises `ref_mismatch` *before* any scoring mock is hit (assert
  the scoring respx route is **not** called → proves pre-flight + speed). A
  wrong-REF that matches the other build still yields `ref_mismatch` (with
  `other_build_hint`), **not** `build_mismatch` (the D1 regression test, using
  the exact `chr8-140300616-C-A` case). A genuine out-of-range position still
  yields `build_mismatch`. Ensembl-unavailable → falls through to scoring
  (no regression).
- **D3:** ambiguous resolve → `variant_id is None`, `ambiguous` true,
  `variant_ids` populated, schema accepts null.
- **D4:** minimal/hints-off `_meta` excludes `capabilities_version`,
  `cache_ttl_s`, `cache_age_s`; includes `request_id`, `timing`, `cache`,
  `served_warm`, `unsafe_for_clinical_use`. compact/full still include the full
  provenance.
- **C4:** cache hit → `served_warm true`; slow miss (mock latency unavailable in
  unit, so assert from cache state + threshold logic via the telemetry helper);
  combined partial-with-miss → not warm.
- **D5:** full-mode `consequence.transcript_info.tx_start/tx_end` populated from
  exon arrays; `exon_model.tx_start/tx_end` present; non-null upstream values
  preserved.
- **C3:** 26 variants → `validation_failed`; envelope carries `max_items`,
  `items_submitted`.
- **C5:** lean capabilities includes `resources`.
- **Rec #5:** 503 → `upstream_unavailable`; rate_budget shape.

## Success criteria

`make ci-local` green (format, lint, lint-loc, mypy, tests). Every D1–D5 + C3–C5
+ Rec-#5 item has a passing test asserting the new behavior, and a regression
test for the exact assessment reproduction where one exists (notably the D1
`chr8-140300616-C-A` case and the D2 "no scoring call on wrong REF" assertion).
No tool name or response-schema break beyond the documented additive `_meta`
fields and the `resolve_variant` `variant_id` nullability.
