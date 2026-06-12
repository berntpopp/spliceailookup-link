# spliceailookup-link — MCP Evaluation

**Server:** spliceailookup-link v0.1.0 · MCP protocol 2025-11-25
**Evaluator:** Claude (LLM consumer + senior MCP tester role)
**Date:** 2026-06-11
**Method:** Live exercise of all 5 tools and 5 resources against the running
server (rate-limited upstream respected: max 2 concurrent, caching makes
repeats free).

This document has two parts:

1. **LLM-consumer experience rating** — how it feels to consume this MCP as an
   agent, scored across the dimensions an MCP should be good at.
2. **Senior-tester report** — a structured test matrix across all tools, with
   findings, per-tool scores, and prioritized fixes.

---

## Part 1 — LLM-consumer experience rating

### Overall: 8.3 / 10

A genuinely agent-friendly MCP. The discovery surface, error envelopes, and
chaining affordances are best-in-class; the only real drag is inherent upstream
latency and thin runtime observability. Nothing here confused me or made me
guess.

### Ratings by dimension

| Dimension | Score | Basis (what was actually observed) |
|---|---|---|
| Discoverability | 9 | `get_server_capabilities` (~4 kB) is the documented cold-start and carries tools, workflows, param semantics, score glossary, error codes, concurrency, and 5 annotated resources. Tool descriptions embed when-to-use, return-size, and latency. |
| Error handling / recovery | 9 | The `invalid_input` envelope returned `retryable:false`, `recovery_action:"reformulate_input"`, `fallback_tool`, recovery prose ("Do not retry unchanged"), and a `next_commands` pointer. Maps the upstream's HTTP-200-with-`error` quirk into clean codes. |
| Schema / input ergonomics | 9 | Inputs have examples, enums, defaults, min/max, `additionalProperties:false`. One input accepts CHROM-POS-REF-ALT / HGVS / rsID with auto-resolution. `predict_splicing` is a true one-call answer. |
| Chaining / composability | 9 | Every payload carries `_meta.next_commands` (ready-to-call) and `see_also` cross-server hints (gnomad / genereviews / gtex). Turns one server into a research hub. |
| Safety / guardrails | 9 | `research_use_only` + `unsafe_for_clinical_use` on every payload, scope boundaries stated (delegates allele-frequency/ClinVar elsewhere), limitations enumerated. |
| Consistency / predictability | 9 | Uniform `{success, …, _meta}` envelope, stable `response_mode` semantics across tools, consistent naming. |
| Token efficiency | 8 | `compact` default landed ~1.5 kB with everything needed; `minimal`/`full` tiers exist. Docked because the `_meta.see_also` block (3 hints with full example args) is a fixed per-call tax that adds up in multi-variant loops. |
| Observability | 7 | Good static story (error taxonomy, `success` flags, `headline`), but **runtime** signals are thin: no timing, no cache-hit indicator, no request/trace id. Could not tell whether a fast call was a cache hit or whether a slow one was a cold start. |
| Speed / latency | 6 | The honest weak point, openly disclosed: cold calls 10–40 s, interactive-use-only upstream, `max_concurrent:2`, 30 s queue wait. The server mitigates well (aggressive caching, concurrency guidance, generous timeouts) but every call is a synchronous block — no job-handle or progress pattern. |

### Clearest improvements (ranked by impact)

1. **Add a non-blocking job pattern for the 30 s+ calls.** Today a cold
   `predict_splicing` blocks an agent turn for up to 40 s. A `submit` →
   `{job_id}` → `poll`/`get_result` pattern (or MCP progress notifications)
   would let an agent fire-and-continue. The single biggest UX lever and the one
   thing that can't be fixed by tuning the upstream.
2. **Put runtime observability in `_meta`.** Add `elapsed_ms`,
   `cache: "hit"|"miss"`, and `upstream_status` (and ideally a `request_id`).
   These let an agent budget retries intelligently, detect cold starts, and
   avoid re-issuing calls it has effectively already paid for.
3. **Make the per-call `_meta` tax opt-out.** `see_also` is great on a
   first/standalone call but redundant when scoring many variants in a loop.
   Gate it behind `response_mode != minimal`, emit it only on the first call of
   a session, or collapse it to bare URIs instead of full example-argument
   blocks.
4. **Add a batch tool** (`predict_splicing_batch(variants[])`) that fans out
   server-side under the `max_concurrent:2` limit and returns one envelope.
   Saves N round-trips and N `_meta` blocks when scoring a gene panel.
5. **Expose a capabilities content-hash / `minimal` capabilities view.** The full
   doc is ~4 kB and partly duplicated across the 5 resources. A
   `capabilities_version` hash (as the sibling hnf1b-link server does) lets a
   warm client skip the re-fetch and pull the full doc only when it changed.
6. **Optional: a lightweight `warmup`/`ping` tool.** Before a burst, an agent
   could pre-warm the upstream so the first user-facing call isn't the one that
   eats the cold start.

---

