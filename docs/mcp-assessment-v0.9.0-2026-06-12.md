# SpliceAI Lookup Link — MCP Assessment (v0.9.0)

**Date:** 2026-06-12
**Server under test:** `spliceailookup-link` v0.9.0 (MCP protocol `2025-11-25`, capabilities hash `6a34cf01836d`, descriptor 13,139 chars)
**Author:** Claude (Fable 5), acting first as an LLM consumer of the MCP, then as a senior MCP tester
**Method:** Live black-box testing through the MCP facade. All 7 tools and the 5 resources were exercised across happy paths, every meaningful parameter axis, the resolver, batch, and deliberate error/edge probes. Evidence (request IDs, timings, scores) is captured verbatim from responses. Upstream is interactive-use-only and rate-limited (2 concurrent, calls 10–53s cold), so calls were paced in small waves and leaned on the 24h cache where possible.

> Research-use-only server. Every payload carries `unsafe_for_clinical_use`. Nothing in this report is clinical guidance.

This document collects two assessments produced in sequence:

1. **Part 1 — LLM-consumer UX evaluation:** a dimension-by-dimension rating of the server as experienced by an LLM client.
2. **Part 2 — Senior-tester test report:** a structured campaign across all 7 tools with error-code coverage, findings ranked by severity, and prioritized change recommendations.

> Related prior docs in this folder: `mcp-assessment-v0.7.0-2026-06-12.md`, `mcp-assessment-v0.8.0-2026-06-12.md`, `mcp-consumer-assessment-2026-06-12.md`, `mcp-tester-report-2026-06-12.md`.

---

# Part 1 — LLM-Consumer UX Evaluation

Grounded in a cold prediction (53s), a cache hit (303ms), both capabilities detail levels, an rsID resolution, an invalid input, and a wrong-REF coordinate.

## Overall: 9 / 10

A reference-quality MCP — clearly designed *for* LLM consumption, not just exposed as a thin API wrapper. The only hard ceiling is upstream latency, which the server mitigates about as well as is possible.

## Per-dimension scores

| Dimension | Score | Basis (observed) |
|---|---:|---|
| Discoverability | 9 | `get_server_capabilities` (`lean` 1ms / `full`), 5 annotated resources, workflow recipes, `next_commands` + cross-server `see_also` on every response, "which tool?" disambiguation baked in |
| Error handling | 9 | Structured envelope (`error_code`, `retryable`, `recovery_action`, ready-to-call `fallback_tool`/`fallback_args`, prose `recovery`), 10 documented codes, pre-flight REF check, ambiguity surfaced not guessed |
| Observability | 9 | `request_id` per call (+ per-item in batch), `timing.elapsed_ms` + `upstream_elapsed_ms`, `cache: miss/partial/hit`, `served_warm`, `rate_budget`, `cache_ttl_s`/`cache_age_s`, `capabilities_version` hash |
| Ergonomics | 9 | Accepts coords/HGVS/rsID/loose delimiters, auto-resolves, sane defaults (GRCh38/MANE/basic/raw), `headline` one-liner, two-model agreement verdict |
| Safety | 9 | `unsafe_for_clinical_use` + `research_use_only` on every payload, dedicated research-use + citations resources, explicit out-of-scope delegation to sibling servers |
| Token efficiency | 8 | 3 response modes (`minimal`≈0.5kB / `compact` / `full`), `detail=lean`, `include_hints`/`include_see_also` toggles, `params_by_reference`. Loses a point on verbose-by-default `_meta` |
| Speed | 7 | Cache (303ms hit vs 53s cold), `warmup`, `predict_splicing_batch`, background-task support, 0ms fail-fast — but cold upstream is inherently 13–53s, rate-limited to 2 concurrent |

## Strengths that stood out

