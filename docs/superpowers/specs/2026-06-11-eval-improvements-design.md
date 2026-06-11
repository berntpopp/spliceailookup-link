# spliceailookup-link — Evaluation-Driven Improvements (Design)

**Date:** 2026-06-11
**Author:** MCP engineering (driven by `docs/mcp-evaluation.md`)
**Status:** Design — pending implementation plan
**Baseline:** v0.1.0 · MCP protocol 2025-11-25 · FastMCP 3.x

## 1. Goal

Lift the two headline scores in `docs/mcp-evaluation.md` from **8.3/10**
(LLM-consumer) and **8.0/10** (senior-tester) to **>9/10** on both axes, by
closing the five concrete findings (F1–F5) and the six ranked improvements the
evaluation enumerates. Do this without violating the project's load-bearing
constraints: thin FastAPI host, MCP facade is the product, aggressive caching,
conservative concurrency, 600-LOC/file budget, and the research-use-only framing.

### Score model (what we are moving)

| Axis | Dimension | Now | Target | Lever |
|---|---|---|---|---|
| Consumer | Speed / latency | 6 | 8+ | Progress notifications + native MCP Tasks + batch + warmup |
| Consumer | Observability | 7 | 9 | `_meta.request_id` / `timing.elapsed_ms` / `cache` / `upstream_elapsed_ms` |
| Consumer | Token efficiency | 8 | 9 | Gate/collapse `see_also`, dedup `predict_splicing` (F4) |
| Consumer | Discoverability | 9 | 9.5 | `capabilities_version` content hash |
| Consumer | Error/recovery | 9 | 9.5 | Auto `build_mismatch` (F5) |
| Consumer | Schema ergonomics | 9 | 9.5 | Stable `consequence` contract (F2) |
| Consumer | Chaining | 9 | 9.5 | Uniform `next_commands` (F3) + correct multi-allele chaining (F1) |
| Tester | `resolve_variant` | 6 | 9 | Fix F1 (multi-allelic rsID) |
| Tester | `predict_spliceai` | 8 | 9 | Fix F2 |
| Tester | `predict_splicing` | 8 | 9 | Fix F3 + F4 |

The latency dimension is upstream-bound and cannot reach 10; everything else has
clear headroom to ≥9. Average of the projected per-dimension scores clears 9 on
both axes.

## 2. Scope (decided)

In scope (all confirmed with the maintainer):