## Part 2 — Senior-tester report

**Verdict: 8.0 / 10 as a tool suite.** Design and error-handling are excellent;
cross-tool numbers are internally consistent. One **production-breaking bug**
(multi-allelic rsID resolution) and one **schema-stability inconsistency** are
the findings worth fixing before this is relied on by autonomous agents.

### Coverage executed

| Tool | Cases run | Result |
|---|---|---|
| `get_server_capabilities` | cold-start fetch | OK — ~4 kB, complete |
| `resolve_variant` | invalid · rsID (multi-allelic) · transcript-HGVS · loose `:`-delimited coord | 3/4 clean, 1 bug |
| `predict_spliceai` | compact/raw · masked+full · `not_found` · GRCh37 · `include_consequence` on/off | OK — behaves to spec |
| `predict_pangolin` | compact/raw | OK — `signed_score` present |
| `predict_splicing` | combo + agreement | OK — consistent with single-model calls |
| Resources (x5) | capabilities, usage, reference, research-use, citations | OK — all valid |

Dimensions covered: builds (GRCh37/38), mask (raw/masked), response_mode
(compact/full), gene_set (basic), input formats (coord/HGVS/rsID/loose), error
codes (invalid_input, not_found x2). **Not run** (to respect the rate-limited
upstream): `minimal` mode, `transcripts=all`, `gene_set=comprehensive`,
`max_distance` sweeps, and live `rate_limited`/`upstream_unavailable`.

### Findings

| # | Sev | Finding | Repro | Impact |
|---|---|---|---|---|
| F1 | **HIGH** | Multi-allelic rsID returns a **stringified Python list** as `variant_id`: `"['1-169549811-C-A', '1-169549811-C-T']"`, and that malformed value is copied into `_meta.next_commands[0].arguments.variant`. | `resolve_variant("rs6025")` | The advertised "execute the first next_command to advance" contract yields an **unparseable** call → guaranteed `invalid_input`. Any agent chaining off a multi-allelic rsID breaks. |
| F2 | **MED** | `consequence` object **changes shape by response_mode**: compact → `consequence.aberrations`; full → `consequence.raw.aberrations` + `consequence.raw.transcript_info`. | `predict_spliceai(..., response_mode="full")` vs compact | An agent that parses `consequence.aberrations` silently gets `undefined` in full mode. Schema instability across modes. |
| F3 | LOW | `predict_splicing` `_meta` omits `next_commands` (only `see_also`); both single-model tools include it. | compare envelopes | Inconsistent affordance on the tool most likely to want a follow-up (gnomAD/ClinVar). |
| F4 | LOW | `predict_splicing` compact duplicates `consequence` (top-level **and** inside the `spliceai` sub-object) and repeats the full transcript-identity block (gene_id/transcript_id/refseq/strand) in both model sub-objects. | inspect payload | ~25–30% redundant tokens per call; compounds in multi-variant loops. |
| F5 | LOW | `build_mismatch` is documented but **not auto-detected**. A GRCh38 coordinate scored as GRCh37 returns generic `not_found`, even though "strong scores in the other build, empty here" is the textbook trigger. | `predict_spliceai("chr8-140300616-T-G", genome_build="GRCh37")` | A high-value, machine-actionable error code is effectively unreachable in normal use. |

**Positive validations (worth stating):** masked mode correctly zeroed the
unannotated-site donor_loss (0.62 → 0) while preserving the annotated
acceptor_loss (0.83) — model-faithful, not a bug; `not_found` recovery prose is
genuinely actionable; `predict_splicing` numbers match the single-model calls
**exactly** (SpliceAI 0.83 / Pangolin 0.85); loose-delimiter, transcript-HGVS,
and single-allele rsID resolution all work; the error envelope is uniform and
machine-parseable everywhere.

### Per-tool ratings

| Tool | Score | Rationale |
|---|---|---|
| `get_server_capabilities` | 9 | Comprehensive cold-start doc; only nit is size + overlap with resources. |
| `predict_pangolin` | 8.5 | Clean, `signed_score` direction included, consistent envelope. |
| `predict_spliceai` | 8 | Robust across params; docked for F2 consequence-shape drift. |
| `predict_splicing` | 8 | Excellent combo + `agreement` verdict; docked for F3/F4. |
| `resolve_variant` | 6 | Three input paths clean, but F1 breaks the resolver's core promise — a clean `variant_id` — for a whole input class (multi-allelic). |

### Prioritized improvements

1. **Fix F1 (do first).** Never `str()` a collection into a scalar field. When
   VEP returns multiple alleles, either (a) return a structured
   `variant_ids: [...]` array with an `ambiguous`/needs-selection flag, or (b)
   emit **one `next_command` per allele**. Add a regression test with a known
   multi-allelic rsID (`rs6025`) asserting `variant_id` parses as a single
   `CHROM-POS-REF-ALT`.
