# spliceailookup-link — Evaluation-Driven Improvements, Pass 2 (Design)

**Date:** 2026-06-11
**Author:** MCP engineering (driven by `docs/mcp-evaluation.md` Part 4)
**Status:** Design — pending implementation plan
**Baseline:** v0.2.0 · MCP protocol 2025-11-25 · FastMCP 3.x
**Target version:** v0.3.0

## 1. Goal

`docs/mcp-evaluation.md` Part 3 (the maintainer's own v0.2.0 self-report) claimed
both axes had reached ~9.1–9.2. Part 4 — an **independent, black-box re-test of
the deployed v0.2.0** — did not reproduce that: it scored the LLM-consumer
experience at **8.5/10** and the senior-tester suite at **8.0/10**, and surfaced
**five new findings (F6–F10)**, one of them HIGH-severity (F6). The earlier
F1–F5 fixes all held up live; the regression is new.

This pass closes F6–F10 and the load-bearing consumer asks from Part 4a so that a
*fresh* independent re-evaluation clears **>9/10 on both axes**, without violating
the project's load-bearing constraints: thin FastAPI host (`/health` only), MCP
facade is the product, aggressive caching, conservative concurrency
(`MAX_CONCURRENCY` low), 600-LOC/file budget, research-use-only framing, and the
upstream contract (HTTP-200-with-`error`, 30s+ calls).

### Score model (what we are moving)

Senior-tester axis (per-tool; Part 4b). The tester score is dominated by one bug:

| Tool | Now | Target | Lever |
|---|---|---|---|
| `predict_splicing` | 6 | 9 | **F6** headline/verdict single source of truth |
| `predict_splicing_batch` | 7 | 9 | **F6** + **F10** summary/next_commands |
| `predict_spliceai` | 8.5 | 9 | **F7** transcript collapse |
| `get_server_capabilities` | 9 | 9.5 | **#2** background-exec advertised + hash |
| `resolve_variant` | 9 | 9 | unchanged |
| `predict_pangolin` | 9 | 9 | unchanged (F7 applies, already clean) |
| `warmup` | 9 | 9 | unchanged |

Projected tester mean: (9.5+9+9+9+9+9+9)/7 ≈ **9.07**.

LLM-consumer axis (per-dimension; Part 4a):

| Dimension | Now | Target | Lever |
|---|---|---|---|
| Token efficiency | 8 | 9 | **F7** dedup + **F8** real `minimal` |
| Speed / latency | 7 | 8.5 | **#2** background tasks made discoverable/usable |
| Observability | 9 | 9.5 | **#5** `cache_age_s`/`cache_ttl_s` + **F9** stamp |
| Schema ergonomics | 9 | 9.5 | **#4** `interpretation` band |
| Composability | 9 | 9.5 | **F10** batch `next_commands` |
| Discoverability | 9 | 9.5 | **#2** advertised |
| Error / recovery | 9 | 9.5 | **F9** consistency; correct headline |
| Safety | 9 | 9 | unchanged |

Projected consumer mean: (9+8.5+9.5+9.5+9.5+9.5+9.5+9)/8 ≈ **9.25**.

Both axes clear 9. The speed/latency ceiling stays below 10 because it is
upstream-bound (interactive-use-only Cloud Run, 13–40 s cold start); the lever is
making the existing `task=True` background path *visible and usable*, not making
the upstream faster.

## 2. Scope (decided)

In scope (v0.3.0):

1. **F6 (HIGH)** — `predict_splicing` headline contradicts its own `agreement.verdict`.
2. **F6b (correctness, bundled with F6)** — add a `concordant_moderate` verdict band.
3. **F7 (MED)** — `transcripts:"all"` returns N byte-identical blocks; compact does not trim.
4. **F8 (LOW–MED)** — `minimal` mode barely differs from `compact`.
5. **F9 (LOW)** — `validation_failed` `_meta` omits `request_id`/`timing`.
6. **F10 (LOW)** — batch `summary` thin; misleading single-gene `see_also`; no `next_commands`.
7. **#2** — surface the background-task capability in discovery (descriptor + tool text + test).
8. **#4** — `interpretation: {band, threshold_basis}` beside `max_delta_score`.
9. **#5** — `cache_age_s` / `cache_ttl_s` in `_meta` for cache auditability.

Out of scope (unchanged from pass 1): new prediction models
(AlphaMissense/PrimateAI/PromoterAI/CADD), allele-frequency/ClinVar/expression
(delegated to sibling `-link` servers), any clinical framing, REST beyond
`/health`, multi-worker task backend (document `FASTMCP_DOCKET_URL` only).

## 3. Findings → fixes

### F6 — headline contradicts `agreement.verdict` (HIGH)

**Root cause (code-confirmed).** `spliceailookup_link/mcp/tools/_predict.py`:

- `_assess_agreement` (`:44`) is **3-state**: `concordant_high` (both ≥ `_HIGH`
  0.5), `concordant_low` (both < `_LOW` 0.2), else `discordant`.
- `_combined_headline` (`:217`) **recomputes** agreement as **2-state**:
  `agree if (sai_max >= _HIGH) == (pang_max >= _HIGH)` (`:236`).

The two diverge in the moderate band: a 0.31/0.09 variant is `(False)==(False)` →
headline "models agree", while `_assess_agreement` yields `discordant`. The
headline is the most-read field; it must never contradict the structured verdict.
Reproduced by the eval on `1-169549811-C-A` (0.31/0.09) and ABCA3
`16-2317763-T-A` (0.21/0.05).

**Fix — single source of truth.**

- `_combined_headline` takes the already-computed `agreement` dict (built at
  `_predict.py:203`, before the headline at `:204`) and renders its clause from
  `agreement["verdict"]` — it does **not** recompute anything from raw scores.
- Verdict → headline clause map:
  - `concordant_high` → "; models agree (both strong)"
  - `concordant_moderate` → "; models agree (both moderate)"
  - `concordant_low` → "; models agree (both low/none)"
  - `discordant` → "; models disagree"
  - `incomplete` → "; only one model scored" (or no clause when a model is absent)
- The numeric `SpliceAI Δ=… ; Pangolin Δ=…` prefix and the `; predicted <aberr>`
  tail are unchanged.

**F6b — verdict band correctness (bundled).** The current 3-state has a real gap:
two models both predicting *moderate* (e.g. 0.30/0.32) fall through to
`discordant`, which is wrong — they agree. Add a `concordant_moderate` band:

```
both_high      = sai >= 0.5 and pang >= 0.5      -> concordant_high
both_low       = sai < 0.2 and pang < 0.2        -> concordant_low
both_moderate  = both in [0.2, 0.5)              -> concordant_moderate
otherwise                                         -> discordant
one missing                                       -> incomplete
```

This is an **additive enum value** (`agreement.verdict` gains
`concordant_moderate`). Documented in the score glossary, the reference resource,
and the capabilities descriptor. The batch `summary` (F10) counts it.

**Tests.** A consistency matrix over representative (sai, pang) pairs asserting
the headline's agreement clause is derivable from — and never contradicts —
`agreement.verdict`: `(0.83, 0.85)` concordant_high; `(0.30, 0.32)`
concordant_moderate; `(0.05, 0.09)` concordant_low; `(0.31, 0.09)` and
`(0.21, 0.05)` discordant; `(0.8, None)` incomplete.

### F7 — `transcripts:"all"` duplicate blocks (MED)

**Root cause (code-confirmed).** `shaping.py:_select_transcripts` (`:62`) returns
all rows verbatim for `transcripts="all"`; `_shape_*_transcript` produces one
block each; nothing collapses identical blocks and `compact` does not trim. For
TRAPPC9 the upstream returns 19 transcripts with byte-identical delta scores → 19
identical blocks, ~19× consumer tokens for one fact.

**Fix — lossless collapse + optional cap.**

- After shaping the selected transcripts, group blocks whose **score signature**
  (the `delta_scores` map: per-class `{score, position}`, plus `max_delta_score`)
  is identical. Emit **one representative** per group; attach
  `shared_by: [transcript_id, …]` (the other transcripts' ids, sorted) and a
  merged `refseq_ids` union. Lossless: no score is dropped, only de-duplicated.
- Applies in every mode for `transcripts="all"` (the collapse is the point).
  `transcripts="mane"` is unaffected (already one block).
- Add an optional `max_transcripts: int | None = None` parameter to the three
  prediction tools and the batch tool. When set and the *collapsed* list still
  exceeds it, keep the top-`N` by `max_delta_score` and add
  `_meta.transcripts_truncated: {kept, total}` plus a `log()`-style note. Default
  `None` preserves current behaviour (all distinct transcripts).
- Helper lives in `shaping.py` (`_collapse_identical_transcripts`); ~30 LOC, file
  stays well under budget.

**Tests.** Mock an `all` payload with 3 identical + 1 distinct transcript →
collapsed to 2 blocks, representative carries `shared_by` of length 2; serialized
size strictly less than the un-collapsed fixture; `max_transcripts=1` keeps the
highest `max_delta_score` and stamps `transcripts_truncated`.

### F8 — `minimal` barely differs from `compact` (LOW–MED)

**Root cause (code-confirmed).** `shaping.py` `minimal` only does `shaped[:1]`
(`:181`, `:283`) and `_common.see_also_for` returns `[]`; the single retained
transcript still carries the full `delta_scores` map, identity fields, and (for
SpliceAI) the `consequence` block. Side-by-side, `minimal` was within a few
percent of `compact`.

**Fix — `minimal` becomes a true headline tier.**

Single-model (`shape_spliceai` / `shape_pangolin`) `minimal` result:

```jsonc
{
  "model": "SpliceAI",
  "variant_id": "...", "genome_build": "GRCh38", "gene": "TRAPPC9",
  "max_delta_score": 0.83,
  "interpretation": { "band": "high" },          // #4, inline in minimal
  "top": { "class": "acceptor_loss", "score": 0.83, "position": -2 },
  "consequence_summary": "exon_skipping",         // single string if present, else omitted
  "headline": "SpliceAI (GRCh38): TRAPPC9 — strong acceptor loss (Δ=0.83 at -2 bp)."
}
```

No `transcripts[]` array, no per-class `delta_scores` map, no `ref_alt_scores`,
no full `consequence.aberrations` list, no `see_also`.

Combined (`predict_splicing`) `minimal` result:

```jsonc
{
  "variant_id": "...", "genome_build": "GRCh38", "gene": "TRAPPC9",
  "agreement": { "verdict": "concordant_high" },  // verdict only, no detail
  "spliceai_max": 0.83, "pangolin_max": 0.85,
  "interpretation": { "band": "high" },
  "headline": "TRAPPC9 (GRCh38): SpliceAI Δ=0.83; Pangolin Δ=0.85; models agree (both strong)."
}
```

`compact` (default) and `full` are unchanged except for the additive
`interpretation` (F#4) and F7 collapse. Contract documented crisply in the
glossary: **minimal = headline + the single decision number; compact =
per-transcript deltas; full = + REF/ALT + exon model.**

**Tests.** For both single-model and combined: serialized `minimal` strictly
smaller than `compact` strictly smaller than `full`; `minimal` has no
`delta_scores` map and no `see_also`; `minimal` still carries `headline`,
`max_delta_score`/`*_max`, and `interpretation.band`.

### F9 — `validation_failed` `_meta` omits `request_id`/`timing` (LOW)

**Root cause (code-confirmed).** `errors.py:install_validation_error_handler`
wraps each tool's `run`; on `PydanticValidationError` it returns
`mcp_validation_tool_error(...).payload` directly through `convert_result`
(`:252`). That path **never enters `run_mcp_tool`**, whose `_stamp` (`:315`) is
the only place `request_id` + `timing` are added. So validation envelopes lack
them — directly contradicting the capabilities claim that "every `_meta` carries
`request_id` + `timing.elapsed_ms`."

**Fix.** In `wrapped_run`, generate a `request_id` and measure `perf_counter`
around `_original_run`; merge `{request_id, timing:{elapsed_ms}}` into the
validation envelope's `_meta` before returning (mirroring `_stamp`'s shape and
ordering). Keep `mcp_validation_tool_error` as the envelope factory; the stamping
happens at the wrapper boundary so request-scoped values are correct. No
behavioural change to successful calls or non-validation errors.

**Tests.** `predict_spliceai(max_distance=20000)` (over the `le=10000` bound) →
envelope `error_code == "validation_failed"`, `_meta.request_id` is a 12-char
hex, `_meta.timing.elapsed_ms` is an int ≥ 0, and `field_errors` still present.

### F10 — batch summary thin / misleading see_also / no next_commands (LOW)

**Root cause (code-confirmed).** `batch.py:93` counts only
`{ok, failed, concordant_high}`; `:101` emits one `see_also` built from the first
arbitrary gene in the panel; the batch envelope has no `next_commands`.

**Fix.**

- **Full verdict histogram** in `summary`:
  `{ok, failed, concordant_high, concordant_moderate, concordant_low,
  discordant, incomplete}` (computed from each result's `agreement.verdict`;
  `failed` items, which have no verdict, are excluded from the verdict buckets).
  Add `top_variant: {variant, max_delta_score}` for the single highest-impact
  result so the agent has a ranked entry point.
- **Same-server `next_commands`** on the batch envelope: one ready-to-call
  `predict_splicing` for `top_variant.variant` with `response_mode="full"` (drill
  into the most impactful hit). Honors the next_commands-vs-see_also contract
  (next_commands = same server; see_also = cross server).
- **Drop the batch-level single-gene `see_also`** (misleading for a multi-gene
  panel). The batch envelope asserts no panel-wide cross-server hint; an agent
  that wants gnomAD/GeneReviews context per variant uses the same-server
  `next_commands` drill-down (whose `full`-mode result carries its own
  `see_also`) or calls the sibling servers directly. Keeping per-item results
  `see_also`-free is also what makes the one-envelope batch token-efficient.

**Tests.** Mixed-verdict panel → `summary` contains all seven counts and they sum
correctly (`ok == sum(verdict buckets)`); batch envelope has non-empty
`next_commands` pointing at the top variant in `full` mode; batch envelope has no
top-level `see_also`.

## 4. Consumer improvements

### #2 — Surface the background-task capability in discovery

**Context (spec-confirmed).** MCP 2025-11-25 advertises per-tool task-eligibility
via `execution.taskSupport` in the `tools/list` result, value
`"optional"|"required"|"forbidden"`. FastMCP `task=True` emits `"optional"`. The
server already sets `task=True` on `predict_spliceai`, `predict_pangolin`,
`predict_splicing`, `predict_splicing_batch`. The gap Part 4a identified is that
this is **invisible to an agent reading the hand-authored ~4 kB capabilities
descriptor** (the documented cold-start), so the agent blocks on a 30 s call
instead of backgrounding it. Many clients also do not surface
`execution.taskSupport` to the model, so the descriptor must say it explicitly.

**Fix.**

- Add a `background_execution` block to `get_capabilities_resource()`:
  - `task_eligible_tools`: the four tool names.
  - `task_support`: `"optional"` (sync still works; augment to background).
  - `how_to`: "augment the `tools/call` with a `task` field (per MCP 2025-11-25
    Tasks); poll `tasks/get`, retrieve with `tasks/result`."
  - `backend`: `"in-process (memory://); tasks are session-local, lost on server
    restart, and not auth-context-bound — retrieve results within the session."`
  - `recommended_for`: "cold `predict_*` calls (13–40 s) and
    `predict_splicing_batch`."
- Append one sentence to each task-tool description: *"Supports MCP background
  tasks (`execution.taskSupport=optional`): augment the call with a `task` to
  fire-and-continue instead of blocking 15–40 s."*
- This changes the capabilities descriptor → `capabilities_version` hash changes
  (correctly signals v0.3.0).

**Verification.**

- Unit (in-memory FastMCP client): `await client.list_tools()` → each of the four
  tools exposes `execution.taskSupport == "optional"`; the other tools do not
  (or expose `"forbidden"`/absent). This pins improvement #2's protocol surface
  so a future regression is caught.
- Unit: capabilities descriptor contains `background_execution.task_eligible_tools`
  == the four names; each task tool's `description` contains "background task".
- Manual (running container, already in pass-1 acceptance): task-augmented
  `predict_splicing` returns a `taskId`; `tasks/result` returns the completed
  result; a normal call is unchanged.
- Optional (verify, do not force): whether FastMCP populates
  `io.modelcontextprotocol/model-immediate-response` in the `CreateTaskResult`
  `_meta`. If absent and trivially configurable, supply a short immediate string
  ("scoring in background; poll for the result"); otherwise document the host
  fallback and move on. Not a blocker.

### #4 — `interpretation` band beside `max_delta_score`

Agents currently re-derive the 0.5/0.2 cutoffs from prose. Surface them as data.

- Add `interpretation: {band, threshold_basis}` next to `max_delta_score`:
  - single-model results: `band` from that model's `max_delta_score` via a new
    small `_band()` helper returning the clean four-value public enum
    `high|moderate|low|none` (Δ≥0.5 high, 0.2–0.5 moderate, >0–0.2 low, 0 none).
    This is **distinct from** `_strength()`, which keeps returning the prose words
    `strong|moderate|weak|none` used inside the human-readable headlines
    ("strong acceptor loss"); `_strength` is unchanged so headline wording does
    not regress.
  - combined result: top-level `interpretation` whose `band` is the stronger of
    the two models' bands.
  - `threshold_basis`: a short constant — `"Δ≥0.5 high; 0.2–0.5 moderate;
    >0–0.2 low; 0 none (SpliceAI/Pangolin community convention)"`.
- In `minimal`, include `interpretation: {band}` only (drop `threshold_basis` to
  stay headline-tier — it is documented once in the glossary).
- Tiny token cost (one short object); offset by F7/F8 reductions.

**Tests.** `band` is correct for representative scores (0.83→high, 0.31→moderate,
0.1→low, 0→none); `threshold_basis` present in compact/full, absent in minimal.

### #5 — `cache_age_s` / `cache_ttl_s` in `_meta`

Make cache hits auditable so an agent can tell a fresh hit from a near-stale one.

- `cache_ttl_s`: trivial — `SpliceService` already knows `ttl_seconds`
  (`cache_ttl_minutes * 60`, default 86400). Expose it on `CallTelemetry` and in
  `_meta` of prediction payloads.
- `cache_age_s`: add `self._scored_at: dict[key, float]` in `SpliceService`,
  recording a monotonic timestamp on each **miss** (when the body actually ran).
  On a **hit**, `cache_age_s = round(monotonic() - scored_at[key])`. Bound the
  map to `cache_size` (drop oldest insert when exceeded) so it cannot outgrow the
  LRU it shadows — this also fixes the pre-existing unbounded growth of
  `_scored_keys` by giving both a single bounded structure.
- `CallTelemetry` gains `cache_age_s: int | None` and `cache_ttl_s: int | None`.
  `combined.py` / `spliceai.py` / `pangolin.py` fold them into `_meta` next to the
  existing `cache` field. Emitted only when `cache == "hit"` (age) / always
  (ttl). For `predict_splicing` (two underlying calls) report the **max** age
  across the hit calls, matching how `upstream_elapsed_ms` is aggregated.

**Tests.** First call → `cache: "miss"`, no `cache_age_s`, `cache_ttl_s` present;
immediate repeat → `cache: "hit"`, `cache_age_s` an int ≥ 0, `cache_ttl_s`
unchanged.

## 5. Module map (600-LOC budget)

New files:

- `spliceailookup_link/mcp/tools/_predict_shape.py` — extract the combined-tool
  presentation logic out of `_predict.py`: `_assess_agreement` (with F6b band),
  `_combined_headline` (F6, verdict-driven), combined `interpretation`, and the
  combined `minimal` projection. Keeps `_predict.py` orchestration-only and both
  files under budget.

Modified (all expected < 500 lines; re-check with `make lint-loc`):

- `mcp/tools/_predict.py` — call `_predict_shape` helpers; pass `agreement` into
  the headline; wire `max_transcripts`, `interpretation`, telemetry passthrough.
- `mcp/shaping.py` — F7 `_collapse_identical_transcripts`; F8 single-model
  `minimal` projection; #4 per-model `interpretation`; `_strength` band rename to
  the public `high|moderate|low|none`.
- `mcp/tools/batch.py` — F10 full summary histogram, `top_variant`,
  `next_commands`, drop batch `see_also`; thread `max_transcripts`.
- `mcp/tools/combined.py` / `spliceai.py` / `pangolin.py` — `max_transcripts`
  param; fold `cache_age_s`/`cache_ttl_s` into `_meta`; append the background-task
  sentence to each description.
- `mcp/errors.py` — F9 stamp `request_id` + `timing` in `wrapped_run`.
- `services/telemetry.py` — `cache_age_s`, `cache_ttl_s` on `CallTelemetry`.
- `services/splice_service.py` — `_scored_at` map (bounded), populate the new
  telemetry fields.
- `mcp/resources.py` — `background_execution` block; document the new verdict
  band, the four-band interpretation enum + `threshold_basis`, the
  minimal/compact/full tier contract, and the `shared_by`/`transcripts_truncated`
  shapes; version bump.
- `__init__.py` / `pyproject.toml` — version → `0.3.0`.

## 6. Testing / CI additions (all respx-mocked, no live upstream)

- **F6:** headline-vs-verdict consistency matrix (six representative pairs above);
  assert headline contains the clause implied by `agreement.verdict` for each.
- **F6b:** `(0.30, 0.32)` → `concordant_moderate`; existing concordant_high /
  concordant_low / discordant / incomplete cases still pass (update fixtures).
- **F7:** identical+distinct `all` payload → collapse + `shared_by`; size strictly
  reduced; `max_transcripts=1` → top-N + `transcripts_truncated`.
- **F8:** `minimal < compact < full` serialized size (single-model and combined);
  `minimal` shape asserts (no `delta_scores`, has `headline` + band).
- **F9:** over-range `max_distance` → `_meta.request_id` + `timing.elapsed_ms`
  present on the `validation_failed` envelope.
- **F10:** mixed panel → seven summary counts, `ok == sum(verdict buckets)`,
  batch `next_commands` present, no batch `see_also`.
- **#2:** `list_tools()` exposes `execution.taskSupport == "optional"` on the four
  task tools and not on the rest; descriptor has `background_execution`; each task
  description mentions background tasks.
- **#4:** band correctness across scores; `threshold_basis` present in
  compact/full, absent in minimal.
- **#5:** miss→hit cache telemetry (`cache_age_s` ≥ 0, `cache_ttl_s` present).
- **Hash:** `capabilities_version` differs from the committed v0.2.0 value and is
  stable across two calls.

Keep coverage ≥ 80% (`fail_under`). Background-task round-trip stays a manual
verification step (FastMCP in-memory client where practical), not default CI.

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| F6b adds a verdict value → downstream parsers expecting 3 states | Additive enum; documented in glossary + reference + capabilities; version bumped to 0.3.0; batch summary enumerates all values. |
| F8 reshapes `minimal` → clients depending on `minimal` having full deltas | `minimal` was explicitly under-specified (the bug). Tier contract now documented; `compact` (the default) is unchanged and is what most callers use. |
| F7 `shared_by`/`transcripts_truncated` change the `all` array shape | Only triggers under `transcripts="all"` (a deliberate opt-in) or when `max_transcripts` is set; documented; `mane` default unchanged. |
| `interpretation` + cache fields inflate `_meta`/payload tokens | All tiny scalars/short strings; net offset by F7 collapse + F8 minimal; in minimal only `band` is carried. |
| `_predict_shape.py` extraction churns call sites | Pure internal refactor; tool names/schemas/response keys unchanged except documented additions; covered by existing + new unit tests. |
| FastMCP does not emit `execution.taskSupport` as expected | The `list_tools()` test makes the assumption explicit and catches drift; if absent, fall back to advertising via the descriptor text only and record the FastMCP version gap (descriptor advertisement is independent and still ships). |
| `_scored_at` unbounded like today's `_scored_keys` | New map is bounded to `cache_size` (drop-oldest), improving on the status quo. |

## 8. Acceptance

- `make ci-local` green (format, lint, lint-loc ≤600, mypy, tests, ≥80% cov).
- All F6–F10 + #2/#4/#5 tests pass; `_predict_shape.py` keeps `_predict.py` and
  itself under budget.
- Headline never contradicts `agreement.verdict` (consistency matrix green).
- `minimal` strictly smaller than `compact`; `transcripts="all"` collapses
  identical blocks.
- `validation_failed` carries `request_id` + `timing`.
- Capabilities advertises background execution; `list_tools()` confirms
  `execution.taskSupport == "optional"` on the four tools.
- Manual: task-augmented `predict_splicing` returns a `taskId` + retrievable
  result; cache hit shows `cache_age_s`/`cache_ttl_s`; multi-allelic
  `resolve_variant("rs6025")` still chains cleanly (F1 regression intact).
- Re-run the `docs/mcp-evaluation.md` method (fresh, black-box); record a **Part
  5** appendix with the new per-tool and per-dimension scores; expect both axes
  **>9/10**.

*Research use only; not for clinical decision support. Splice predictions are
computational and must be interpreted alongside orthogonal evidence.*