1. **F1 (HIGH)** — multi-allelic rsID returns a stringified Python list.
2. **F2 (MED)** — `consequence` object shape drifts (`aberrations` vs `raw.aberrations`).
3. **F3 (LOW)** — `predict_splicing` `_meta` omits `next_commands`.
4. **F4 (LOW)** — `predict_splicing` duplicates `consequence` + transcript identity.
5. **F5 (LOW)** — `build_mismatch` documented but never auto-detected at runtime.
6. **Runtime observability** in `_meta`.
7. **Token-efficiency** trims (`see_also` gating + F4 dedup).
8. **Latency:** progress notifications **and** native MCP background Tasks (`task=True`).
9. **New tool:** `predict_splicing_batch`.
10. **New tool:** `warmup`.
11. **`capabilities_version`** content hash.
12. **CI coverage** additions (eval improvement #6), all respx-mocked.

Out of scope (unchanged): adding AlphaMissense/PrimateAI/PromoterAI/CADD,
allele-frequency/ClinVar/expression (delegated to sibling servers), any clinical
framing, REST surface beyond `/health`.

## 3. Findings → fixes

### F1 — Multi-allelic rsID (HIGH)

**Root cause:** `splice_service.py:_normalize_vep_record` does
`variant_id = str(vcf_string)`. Ensembl VEP returns `vcf_string` as a **list**
when an rsID has multiple ALT alleles at one locus (e.g. `rs6025` →
`["1-169549811-C-A", "1-169549811-C-T"]`). The list is stringified into a scalar
field and copied into `_meta.next_commands[0].arguments.variant`, so the
advertised "execute the first next_command" contract yields an unparseable call.

**Fix (combines eval options a + b):**

- Normalize `vcf_string` to a list of candidate ids (also dedup; strip `chr`).
- **Single allele:** unchanged behaviour — `variant_id` is the scalar id.
- **Multiple alleles (ambiguous):**
  - `variant_id` = the first allele (keeps the field a single, parseable
    `CHROM-POS-REF-ALT`, satisfying the regression contract).
  - add `ambiguous: true` and `variant_ids: [...all alleles...]`.
  - add a short `note`: "rsID maps to N alleles at this locus; pick one
    `variant_id` before predicting."
  - `_meta.next_commands` emits **one `predict_splicing` per allele** (not just
    the first), so every allele is reachable and the first entry is valid.

**Output-schema change:** `resolve.py:_OUTPUT_SCHEMA` gains optional
`ambiguous: bool`, `variant_ids: string[]`, `note: string`. `variant_id` stays
required + scalar.

**Regression test:** mock VEP `/vep/human/id/rs6025` with the real
`vcf_string`-as-list shape; assert `variant_id` matches
`^(chr)?[\dXYM]+-\d+-[ACGT]+-[ACGT]+$`, `ambiguous is True`,
`len(variant_ids) >= 2`, and **every** `next_commands[i].arguments.variant`
parses as a single coordinate.

### F2 — `consequence` shape drift (MED)

**Root cause:** `shaping.py:_shape_consequence` returns `out["aberrations"]` when
the SAI-10k aberration list is non-empty, but falls back to `out["raw"] = sai`
(the whole upstream object, which itself contains `.aberrations` and
`.transcript_info`) when the list is empty. Empty aberrations happen routinely
under `mask=masked` (the relevant site is zeroed) — which is why the evaluator
saw `consequence.raw.aberrations` rather than `consequence.aberrations`. It is
**not** strictly a `response_mode` effect; it is an empty-list fallback.

**Fix — make `consequence.aberrations` the stable path in every mode:**

- Always emit `consequence.aberrations` as a list (`[]` when none), never under
  `raw`.
- In `full` mode only, additively attach `consequence.transcript_info` and any
  other upstream extras as **siblings** of `aberrations` (never replacing it).
- Document in the score glossary + reference resource: under `mask=masked` the
  aberration list is computed on masked scores and may be empty even when `raw`
  mode predicts an aberration.

### F3 — `predict_splicing` missing `next_commands` (LOW)

**Root cause:** `combined.py` sets `_meta = {"see_also": ...}` only. The two
single-model tools include `next_commands`; the headline tool does not.

**Fix:** Add a `next_commands` entry to `predict_splicing` `_meta`. Per the
server's own contract, `next_commands` must be **same-server callable** (cross-
server delegation stays in `see_also`), so it points at a genuinely useful
same-server drill-down: `predict_spliceai` (or `predict_pangolin`) with
`response_mode="full"` for the same `variant_id`/build — i.e. "get the REF/ALT raw
scores + exon model for this variant." `see_also` (gnomad/genereviews/gtex) is
retained unchanged. This makes the affordance uniform with the single-model tools
without violating the next_commands-vs-see_also boundary the capabilities doc
advertises.

### F4 — `predict_splicing` duplication (LOW)

**Root cause:** `consequence` is emitted top-level **and** left inside the
`spliceai` sub-object; the full transcript-identity block (gene/gene_id/
transcript_id/refseq_ids/strand) is repeated in both model sub-objects.

**Fix:**

- Lift `consequence` to top-level only; pop it from the `spliceai` sub-object.
- Lift a single top-level `transcript` identity block
  (`{gene, gene_id, transcript_id, transcript_priority, refseq_ids, strand}`)
  taken from the top transcript when both models report the same transcript;
  per-model sub-objects then carry only `delta_scores` + `max_delta_score`
  (+ `transcript_id` *only if it differs* from the lifted one). When the models
  disagree on transcript, fall back to the current per-model identity (correctness
  over compactness).
- Target: ~25–30% fewer tokens per compact `predict_splicing` call.