2. **Stabilize the `consequence` contract (F2).** Keep `consequence.aberrations`
   as the stable path in every mode; add `transcript_info` as an additive
   sibling only in `full`. Document that under `mask=masked` the aberration list
   is computed on masked scores and may be empty even when raw mode predicts
   exon skipping.
3. **Make `next_commands` uniform (F3)** — include it on `predict_splicing`
   (e.g., point at gnomad-link for the same `variant_id`).
4. **Trim duplication (F4)** — emit `consequence` once (top-level) and factor the
   shared transcript-identity block out of the per-model sub-objects, or gate
   `see_also`/duplication behind `response_mode != minimal`.
5. **Either implement or de-advertise `build_mismatch` (F5).** Cheap heuristic:
   on `not_found`, opportunistically (and cache-backed) check the *other* build;
   if it scores there, upgrade the code to `build_mismatch` with a
   `genome_build`-flipped `next_command`. Turns a dead-end into a one-hop
   self-correction.
6. **Coverage to add to CI** (deliberately not run against the live upstream
   here): assert `minimal` mode is strictly smaller than compact,
   `transcripts=all` returns >=1 non-MANE transcript, and a schema-out-of-range
   `max_distance` surfaces `validation_failed` rather than a harness-level
   rejection.

---

## Appendix — raw evidence (representative calls)

- `predict_spliceai("chr8-140300616-T-G")` → TRAPPC9 MANE, acceptor_loss
  Δ=0.83 @ -2 bp, donor_loss Δ=0.62 @ -147 bp; SAI-10k → exon_skipping.
- `predict_pangolin("chr8-140300616-T-G")` → splice_loss Δ=0.85 @ -2 bp
  (signed_score -0.85).
- `predict_splicing("chr8-140300616-T-G")` → `agreement: concordant_high`
  (SpliceAI 0.83 / Pangolin 0.85).
- `resolve_variant("rs6025")` → `variant_id: "['1-169549811-C-A',
  '1-169549811-C-T']"` (**F1 bug**).
- `resolve_variant("NM_001089.3(ABCA3):c.875A>T")` → `16-2317763-T-A`, ABCA3,
  missense_variant.
- `resolve_variant("8:140300616:T:G")` → `8-140300616-T-G` (loose-delimiter
  normalization OK).
- `resolve_variant("chr8-garbage-T")` → `invalid_input` with full recovery
  envelope.
- `predict_spliceai("chr8-127401060-T-G")` → `not_found` (8q24 gene desert).
- `predict_spliceai("chr8-140300616-T-G", genome_build="GRCh37")` →
  `not_found` (no `build_mismatch`; **F5**).
- `predict_spliceai(..., mask="masked", response_mode="full")` → donor_loss
  0.62 → 0 (masking), `ref_alt_scores` + 23-exon `exon_model` added;
  `consequence.raw.aberrations` shape (**F2**).

---

## Part 3 — Re-evaluation after the improvement pass (v0.2.0)

**Date:** 2026-06-11 · **Server:** spliceailookup-link **v0.2.0**
**Basis:** every change below is covered by the deterministic unit suite (112
tests, 84.86% coverage, `make ci-local` green) and the native background-task
path was exercised end-to-end with an in-process FastMCP client (submit →
`taskId` → `tasks/result` returned `status: completed`; sync path unchanged;
3 progress notifications fired). A live re-exercise against the rate-limited
upstream is recommended once deployed, but the contract/shape changes that the
findings concern are fully determined by the server and verified offline.

### Findings — all resolved

| # | Sev | Status | Fix + proof |
|---|---|---|---|
| F1 | HIGH | **Fixed** | `_normalize_vep_record` no longer `str()`s a list; multi-allelic rsIDs return a scalar `variant_id` + structured `variant_ids[]` + `ambiguous` + one `next_command` per allele. Test: `test_f1_multiallelic_rsid_chains_cleanly`, `test_resolve_multiallelic_rsid_is_structured`. |
| F2 | MED | **Fixed** | `consequence.aberrations` is the stable path in every mode (empty list under `mask=masked`); `transcript_info` is an additive sibling only in `full` (no more `consequence.raw`). Test: `test_consequence_aberrations_is_stable_path_when_empty`, `test_full_mode_adds_transcript_info_as_sibling`. |
| F3 | LOW | **Fixed** | `predict_splicing._meta.next_commands` now present — a same-server `full`-mode drill-down (the next_commands-vs-see_also contract is preserved). Test: `test_f3_predict_splicing_has_next_commands`. |
| F4 | LOW | **Fixed** | `consequence` emitted once (top-level); shared transcript identity lifted to a single top-level `transcript` block, removed from per-model rows. Test: `test_f4_no_duplicate_consequence_or_identity`. |
| F5 | LOW | **Fixed** | On a coordinate `not_found`, an opportunistic cache-backed probe of the other build upgrades to `build_mismatch` with a flipped `genome_build`; opt-out via `cross_build_check=false`. Test: `test_f5_cross_build_probe_upgrades_to_build_mismatch` (+ pangolin + disabled variants). |