- **Error handling is best-in-class.** The invalid input returned in **0ms** (local, before any upstream call) with `retryable:false`, a prose `recovery`, and a pre-filled `fallback_tool: resolve_variant` + `fallback_args`.
- **It refuses to guess.** `rs6025` (Factor V Leiden) resolved to `ambiguous` with both alleles (`1-169549811-C-A` and `-C-T`) and a `next_command` for each, rather than silently picking one.
- **Caching + observability together.** Re-querying the variant returned `cache:"partial"`, `served_warm:true` in 303ms, and exposed exactly which half was warm. The `capabilities_version` content hash lets a warm client skip re-fetching the descriptor.

## Improvements (Part 1 view)

1. **Add MCP tool annotations** (`readOnlyHint:true`, `idempotentHint:true`, `openWorldHint:true`). Absent from all 7 schemas; all tools are read-only. Single biggest UX win available — lets clients auto-allow and cuts permission friction.
2. **Emit model/version provenance** in prediction payloads (SpliceAI/Pangolin model versions, GENCODE/MANE release, Ensembl VEP release) — currently only `server_version` + `capabilities_version`.
3. **Trim the hot-path `_meta`.** `capabilities_version` is echoed on nearly every prediction response; drop it there or push the "set `include_hints=false` after the first call" guidance into the headline so multi-call workflows don't pay the hint token tax repeatedly.
4. **Make `warmup` cover more in one shot** (both masks / a stay-warm TTL estimate) — it currently warms a single `(gene_set, mask)` path per model and warmth decays silently.
5. **Accept an optional client-supplied correlation ID** echoed into `_meta` so a multi-step workflow can be traced as one unit.

The speed score (7) is almost entirely the Broad Cloud Run cold-start + interactive-only rate limit; the server's mitigations (cache, warmup, batch, background tasks, fail-fast) are near the realistic ceiling.

---

# Part 2 — Senior-Tester Test Report

## 1. Test coverage

**Tools exercised: 7 / 7.** ~20 live calls.

| Tool | Variations tested | Verdict |
|---|---|---|
| `get_server_capabilities` | `lean` (1ms), `full` (~13kB, 0ms) | ✅ Pass |
| `resolve_variant` | rsID (ambiguous), transcript HGVS, wrong-REF coord, GRCh37 build | ✅ Pass |
| `predict_spliceai` | compact, `mask=masked`, `transcripts=all`, `max_distance=10000`, `include_hints=false` | ✅ Pass |
| `predict_pangolin` | `response_mode=full` (cache hit, per-position grid) | ✅ Pass |
| `predict_splicing` | minimal, GRCh37 cross-build, rsID auto-resolve | ✅ Pass |
| `predict_splicing_batch` | 5-item mixed (cached/fresh/HGVS/2 errors) | ✅ Pass (1 efficiency finding) |
| `warmup` | GRCh38/raw (both models warmed) | ✅ Pass |

**Parameter axes covered:** `genome_build` (GRCh38 + GRCh37), `max_distance` (500 default + 10000 upper bound), `mask` (raw + masked), `transcripts` (mane + all, collapse verified), `response_mode` (minimal + compact + full), `include_hints=false`, `cross_build_check` (via `other_build_hint`), `check_ref` (default, `ref_warning`). **Not tested:** `gene_set=comprehensive` (deliberately — it can 503; repo guidance is to keep load off the upstream).

**Error codes triggered (4 / 10):** `invalid_input` (parse + out-of-range), `ambiguous`, `ref_mismatch` (+ `other_build_hint`), `unsupported_contig`. All returned in **0ms** (local, pre-upstream) with a structured envelope, prose `recovery`, and pre-filled `fallback_tool`/`fallback_args`.

**Not triggered (documented, with reasons):**
- `not_found` — the intergenic candidate (rs6983267, 8q24) overlapped a MANE **lncRNA** (CCAT2) and scored `concordant_low` (see Finding 4).
- `build_mismatch` — the server prefers the more precise `ref_mismatch` + `other_build_hint` when the same coordinate's REF matches the other build.
- `rate_limited` / `validation_failed` / `upstream_unavailable` / `internal_error` — would require saturating the rate-limited upstream or inducing a fault; behavior is well specified in `spliceailookup://reference`. Not worth burning the interactive-only budget.