This shaping logic is combined-tool-specific, so it lives in a new
`mcp/tools/_combine_shape.py` helper (keeps `combined.py` under budget).

### F5 — `build_mismatch` never auto-detected (LOW)

**Root cause:** `detect_build_mismatch` is a **static** chromosome-length-table
check. It catches positions out of range for the requested build, but a
coordinate valid in *both* builds (e.g. `chr8-140300616`, where chr8 is ~145–146
Mb in both) passes the static check and, when it scores in the wrong build,
returns generic `not_found`.

**Fix — opportunistic cross-build probe on `not_found`:**

- Only when the input was a **coordinate** (HGVS/rsID are already build-resolved)
  and the scoring call returned `not_found`.
- Fire **one** cache-backed scoring call against the *other* build (single model
  — SpliceAI — is enough to decide).
- If it scores there (≥1 transcript with a delta), raise `BuildMismatchError`
  (already wired into the error taxonomy) with a `genome_build`-flipped
  `next_command`, turning a dead-end into a one-hop self-correction.
- Cost control: gated behind `cross_build_check: bool = True` tool param so a
  caller in a tight loop can disable it; the probe is cache-backed (free on
  repeat); emit a `_meta.cross_build_probed: true` note and a progress message so
  the added latency is visible. If the other build also returns `not_found`, the
  original `not_found` envelope is returned unchanged.

## 4. Runtime observability

Every success and error envelope already passes through `run_mcp_tool`
(`errors.py:302`), which is the single chokepoint that stamps `_meta`. Extend it:

- `_meta.request_id` — `uuid4().hex[:12]`, generated per call, also written to the
  structured log line so a slow/odd call can be correlated.
- `_meta.timing.elapsed_ms` — wall time around the tool body (`perf_counter`).

The cache + upstream signals originate in the service and are surfaced up to the
tool body, which attaches them before `run_mcp_tool` merges:

- `_meta.cache` — `"hit" | "miss" | "partial"` (partial for `predict_splicing`
  when one model hit and the other missed).
- `_meta.upstream_elapsed_ms` — measured around the actual upstream call(s);
  omitted on a pure cache hit.

**Cache hit/miss mechanism (recommended):** keep `alru_cache` but detect whether
the underlying body ran via a `ContextVar` flag set inside `_score_uncached` /
`_resolve_uncached`. `asyncio.gather` wraps each coroutine in its own Task with an
isolated context, so concurrent scoring calls report hit/miss independently.
`SpliceService.score()`/`resolve()` return the payload plus a small
`CallTelemetry{cache, upstream_elapsed_ms}` (new lightweight dataclass), rather
than changing the payload itself.

This is purely additive to `_meta`; no existing field moves.

## 5. Token efficiency — `see_also` policy

`see_also` is valuable on a standalone/first call and pure tax inside a
multi-variant loop. Stateless policy (no session tracking):

- `response_mode == "minimal"` → omit `see_also` entirely.
- `response_mode == "compact"` (default) → collapse each hint to
  `{server, hint}` (drop the verbose `example` args block).