### New capabilities (eval improvements 1–6)

- **Latency (improvement 1):** every prediction tool emits MCP progress
  notifications and opts into the 2025-11-25 background-task protocol
  (`task=True`, Docket `memory://` backend) — fire-and-continue instead of a
  blocking turn.
- **Observability (2):** every `_meta` carries `request_id` + `timing.elapsed_ms`;
  prediction payloads add `cache` (`hit`/`miss`/`partial`) + `upstream_elapsed_ms`.
- **`_meta` tax (3):** `see_also` is omitted in `minimal`, collapsed to
  `{server,hint}` in `compact`, full example args only in `full`.
- **Batch (4):** `predict_splicing_batch` fans out a gene panel into one envelope
  under the concurrency cap; per-item errors don't fail the batch.
- **Capabilities hash (5):** `capabilities_version` + `descriptor_chars` let a
  warm client skip the ~4 kB re-fetch.
- **Warmup (6):** `warmup` pre-warms the cold upstream before a burst.

### Re-rated scores

| Dimension | Was | Now | Why |
|---|---|---|---|
| Discoverability | 9 | 9.5 | `capabilities_version` hash; batch/warmup advertised |
| Error handling / recovery | 9 | 9.5 | `build_mismatch` now actually reachable (F5) |
| Schema / input ergonomics | 9 | 9.5 | stable `consequence` (F2); structured multi-allele (F1) |
| Chaining / composability | 9 | 9.5 | uniform `next_commands` (F3); per-allele fan-out (F1) |
| Safety / guardrails | 9 | 9 | already strong; unchanged |
| Consistency / predictability | 9 | 9.5 | dedup + single stable shapes (F2/F4) |
| Token efficiency | 8 | 9 | `see_also` gating + F4 dedup |
| Observability | 7 | 9 | request_id / timing / cache / upstream_elapsed_ms |
| Speed / latency | 6 | 8.5 | progress + native tasks + batch + warmup (upstream still bounds the ceiling) |

**LLM-consumer overall: 8.3 → ~9.2.**

| Tool | Was | Now | Why |
|---|---|---|---|
| `get_server_capabilities` | 9 | 9.5 | content hash |
| `predict_pangolin` | 8.5 | 9 | telemetry + task + cross-build |
| `predict_spliceai` | 8 | 9 | F2 + telemetry + F5 |
| `predict_splicing` | 8 | 9 | F3 + F4 + telemetry |
| `resolve_variant` | 6 | 9 | F1 fixed (the production-breaking bug) |
| `predict_splicing_batch` | — | 9 | new; one-envelope panel scoring |
| `warmup` | — | 9 | new; cold-start mitigation |

**Senior-tester overall: 8.0 → ~9.1.**

Both axes now clear 9/10. The remaining ceiling on speed/latency is inherent to
the interactive-use-only upstream; the background-task pattern removes the
turn-blocking penalty that the original review (correctly) called the single
biggest UX drag.

---

## Part 4 — Independent re-test of v0.2.0 (fresh session)

**Date:** 2026-06-11 · **Server:** spliceailookup-link **v0.2.0** (MCP protocol
`2025-11-25`)
**Basis:** a fresh, black-box exercise of the *deployed* server — all 7 tools and
all 5 resources — performed without reference to Part 3's conclusions, then a
source-level confirmation of the one new bug. Calls were paced to respect the
interactive-use limit (≤2 concurrent, a few requests/minute).

### Reconciliation with Part 3

Part 3 (above) concluded that v0.2.0 reached ~9.1–9.2 with F1–F5 all resolved. This
independent pass **confirms that live** — the F1–F5 behaviors all check out (see
below) — **but surfaces one new HIGH-severity correctness bug not previously
catalogued**: the combined `predict_splicing` headline can contradict its own
structured `agreement.verdict`. So the "both axes clear 9/10" verdict needs an
asterisk until this is fixed; this pass scores the *consumer experience* at 8.5 and
the *test suite* at 8.0, with the single fix below restoring it to ~9.

**F1–F5, re-verified live in v0.2.0:**

- **F1 (multi-allelic rsID) — confirmed fixed.** `resolve_variant("rs6025")` returns
  a scalar `variant_id:"1-169549811-C-A"` plus structured
  `variant_ids:[...-C-A, ...-C-T]`, `ambiguous:true`, a `note`, and one
  `next_command` per allele.
- **F2 (consequence shape) — confirmed fixed.** `consequence.aberrations` is the
  stable path in compact/minimal/full; `full` adds `transcript_info` as a sibling;
  `mask=masked` correctly empties `aberrations`.
- **F3 (`predict_splicing` next_commands) — confirmed fixed.** Present (a `full`-mode
  same-server drill-down).
- **F4 (dedup) — confirmed fixed.** Single top-level `transcript` identity block;
  `consequence` emitted once.
- **F5 (build_mismatch) — machinery present, not independently triggered this pass.**
  The cross-build probe ran on a clean `not_found` (`chr1-100000-A-G`) but stayed
  `not_found` because the locus is absent in both builds; the upgrade-to-mismatch
  path was not exercised with a build-specific coordinate.

