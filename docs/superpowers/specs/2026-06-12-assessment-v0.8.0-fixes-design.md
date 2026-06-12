# Design — v0.9.0: resolving the v0.8.0 LLM-consumer assessment to >9.5/10

- **Date:** 2026-06-12
- **Source assessment:** `docs/mcp-assessment-v0.8.0-2026-06-12.md` (overall 8/10)
- **Target:** >9.5/10 overall; all six findings + Part 1 polish closed
- **Version:** 0.8.0 → **0.9.0** (breaking response-shape unification; `capabilities_version`
  content hash changes so warm clients re-discover)

## Context

The v0.8.0 assessment rates the server "mature, unusually well-engineered" (Discoverability 9,
Error handling 9, Safety 9, Observability 8, Token 8, Speed 7). It surfaces **one real
correctness/latency bug**, **two error-recovery dead-ends**, and **a handful of consistency /
token / polish nits**. None block production. This design closes every item with the smallest
cohesive change per finding, keeps tool names/semantics stable except for the one intentional
breaking shape unification, and aligns with current MCP guidance:

- Tool errors must carry **actionable, self-correcting feedback** in the result object
  (MCP tools spec; cyanheads server guide). Our two dead-end fallbacks violate the spirit —
  they hand back a next step that loops. F1/F2/F4 fix that.
- Rate-limited responses on non-HTTP transports should carry an explicit **retry-after**
  duration so agents back off rather than retry-storm (Zuplo "Never Ship an MCP Server Without
  a Rate Limit"; MintMCP). P1#2 adds `retry_after_s` + a proactive success-path budget.
- **SEP-1576** (token bloat): maintain *structural consistency* across responses and drop
  repeated static content. F3 (one shape per fact) and F6 (drop the repeated glossary) are
  direct applications. The lean-capabilities path already cites SEP-1576.
- Embed a **version string in the capability response** for breaking-change detection — already
  present as `capabilities_version`; the 0.9.0 hash change is the signal.

## Decisions (confirmed)

1. **F1 error code = `invalid_input`** (not `build_mismatch`). A position beyond *both* builds'
   chromosome lengths cannot score in any build, so a `build_mismatch` "switch build" recovery
   would loop. `build_mismatch` remains for the genuine case (in-range on the *other* build).
   The capabilities/reference docs are corrected to match.
2. **Clean break to 0.9.0.** The F3 shape unification removes divergent keys
   (`spliceai_max`/`pangolin_max`) rather than keeping aliases — aliases would re-introduce the
   redundancy this assessment penalizes.

## Findings → changes

### F1 [Bug, Medium] — out-of-range coordinate costs ~15s and returns `not_found`

**Where:** `mcp/build_check.py`, `mcp/tools/_common.py:prepare_variant`, `mcp/errors.py`.

`likely_build` returns a build only when `pos` is within exactly one build's range. For
`pos > max(g37, g38)` it returns `None`, so the coordinate falls through to a ~15s scoring call
that ends in `not_found`.

- Add `max_length(chrom) -> int | None` and `out_of_range(chrom, pos) -> tuple[int,int] | None`
  to `build_check.py` returning `(grch38_len, grch37_len)` when `pos` exceeds **both** standard
  builds for a standard contig (chr1-22, X, Y); `None` for MT / non-standard / in-range.
- New exception `CoordinateRangeError(ValueError)` carrying `chrom, pos, grch38_len, grch37_len`.
- In `prepare_variant`, for a `coordinate` input, after `_reject_unsupported_contig` and before
  `detect_build_mismatch`, raise `CoordinateRangeError` when `out_of_range` fires.
- `errors._classify`: `CoordinateRangeError` → `("invalid_input", False, "get_server_capabilities", None)`.
  Distinguish its recovery text from the parse-error case: "position N exceeds the length of
  chrC in all supported builds (GRCh38 …, GRCh37 …). Verify the coordinate; if you have an
  HGVS/rsID, resolve_variant can derive valid coordinates." (resolve_variant is **not** the
  structured fallback — it would re-emit the same bad id, the F2 lesson.)
- The exception message is developer-authored and safe to surface verbatim
  (`_envelope_message` already passes `invalid_input` through).

**Test:** `chr1-260000000-A-G` → `invalid_input`, <50ms, no upstream call (respx asserts zero
scoring requests); message names both lengths; fallback is `get_server_capabilities`.
In-range-other-build (`chr1-249000000-…` requested GRCh38, valid GRCh37) still → `build_mismatch`.

### F2 [UX] — `ref_mismatch` recovery re-suggests `resolve_variant` with the same wrong-REF coord

**Where:** `mcp/errors.py` (`_classify`, `mcp_tool_error`, `_recovery_text`).

`ref_mismatch` always implies a coordinate input (HGVS/rsID resolve through VEP and never reach
the REF check). So `resolve_variant` with the unchanged coordinate is a structural dead end.
Replace with an actionable fallback computed from the error:

- New `_ref_mismatch_fallback(exc: RefMismatchError, context) -> (tool, args)`:
  - **other-build** (`exc.other_build_hint` set): re-call the *same predict tool*
    (`context.tool_name` if a predict tool else `predict_splicing`) with
    `{variant: exc.variant_id, genome_build: <other build>}`.
  - **REF/ALT swap**: parse `variant_id`; when `len(ref)==len(alt)`, both ACGT, and
    `alt.upper() == reference_base.upper()`, re-call the same predict tool with the swapped
    `variant_id` `CHROM-POS-{alt}-{ref}`; append a swap sentence to `recovery`.
  - **else** (genuine wrong REF on a coordinate): `("get_server_capabilities", None)`.
- `RefMismatchError` gains an `alt` attribute (parsed at raise time in `_diagnose._ref_mismatch_error`)
  so swap detection needs no re-parse; `reference_base` is already stored.
- Prose `_recovery_text("ref_mismatch")` keeps "fix the REF, or pass an HGVS/rsID to
  resolve_variant" (passing a *different* form is valid) — only the structured fallback changes.

**Test:** `chr8-140300616-A-G` (wrong REF, no other-build) → fallback `get_server_capabilities`,
no `resolve_variant` echo. Swap case (ALT == ref base) → fallback predict tool with swapped id +
swap sentence. Other-build case → fallback predict tool with corrected `genome_build`.

### F3 [Consistency] — same fact, different field names/locations across response modes

**Where:** `mcp/shaping.py`, `mcp/tools/_predict_shape.py`, `mcp/tools/_predict.py`.

Stable contract: every prediction payload exposes the headline number under one name in one
place, in every mode.

- **Single model** (`shape_spliceai`/`shape_pangolin`): compute `top:{class,score,position}` once
  and attach it to the compact/full result (already present in minimal). `_minimal_single_model`
  reads the precomputed `top`. Result: `top` + `max_delta_score` present in all three modes.
- **Combined** (`minimal_combined`): emit `agreement:{verdict, spliceai_max_delta,
  pangolin_max_delta}` (matching compact/full) and **drop** the divergent top-level
  `spliceai_max`/`pangolin_max`. One name (`*_max_delta`), one location (`agreement{}`), all modes.

Breaking → 0.9.0. Update every affected unit test and the capabilities `response_mode_tiers` /
`response_fields` prose + the `v0_8_0_shape` note (renamed `v0_9_0_shape`).

### F4 [Cross-server hint] — gtex `see_also` passes a gene *symbol* into `gencode_id`

**Where:** `mcp/tools/_common.py` (`see_also_for`, `_see_also_full`), `_predict.py` telemetry,
`spliceai.py`/`pangolin.py`/`combined.py` callers.

- `see_also_for` and `_see_also_full` gain a `gene_id: str | None` param.
- gtex hint: when `gene_id` is known → `get_median_expression_levels({gencode_id:[gene_id]})`
  (versioned ENSG, what gtex expects); else → `search_gtex_genes({query: gene})` (symbol path).
- Thread `gene_id` from the shaped top transcript: combined adds `gene_id` to `_telemetry`
  (from the lifted `transcript` identity or the SpliceAI/Pangolin top block); single-model reads
  `shaped["transcripts"][0]["gene_id"]`.

**Test:** full-mode `see_also` for a symbol+id variant → gtex entry uses the ENSG id; symbol-only
variant → `search_gtex_genes`.

### F5 [Polish]

- **(a) lncRNA headline** (`shaping.py`, `_predict_shape.py`): `_gene_label(gene)` renders
  `f"{gene} (no gene symbol)"` when `gene` matches `^ENSG\d+`, else `gene` (or "variant"/
  "unknown gene" when falsy). Applied in `spliceai_headline`, `pangolin_headline`,
  `combined_headline`.
- **(b) batch per-item `request_id`** (`_batch_runner.py`): generate `uuid4().hex[:12]` per item;
  include in `_success_item` `_meta` and `_error_item`. Lets a slow/failed item correlate to logs.

### F6 [Token] — static `threshold_basis` glossary on every compact + full payload

**Where:** `mcp/shaping.py`, `mcp/tools/_predict_shape.py`, `_predict.py`.

Gate `interpretation.threshold_basis` behind `response_mode='full'` only. `interpretation.band`
stays in every mode. Single-model: only add the key when `mode == "full"`. Combined:
`combined_interpretation` keeps `band`; `predict_one` pops top-level `threshold_basis` when
`response_mode != "full"` (consistent with the existing F13 sub-block strip). It remains in
capabilities `interpretation_bands` + the `reference` resource.

### P1#1 — `capabilities_version` duplicated top-level and in `_meta`

**Where:** `mcp/errors.py:run_mcp_tool._stamp`.