## 2. Per-tool evaluation

- **`get_server_capabilities`** — Best-in-class. `lean`/`full` split is the right ergonomic; `full` documents batch semantics, the 55s soft deadline, background-task eligibility, hint lifecycle, transcript collapse, and Ensembl ID normalization. Self-documenting to the point an LLM rarely needs anything else.
- **`resolve_variant`** — Excellent. Handles rsID / transcript-HGVS / loose coords, validates REF pre-flight, refuses to guess on ambiguity, carries `molecular_consequence`. Correctly strand-flipped `c.875A>T` → genomic `T>A`. GRCh37 resolution returned the distinct coordinate (`16-2367764` vs GRCh38 `16-2317763`).
- **`predict_spliceai`** — Correct and well-shaped. `mask=masked` zeroed donor_loss (0.62→0) and emptied `aberrations` **with a note pointing back to raw** — a standout LLM affordance. `transcripts=all` collapsed 18 identical transcripts into one block + a `shared_by` list of 17 IDs.
- **`predict_pangolin`** — Correct; `full` adds `ref_alt_scores` and the per-position `all_non_zero_scores` grid with signed loss direction.
- **`predict_splicing`** — The right default. Two independent models + an `agreement` verdict (`concordant_high`, `discordant_subthreshold`, `concordant_low` all observed) + SAI-10k consequence + a quotable `headline`.
- **`predict_splicing_batch`** — Robust. Per-item errors don't sink the envelope; `summary` splits `terminal_failed` vs `retryable_failed`, tallies verdicts, and `summary_top_variant` surfaces the strongest hit. Per-item `request_id` + cache state present.
- **`warmup`** — Does what it says; returns per-model `elapsed_ms` (SpliceAI 827ms, Pangolin 487ms) + coverage, and is honest about Cloud Run decay.

## 3. Findings

Only one functional inefficiency found; everything else behaved per spec.

1. **[Medium] No intra-batch dedup by resolved variant_id.** The same variant submitted twice — once as `16-2317763-T-A`, once as its HGVS `NM_001089.3(ABCA3):c.875A>T` — both scored as `cache: miss` with separate upstream calls (309ms + 202ms). Upstream calls are the scarce, rate-limited, 10–40s resource; a batch should resolve all inputs, dedup by canonical `variant_id`, score the unique set, then re-expand results to original positions.
2. **[Low] No model/version provenance in result payloads.** Responses carry `server_version` + `capabilities_version` but not the *scientific* versions (SpliceAI/Pangolin model, GENCODE/MANE release, Ensembl VEP release). For citable research output these belong in the result, not just the `data_sources` names in capabilities.
3. **[Low] MCP tool annotations still absent.** None of the 7 schemas expose `readOnlyHint` / `idempotentHint` / `openWorldHint`. All 7 are read-only; advertising that lets clients auto-allow and cuts permission friction.
4. **[Low / Docs] `basic` gene set includes MANE lncRNA, not just protein-coding.** rs6983267 returned a CCAT2 (lncRNA) transcript. Capabilities call it "MANE/curated" (accurate but easy to misread); one clarifying line would prevent a consumer mistaking a low-scoring lncRNA hit for the absence of any transcript.
5. **[Enhancement] On `not_found`, return distance to the nearest annotated transcript** so a consumer can decide mechanically whether widening `max_distance` would help. (Hypothesis — `not_found` could not be triggered this session.)
6. **[Untestable from this client] Background MCP Tasks.** `task_support: optional` is well documented (taskId → `tasks/get` → `tasks/result`), but this client doesn't expose the `task` augmentation on `tools/call`, so the fire-and-continue path for cold/comprehensive calls is unverified here. Coverage gap, not a defect.