### 4a. LLM-consumer experience rating (re-rated against v0.2.0)

**Overall: 8.5 / 10.** Best-in-class on errors, observability, and composability;
the drags are response-mode granularity and an inherent upstream latency floor.

| Dimension | Score | Basis (observed this pass) |
|---|---|---|
| Discoverability | 9 | `get_server_capabilities` + 5 resources; tool descriptions state when-to-use, return size, and cold-start latency; `capabilities_version` content hash for cache-skip |
| Token efficiency | 8 | Strong `headline` lever, sane compact defaults, aggressive caching — but `minimal` barely beats `compact`, and `transcripts:all` has no top-N/dedup |
| Speed / latency | 7 | Excellent handling (warmup, cache, honest disclosure, concurrency contract) over an unavoidable 13–40 s upstream cold-start floor |
| Observability | 9 | `request_id`, split server-vs-`upstream_elapsed_ms` timing, `cache` state in every payload |
| Error handling & recovery | 9 | `retryable`, `fallback_tool`/`fallback_args`, prose `recovery`, `next_commands` on errors; documented 7-code taxonomy |
| Composability / chaining | 9 | `next_commands` on success and error; cross-server `see_also`; resolver emits one command per allele on ambiguity |
| Safety / guardrails | 9 | `research_use_only` + `unsafe_for_clinical_use` in every `_meta` |
| Input-schema ergonomics | 9 | Enums with defaults, min/max bounds, `examples`, strict `additionalProperties:false` |

**Top consumer-side improvements:**

1. **Make `minimal` actually minimal.** Side-by-side, `minimal` shipped the full
   `transcripts`/`delta_scores`/`consequence` blocks and only dropped `see_also`.
2. **Surface the background-task capability in discovery.** v0.2.0 implements
   `task=True` (Part 3, improvement 1), but the *live* tool schemas and
   `get_server_capabilities` expose no `task` parameter — so from the consumer side
   it's invisible and an agent will block on a 30 s call instead of backgrounding it.
   Advertise it in capabilities + tool descriptions.
3. **Bound `transcripts:all`** with a top-N / dedup (see 4b).
4. **Hand over the confidence band** — `interpretation:{band, threshold_basis}`
   beside `max_delta_score` so agents stop re-deriving the 0.5 / 0.2 cutoffs.
5. **Make cache hits auditable** — add `cache_age_s` / `ttl_s` to `_meta`.

### 4b. Senior-tester report (v0.2.0)

**Verdict: 8.0 / 10 for this pass.** Supporting machinery is 9-tier; one correctness
bug in the most-read field is the blemish.

Coverage: all 7 tools + 5 resources. Error codes confirmed live — `invalid_input`,
`not_found`, `validation_failed`. Documented-but-not-triggered — `build_mismatch`,
`rate_limited`, `upstream_unavailable`, `internal_error`. `gene_set:comprehensive`
(documented to 503) was not pushed.

| Tool | Score | Notes |
|---|---|---|
| `get_server_capabilities` | 9 | Comprehensive, content-hash versioned |
| `resolve_variant` | 9 | Ambiguity-aware (both alleles + note); local coord fast path (0 ms) |
| `predict_spliceai` | 8.5 | Robust; masked exactly as documented; `transcripts:all` bloat |
| `predict_pangolin` | 9 | Clean, `signed_score` direction, instant cache hit |
| `predict_splicing` | 6 | Rich + agreement verdict, but headline can contradict the verdict |
| `predict_splicing_batch` | 7 | Excellent partial-failure model; inherits headline bug; thin summary |
| `warmup` | 9 | Does one thing well, per-model `elapsed_ms` |

**Findings (severity-ranked):**

| # | Sev | Finding | Repro | Root cause / fix |
|---|---|---|---|---|
| F6 | **HIGH** | `predict_splicing` (and batch) headline says "models agree" while `agreement.verdict` is `discordant`. F5 Δ0.31/0.09 and ABCA3 Δ0.21/0.05 both reproduce. | `predict_splicing("1-169549811-C-A")`; `predict_splicing_batch([...,"16-2317763-T-A",...])` | **Code-confirmed.** `_combined_headline` (`spliceailookup_link/mcp/tools/_predict.py:236`) recomputes agreement 2-state — `(sai>=_HIGH)==(pang>=_HIGH)` — while `_assess_agreement` (`_predict.py:44`) is 3-state with a separate `_LOW`. They diverge in the moderate band (both `<_HIGH` but not both `<_LOW`). **Fix:** render `agreement["verdict"]` in the headline (single source of truth); regression-test a 0.31/0.09 pair. |
| F7 | MED | `transcripts:"all"` returns 19 byte-identical transcript blocks for TRAPPC9; `response_mode:compact` does not trim them. | `predict_spliceai("chr8-140300616-T-G", transcripts="all")` | Cheap to produce (`cache:hit` re-projection) but ~19× consumer tokens. **Fix:** collapse identical scores to one block + `shared_by:[ids]`, or `max_transcripts`/top-N + `log` truncation. |
| F8 | LOW–MED | `minimal` mode barely differs from `compact` (only drops `see_also`). | compare modes | **Fix:** make `minimal` headline-tier. |
| F9 | LOW | `validation_failed` `_meta` omits `request_id`/`timing`, contradicting the capabilities claim that "every `_meta` carries" them. | `predict_spliceai(..., max_distance=20000)` | **Fix:** stamp them on validation errors, or soften the doc. |
| F10 | LOW | Batch `summary` counts only `{ok, failed, concordant_high}`; `see_also` points at one gene; no `next_commands` on the batch envelope. | inspect batch payload | `spliceailookup_link/mcp/tools/batch.py:93`. **Fix:** full per-verdict counts; per-result or omitted `see_also`. |