The capabilities document carries `capabilities_version` at top level; `_stamp` also injects it
into `_meta`. On that one call it is redundant. Guard: `_stamp` adds the `_meta` copy only when
the envelope has no top-level `capabilities_version`. Prediction payloads (no top-level copy)
are unchanged.

### P1#2 — proactive rate-limit headroom (highest-value gap)

**Where:** `config.py`, `mcp/errors.py`, `mcp/tools/{_predict.py via callers, spliceai.py,
pangolin.py, combined.py}`, `resources.py`.

Honest framing: the cap is a **local `asyncio.Semaphore` (MAX_CONCURRENCY)**, not a tracked
time-window quota; the upstream is "several requests/min" but the server does not meter it.
So the proactive signal is a **soft client-pacing interval**, not a fabricated "remaining".

- New setting `RATE_BUDGET_MIN_INTERVAL_MS: int = 12000` (~5 cache-miss calls/min; conservative
  for "several/min"). Tunable via env.
- `errors.rate_budget_snapshot(*, saturated: bool) -> dict`: returns
  `{limit: MAX_CONCURRENCY, unit: "concurrent_requests", min_interval_ms: RATE_BUDGET_MIN_INTERVAL_MS}`;
  when `saturated` also `remaining: 0` and `retry_after_s` (≈ `min_interval_ms/1000`).
- **Success path:** prediction tools add `meta["rate_budget"] = rate_budget_snapshot(saturated=False)`.
  Small (3 keys); kept even on the lean/minimal path because autonomous burst-callers (minimal)
  are exactly who needs to pace. `get_server_capabilities`/`warmup`/`resolve_variant` do not add it
  (no upstream scoring spend).
- **Error path** (`rate_limited`): replace the inline dict with
  `rate_budget_snapshot(saturated=True)` so `remaining: 0` + `retry_after_s` appear (current MCP
  retry-after guidance). Batch per-item rate_limited already forwards `_meta.rate_budget`.
- Document `min_interval_ms`/`retry_after_s` semantics once in capabilities `concurrency.rate_budget`.

### P1#3 — sticky hint suppression / lifecycle

**Where:** `resources.py`.

Stateless server ⇒ no session memory; the honest fix is documentation. Add a `hint_lifecycle`
field under `response_fields`: "next_commands/see_also are designed to be read once. After your
first successful predict_* call in a session, set include_hints=false (and include_see_also=false)
for the remaining calls to cut per-call tokens — the workflow does not change within a session."
Cross-reference from the `include_hints` prose.

## Architecture / isolation notes

- No new modules required; changes are localized and respect the 600-LOC budget. `errors.py`
  (476) is the closest to the cap — F1/F2/P1#2 add a small helper (`rate_budget_snapshot`) and a
  `_ref_mismatch_fallback`; net add stays well under 600. If it would cross ~580, extract a tiny
  `mcp/rate_budget.py` (single responsibility: the snapshot + interval constant) — preferred over
  growing `errors.py`.
- `build_check.py` (102) absorbs F1 cleanly (length tables already live there).
- `see_also` gains one param; all three predict call sites updated; signature stays cohesive.

## Out of scope (consistent with the assessment's own caveats)

- A genuine liftover-backed `build_mismatch` repro and live background-`task` E2E (the assessment
  lists these as *follow-ups*, not findings). The F1 fix makes the out-of-range path correct;
  E2E task verification stays a manual/integration concern.
- `warm_ttl_remaining_s` (already declined in v0.8.0; Cloud Run warmth is unobservable server-side).
- Touching the upstream's intrinsic latency (the Speed floor) — we improve only what the server
  controls (the F1 fast-fail removes one ~15s wasted call; proactive pacing prevents retry-storms).

## Verification

- Unit tests for every finding (deterministic, respx-mocked; no live upstream): F1 zero-upstream
  fast-fail; F2 three fallback branches; F3 cross-mode key presence/naming in all three modes for
  single + combined; F4 gtex id vs symbol; F5a/b; F6 threshold_basis only in full; P1#1 no `_meta`
  dup on capabilities; P1#2 success `rate_budget` shape + error `retry_after_s`.
- `make ci-local` (format, ruff, lint-loc ≤600, mypy, unit tests) green before handoff.
- Bump `__init__.py` + `pyproject.toml` to 0.9.0; capabilities_version hash recomputes; add a
  `docs/mcp-assessment-v0.8.0-2026-06-12-resolution.md` mapping each finding to its fix; update
  `README.md`/`docs/API.md` where response shape or version is referenced.

## Intended score impact

Error handling 9→9.5+ (both dead-ends + the out-of-range bug fixed), Token 8→9.5+ (F6 + P1#1 +
F3 dedup), Observability 8→9.5+ (proactive rate_budget + retry_after + per-item request_id),
Consistency nits closed (F3/F4/F5), Speed 7→~8 on the affected path (F1 removes a ~15s wasted
call; pacing prevents storms). Discoverability/Safety hold at 9–10. Overall **>9.5/10**.