- `response_mode == "full"` → full hints incl. `example` args (today's shape).

`predict_splicing_batch` emits **one** `see_also` block for the whole batch, not
per item.

## 6. Latency — progress + native Tasks

### 6a. Progress notifications

The three scoring tools and `predict_splicing_batch` accept an injected
`ctx: Context` (FastMCP) and emit `await ctx.report_progress(progress, total,
message=...)` at each stage:

- `predict_spliceai`/`predict_pangolin`: `resolving → scoring → shaping`.
- `predict_splicing`: `resolving → scoring spliceai → scoring pangolin → merging`.
- `predict_splicing_batch`: one increment per completed variant.

`report_progress` is a no-op when the client did not send a `progressToken`, so
this is fully backward-compatible — stdio and non-progress clients are unaffected.
Progress notifications keep the host's timeout window open and give the agent
liveness during the 10–40 s cold calls.

### 6b. Native MCP background Tasks

Adopt the 2025-11-25 Tasks capability via FastMCP:

- `FastMCP(..., tasks=True)` in the facade.
- `task=True` on `predict_spliceai`, `predict_pangolin`, `predict_splicing`,
  `predict_splicing_batch` (all already `async`, the only requirement).
- Backend: `memory://` (Docket in-process) by default — correct for our
  single-process unified host. `FASTMCP_DOCKET_URL` (e.g. `redis://…`) documented
  for multi-worker deployments; surfaced through config as
  `SPLICEAILOOKUP_LINK_DOCKET_URL` (default `memory://`).
- Dependency: add `fastmcp[tasks]` (pulls Docket) to `pyproject.toml`.

`task=True` advertises task-eligibility; the client opts in per call via task
augmentation and otherwise gets normal synchronous execution. Fast tools
(`resolve_variant`, `get_server_capabilities`, `warmup`) stay synchronous-only.

**Verification gate (in plan):** confirm in the running container that (a) a
task-augmented `predict_splicing` returns a `taskId` and the result is retrievable
via `tasks/result`, and (b) a normal call still works unchanged. If FastMCP 3.x
task wiring proves unstable against our unified host, fall back to
progress-notifications-only (still a real latency-dimension lift) and record the
decision — the two are independent.

## 7. New tools

### 7a. `predict_splicing_batch`

```
predict_splicing_batch(
    variants: list[str]  (1..25),
    genome_build, max_distance, mask, gene_set, transcripts,
    response_mode = "compact",
    cross_build_check = True,
) -> { success, count, results: [...], summary, _meta }
```

- Fans out the existing `predict_splicing` logic server-side, bounded by the
  shared `MAX_CONCURRENCY` semaphore (no new pressure on the upstream).
- Per-item failures are captured as `{variant, error_code, message}` and do **not**
  fail the batch; `summary` reports `{ok, failed, concordant_high, ...}`.
- One envelope, one `see_also`, one `_meta` (per-item `_meta` suppressed) — the
  token-saving point of the tool.
- `task=True` + progress (one increment per completed variant).
- List length capped (≤25) → over-cap yields `validation_failed`.
- Lives in new `mcp/tools/batch.py`.

### 7b. `warmup`

```
warmup(genome_build = "GRCh38") -> { success, warmed, elapsed_ms, detail, _meta }
```

- Fires a tiny fixed known-good scoring call (a sentinel coordinate) against
  SpliceAI **and** Pangolin for the build to wake the Cloud Run cold start.
- **Not** cached (defeats the purpose) — uses an uncached path / `raw`-less probe.
- Returns elapsed time so the agent learns cold-vs-warm before the first
  user-facing call. Synchronous, fast intent (but honest if the upstream is cold).
- Registered alongside metadata tools (small) — `mcp/tools/metadata.py` or a new
  `mcp/tools/ops.py` if `metadata.py` approaches budget.

## 8. `capabilities_version` hash

- Compute `sha256(json.dumps(capabilities, sort_keys=True))[:12]` over the
  capabilities dict (excluding the hash field itself).
- Add `capabilities_version` and `descriptor_chars` (serialized length) to
  `get_capabilities_resource()` output and the `spliceailookup://capabilities`
  resource, mirroring sibling `hnf1b-mcp`.
- Document the warm-client contract in the capabilities doc: compare the hash and
  skip the ~4 kB re-fetch when unchanged.
- Computed deterministically and memoised at import/first-call (it only changes
  with code).

## 9. Module map (respecting the 600-LOC budget)

New files:

- `spliceailookup_link/mcp/telemetry.py` — `CallTelemetry` dataclass, `request_id`
  + timing helpers, cache-detection `ContextVar`.
- `spliceailookup_link/mcp/tools/_combine_shape.py` — F4 lift/dedup for
  `predict_splicing`.
- `spliceailookup_link/mcp/tools/batch.py` — `predict_splicing_batch`.

Modified (all expected to stay < 500 lines; re-check in plan):

- `services/splice_service.py` — F1 normalize, telemetry return, F5 probe helper.
- `mcp/shaping.py` — F2 stable consequence.
- `mcp/tools/resolve.py` — F1 schema + ambiguous payload.
- `mcp/tools/combined.py` — F3 next_commands, F4 via `_combine_shape`, progress,
  `task=True`, `cross_build_check`.
- `mcp/tools/spliceai.py` / `pangolin.py` — progress, `task=True`, F5 hook.
- `mcp/tools/_common.py` — `see_also` policy by `response_mode`.
- `mcp/tools/metadata.py` (+ `warmup`), `mcp/resources.py` (capabilities hash),
  `mcp/errors.py` (request_id + timing in `run_mcp_tool`), `mcp/facade.py`
  (`tasks=True`), `config.py` (`DOCKET_URL`), `pyproject.toml` (`fastmcp[tasks]`).

## 10. Testing / CI additions (all respx-mocked, no live upstream)

- **F1:** `rs6025` regression (above).
- **F2:** masked `predict_spliceai` asserts `consequence.aberrations` exists
  (possibly `[]`) and `consequence.raw` does **not**; full mode asserts
  `transcript_info` present as a sibling of `aberrations`.
- **F3:** `predict_splicing` envelope has non-empty `_meta.next_commands`.
- **F4:** compact `predict_splicing` has exactly one `consequence` and a single
  top-level `transcript` block; assert serialized size strictly < pre-fix
  fixture.
- **F5:** mock build-A `not_found` + build-B scores → assert `build_mismatch` with
  flipped `genome_build` next_command; assert disabled when
  `cross_build_check=False`.
- **Observability:** assert `_meta.request_id`, `_meta.timing.elapsed_ms`,
  `_meta.cache` present; cache `hit` on a repeat call, `miss` on first.
- **Token policy:** `minimal` strictly smaller than `compact`; `minimal` has no
  `see_also`; `compact` `see_also` entries have no `example` key; `full` does.
- **Batch:** mixed success/failure list → one envelope, `summary.failed == 1`,
  per-item `_meta` absent, single `see_also`; over-cap → `validation_failed`.
- **Coverage gaps from eval #6:** `transcripts="all"` returns ≥1 non-MANE
  transcript; out-of-range `max_distance` → `validation_failed`.
- **Capabilities hash:** stable across two calls; changes when the dict changes.

Keep coverage ≥ 80% (`fail_under`). Tasks/progress exercised with the FastMCP
in-memory client where practical; the live task round-trip is a manual
verification step in the plan, not default CI.

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|
| F5 probe doubles latency on a true `not_found` | Coordinate-input-only, single cache-backed SpliceAI call, `cross_build_check` opt-out, progress note. |
| Native Tasks unstable in FastMCP 3.x against unified host | Progress notifications are independent and ship regardless; Tasks behind a verification gate with documented fallback. |
| `memory://` Docket loses tasks on restart / breaks multi-worker | Acceptable for a research wrapper; document `FASTMCP_DOCKET_URL=redis://…` for scaled deploys. |
| Files crossing 600-LOC budget | New `_combine_shape.py`, `batch.py`, `telemetry.py` absorb growth; re-run `make lint-loc`. |
| `_meta` additions inflate tokens (works against F4) | request_id/timing are tiny scalars; `see_also` collapse + F4 dedup net-reduce tokens. |
| Changing `consequence`/dedup breaks existing parsers | Additive-only where possible; `consequence.aberrations` becomes the *stable* documented path; bump server to 0.2.0 and note in capabilities. |

## 12. Acceptance

- `make ci-local` green (format, lint, lint-loc, typecheck, tests, ≥80% cov).
- All F1–F5 regression tests pass.
- Manual: task-augmented `predict_splicing` returns a `taskId` + retrievable
  result against the running container; normal call unchanged; a cold call shows
  progress notifications; `warmup` then `predict_splicing` shows the second call
  faster; multi-allelic `resolve_variant("rs6025")` chains cleanly.
- Re-run the evaluation method from `docs/mcp-evaluation.md`; expect both axes
  >9/10. Record the new scores in an updated `docs/mcp-evaluation.md` (or a
  dated follow-up).

*Research use only; not for clinical decision support.*