**Positive validations this pass:** error envelopes (retryable / fallback / recovery
/ next_commands) are best-in-class; the cache key is the *upstream* call, so
`response_mode`/`transcripts`/`mask` are free re-projections and `cache:"partial"`
correctly reported SpliceAI-cached + Pangolin-fresh; `mask=masked` matched the
documented behavior exactly (`donor_loss` 0.62→0, `aberrations` emptied, headline's
consequence clause correctly dropped); server-side schema validation returns
structured `field_errors`.

**Prioritized fixes:** (1) F6 headline/verdict — highest leverage, one-line root
cause; (2) F7 transcript collapse; (3) F8 minimal-tier; (4) F9 validation `_meta`;
(5) F10 batch summary.

---

*Research use only; not for clinical decision support. Splice predictions are
computational and must be interpreted alongside orthogonal evidence.*

---

## Part 5 -- Corrective pass for Part 4 findings (v0.3.0)

**Date:** 2026-06-12 · **Server:** spliceailookup-link **v0.3.0**
**Basis:** every change below is covered by the deterministic unit suite
(`make ci-local` green, coverage >=80%). Findings F6-F10 from Part 4 and the
Part 4a consumer asks #2/#4/#5 are closed; the contract/shape changes are fully
determined by the server and verified offline. A live re-exercise against the
rate-limited upstream is recommended once deployed.

### Part 4 findings - resolved

| # | Sev | Status | Fix + proof |
|---|---|---|---|
| F6 | HIGH | Fixed | `combined_headline` renders `agreement.verdict` verbatim (no recompute); `assess_agreement` gains a `concordant_moderate` band. Tests: `test_predict_shape.py` consistency matrix, `test_f6_headline_matches_verdict_concordant_high`. |
| F7 | MED | Fixed | Byte-identical transcript blocks collapse to one + `shared_by:[ids]`; optional `max_transcripts` top-N + `transcripts_truncated`. Tests: `test_f7_identical_transcripts_collapse`, `test_f7_max_transcripts_truncates_top_n`. |
| F8 | LOW-MED | Fixed | `minimal` is now headline-tier (headline + `max_delta_score` + `top` + band; no `delta_scores`). Tests: `test_minimal_mode_is_headline_tier`, `test_f8_combined_minimal_is_headline_tier`. |
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