## 4. Recommended changes (prioritized)

1. **Add `readOnlyHint: true` (+ `idempotentHint`, `openWorldHint`) to all 7 tool schemas.** Lowest effort, highest day-one UX payoff.
2. **Dedup `predict_splicing_batch` by resolved `variant_id`** (resolve → dedup → score unique → re-expand). Direct saving on the rate-limited upstream.
3. **Emit model/build provenance** (SpliceAI / Pangolin / GENCODE / VEP versions) in every prediction payload for reproducibility.
4. **Clarify `basic` gene-set scope** in the capabilities doc (includes non-coding MANE), and consider adding nearest-transcript distance to `not_found`.

## 5. Overall verdict: 9 / 10 — production-ready, reference quality

Every tool works as specified; error handling, observability, caching, and self-documentation are among the best tested. No correctness bugs surfaced — the single concrete defect is a batch efficiency miss, and the rest are low-effort polish (tool annotations, version provenance, one doc clarification). The only ceiling is the interactive-only, cold-start-prone upstream, which the server already mitigates with caching, `warmup`, batching, background tasks, and 0ms fail-fast validation.

---

## Appendix — Evidence log (selected, verbatim)

| Call | Key result | `request_id` | Timing / cache |
|---|---|---|---|
| `predict_spliceai chr8-140300616-T-G` | TRAPPC9 acceptor_loss Δ=0.83 @ -2; exon skipping | `43212d3e6288` | 53,037ms, cache miss, cold |
| `predict_splicing chr8-140300616-T-G` (minimal) | `concordant_high`, SAI 0.83 / Pang 0.85 | `1ac28cd64e8c` | 303ms, cache partial, warm |
| `resolve_variant rs6025` | `ambiguous` → C-A / C-T (F5) | `c2a6f1107ace` | 919ms |
| `predict_splicing chr8-zzz-T-G` | `invalid_input` (parse) | `b3a20d182e91` | 0ms |
| `resolve_variant chr8-140300616-A-G` | `ref_warning` (REF A≠T) | `c31b5ac7c25c` | 709ms |
| `warmup GRCh38/raw` | SpliceAI 827ms, Pangolin 487ms | `80e035710b1a` | 1,314ms |
| `predict_pangolin chr8-140300616-T-G` (full) | splice_loss Δ=0.85 @ -2 | `efeeb7c28b82` | 0ms, cache hit |
| `predict_splicing M-3243-A-G` | `unsupported_contig` → gnomad-link | `21db7fc4f793` | 0ms |
| `predict_splicing chr8-999999999999-T-G` | `invalid_input` (out of range, both builds) | `cf84ace9c360` | 0ms |
| `resolve_variant NM_001089.3(ABCA3):c.875A>T` | → `16-2317763-T-A`, missense | `ab2b25286a4e` | 1,663ms |
| `predict_splicing_batch` (5 mixed) | 3 ok / 2 terminal_failed; top 0.85 | `48f98769bf27` | 3,065ms |
| `predict_spliceai ... mask=masked` | donor_loss 0.62→0; aberrations emptied + note | `78c92ce4a223` | 313ms |
| `predict_spliceai ... transcripts=all` | 18 transcripts collapsed via `shared_by` | `08e686be3690` | 0ms, cache hit |
| `predict_splicing ... GRCh37` | `ref_mismatch` + `other_build_hint` → GRCh38 | `280d1131a6dd` | 0ms |
| `predict_splicing rs6983267` | CCAT2 (lncRNA) `concordant_low`; not not_found | `007a2c96c8cb` | 21,095ms, cold |
| `predict_spliceai ... max_distance=10000 include_hints=false` | Δ=0.83 retained; lean `_meta` confirmed | `e1bb71545262` | 14,422ms, cold |
| `resolve_variant NM_001089.3:c.875A>T GRCh37` | → `16-2367764-T-A` (distinct from GRCh38) | `dc438e0137f9` | 794ms |