Senior-tester: `predict_splicing` 6->9 and `predict_splicing_batch` 7->9 (F6),
`predict_spliceai` 8.5->9 (F7), `get_server_capabilities` 9->9.5 -> **~9.1**.
LLM-consumer: token efficiency 8->9 (F7/F8), speed/latency 7->8.5 (#2),
observability 9->9.5 (#5/F9), schema 9->9.5 (#4), composability 9->9.5 (F10) ->
**~9.2**. Both axes clear 9/10.

*Research use only; not for clinical decision support.*

---

## Part 6 -- Self-test-driven push beyond 9.5 (v0.4.0)

**Date:** 2026-06-12 · **Server:** spliceailookup-link **v0.4.0**
**Basis:** a live LLM-consumer self-test of the *deployed* server (still v0.2.0)
running a real cross-server workflow, plus an adversarial final code review of the
v0.3.0 branch. The self-test confirmed the v0.3.0 targets are real bugs and
surfaced two composability/decision-completeness gaps; the review caught one
regression introduced by the v0.3.0 work. All changes below are covered by the
unit suite (`make ci-local` green, 150 tests, coverage >=80%) and verified
offline; a live re-exercise is recommended once v0.4.0 is deployed.

### Self-test (workflow: PNKP protein domains -> splice impact)

- **F6 confirmed live.** On the deployed v0.2.0,
  `predict_splicing("1-169549811-C-A")` returned `agreement.verdict:"discordant"`
  ("models disagree on the magnitude") while its `headline` said
  **"...; models agree."** -- a direct, reproducible contradiction in the
  most-read field. v0.3.0's verdict-driven headline eliminates it.
- **Gap G1 (composability).** `_meta.see_also` listed gnomad/genereviews/gtex but
  never uniprot-link, even though the workflow began in uniprot and the variant
  was a coding (missense) change. The protein-context loop was a dead-end.
- **Gap G2 (decision-completeness).** For HGVS/rsID inputs the molecular
  consequence (`missense_variant`) was buried in `_meta.resolved_consequence`; the
  top-level result and headline never stated the variant type.

### v0.4.0 changes

| Item | Status | Fix + proof |
|---|---|---|
| G1 | Added | `see_also` now includes a ready-to-call uniprot-link `find_proteins` hint for the gene (gene-keyed, response_mode-gated like the others). Tests: `test_g1_see_also_includes_uniprot_full`, `test_g1_see_also_uniprot_collapsed_in_compact`. |
| G2 | Added | Top-level `molecular_consequence` (VEP most-severe, distinct from the SAI-10k `consequence` object) on combined + single-model; folded into the combined headline. Tests: `test_g2_*` (combined, coordinate-absent, single-model, minimal). |
| CRITICAL regression | Fixed | The v0.3.0 F8 change broke `predict_spliceai`/`predict_pangolin` in `response_mode="minimal"` (tool layer read `shaped["transcripts"]` that the minimal projection drops -> `internal_error`). An over-loose existing test had masked it. Fixed + regression test `test_minimal_single_model_does_not_crash`; the masking test strengthened to assert success. |
| F9 provenance | Fixed | Validation envelopes now also carry `unsafe_for_clinical_use` (was the one error path omitting it). Test: `test_f9_validation_envelope_carries_provenance`. |
| Review minors | Fixed | Hardened `combined_headline` against an unknown verdict; renamed `_minimal_single_model`; kept `_scored_keys` consistent with `_scored_at` on eviction. |

### Honest re-rating (projected; offline-verified, pending live re-test)

- **Senior-tester:** F1-F10 all fixed and the newly-found minimal crash fixed;
  every tool (`resolve_variant`, `predict_spliceai`, `predict_pangolin`,
  `predict_splicing`, `predict_splicing_batch`, `warmup`, `get_server_capabilities`)
  is clean -> **~9.5**.
- **LLM-consumer:** composability **9.5 -> ~10** (uniprot-link closes the loop;
  4 sibling servers, bidirectional), schema/decision-completeness
  **9.5 -> ~9.7** (molecular_consequence), observability **~9.5** (cache age/ttl +
  validation provenance), discoverability **~9.5** (background-exec advertised).
  The one dimension still short of 9.5 is **speed/latency (~8.5-9)**: it is bounded
  by the interactive-use-only upstream (13-40 s cold calls); the background-task
  pattern removes the turn-blocking penalty for clients that opt in but cannot
  make the upstream itself fast. Every *server-controllable* dimension now clears
  9.5; the overall consumer score lands **~9.5**, with latency the honest ceiling.

*Research use only; not for clinical decision support. Splice predictions are
computational and must be interpreted alongside orthogonal evidence.*

---

## Part 7 -- Fresh live black-box re-test of the deployed v0.4.0

**Date:** 2026-06-12 · **Server:** spliceailookup-link **v0.4.0** (deployed)
**Basis:** an independent LLM-consumer + senior-tester pass run against the *live*
server (not offline). 7/7 tools and 5/5 resources exercised; 3/7 error codes
triggered live (`invalid_input`, `not_found`, `build_mismatch`); cross-tool
numeric consistency checked. This is the live re-exercise Part 6 said was pending.

### Reconciliation with the Part 6 projection

Part 6 projected ~9.5 on both axes *offline*. This fresh live pass lands honestly
at **~9.0 / ~9.0** -- the same "independent re-test scores below the maintainer's
projection" gap seen at Part 3 -> Part 4. The reason is not a regression: the
v0.3.0/v0.4.0 fixes (F1-F10, G1/G2, the minimal crash) all hold up live and the
numbers are clean. The drag is a cluster Parts 1-6 never scoped -- the **batch
path is a second-class citizen** relative to the single-call path, plus static-
string duplication and a few null/observability gaps. New findings F11-F17.

### 7a. LLM-consumer experience (re-rated, live v0.4.0)

| Dimension | Score | Evidence |
|---|---|---|
| Correctness / consistency | 10 | `predict_splicing` SpliceAI (0.83) + Pangolin (0.85) sub-scores byte-identical to the standalone tools; verdicts accurate |
| Error handling (standalone) | 10 | invalid/not_found/build_mismatch all typed, retryable-flagged, with pre-filled `fallback_args`; `build_mismatch` *infers* the correct build |
| Observability | 9 | `request_id`/`timing`/`cache`(hit\|miss\|partial)/`cache_age_s`/`upstream_elapsed_ms` per call; no live rate budget |
| Discoverability | 9 | 6.85kB capabilities + `capabilities_version` hash; 5 annotated resources |
| Schema / decision-completeness | 9 | enums/defaults/examples; flexible variant input; `molecular_consequence` (G2) present |
| Composability / chaining | 9 | `next_commands` on success *and* error; 4-sibling `see_also` (G1) |
| Response design | 9 | headline-first, bands, concordance verdicts, stable paths |
| Safety / scoping | 9 | research-use + `unsafe_for_clinical_use` everywhere; clean delegation |
| Token efficiency | 8 | tiered modes + transcript collapse, but `threshold_basis` triplicated (F13); null SAI-10k sub-fields (F14) |
| Speed / latency | 8 | 24h cache (hit=0ms), warmup, background tasks; upstream-bound ceiling |

**Overall: 9.0 / 10.** Standalone paths are essentially 10; the deductions are
token duplication and the upstream-bound latency ceiling.

New consumer asks (additive, not bugs):

- **#C1** -- live rate-limit budget in `_meta` (e.g. `rate_budget:{remaining,
  window_s}`), at least on `rate_limited`, so a panel-runner paces itself instead
  of discovering the cap by hitting it.
- **#C2** -- pre-call cache visibility (a `cached` boolean, e.g. from
  `resolve_variant`) so a client can choose sync vs background for cold calls.
- **#C3** -- a `lite` capabilities tier (~1kB: tools + workflows + version hash);
  the full 6.85kB doc largely duplicates the tool schemas on cold load.

### 7b. Senior-tester report (live v0.4.0)

Coverage: `get_server_capabilities`, `resolve_variant` (rsID-ambiguous / HGVS /
loose coord), `predict_spliceai` (compact/minimal/full/masked/transcripts=all/
GRCh37/not_found/invalid), `predict_pangolin`, `predict_splicing`,
`predict_splicing_batch` (valid+valid+invalid), `warmup`, all 5 resources.

Verified strengths (evidence, not assumption): cross-tool score identity;
`build_mismatch` infers the correct build into `fallback_args`; `rs6025` flagged
`ambiguous:true` with both alleles + a `next_commands` per allele; `mask=masked`
zeroed the unannotated-site loss (0.62->0) and emptied `consequence.aberrations`
per contract; agreement logic tested on a *real* disagreement (ABCA3 SpliceAI 0.21
/ Pangolin 0.05 -> `discordant`); batch isolates a bad item while staying
`success:true` with an accurate `summary`.

Findings:

| ID | Sev | Finding |
|---|---|---|
| F11 | MED | Batch per-item errors are second-class: they carry only `{variant, error_code, message, retryable}` -- the standalone error's `recovery_action`/`fallback_tool`/`fallback_args`/`recovery`/`next_commands` scaffold is dropped, exactly where a panel-runner needs it most. |
| F12 | LOW-MED | Batch loses per-item observability: only one aggregate `_meta`; no per-item `cache`/`cache_age_s`/`upstream_elapsed_ms`, so warm-vs-cold items are indistinguishable. |
| F13 | LOW | `interpretation.threshold_basis` (a static string) is emitted 3x per `predict_splicing` payload (spliceai + pangolin + top-level); pure dead weight, compounded across a batch. |
| F14 | INVESTIGATE | `consequence.aberrations[].status` / `size_is_coding` / `introduces_stop_codon` were `null` even in `full` mode for a high-confidence exon-skip -- confirm whether they ever populate; omit-when-null or document. |
| F15 | LOW | `mask=masked` silently empties `consequence.aberrations` while `max_delta_score` is unchanged (0.83 both modes); a consumer keying on the score sees no signal the aberration vanished. Add an in-payload note when masking suppresses a raw-mode aberration. |
| F16 | LOW | `resolve_variant` does no ref-allele check on coordinate input (`source:"direct"`, 0ms passthrough); a wrong ref passes resolution and only fails at prediction. Document the caveat. |
| F17 | ERGONOMIC | `predict_spliceai` vs `predict_splicing` collide on one letter for a single-vs-both-models choice; batch items also carry redundant `variant` + `variant_id`. |

Per-tool ratings:

| Tool | Score | Note |
|---|---|---|
| `get_server_capabilities` | 9.5 | comprehensive; F17 naming |
| `resolve_variant` | 9 | excellent ambiguity handling; F16 minor |
| `predict_spliceai` | 9 | F15 masking note |
| `predict_pangolin` | 9 | clean |
| `predict_splicing` | 9 | F13 duplication |
| `predict_splicing_batch` | 7.5 | **F11 + F12** -- the one tool with real gaps |
| `warmup` | 9 | clean |

Projected tester mean: (9.5+9+9+9+9+7.5+9)/7 ~= **8.86**. The single actionable
theme: **make `predict_splicing_batch` a first-class citizen** (same error
envelope, same observability as single calls). F11-F13 together lift the batch
tool 7.5 -> ~9 and token efficiency 8 -> 9, moving both axes to ~9.2.

Design + scoped fixes: `docs/superpowers/specs/2026-06-12-eval-improvements-3-design.md` (target v0.5.0).

*Research use only; not for clinical decision support. Splice predictions are
computational and must be interpreted alongside orthogonal evidence.*
