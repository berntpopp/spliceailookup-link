# spliceailookup-link MCP — LLM-consumer assessment

- **Server version tested:** 0.8.0 (`capabilities_version` `68685f20483a`)
- **Date:** 2026-06-12
- **Perspective:** the consuming LLM (Claude), evaluating the server as a tool client, not the source code
- **Method:** live session against the running MCP. Part 1 is a dimensional UX rating from a short flagship session; Part 2 is a structured test pass exercising all 7 tools, all 5 resources, the response-mode matrix, and the error taxonomy, pacing calls to the documented concurrency cap of 2 and avoiding deliberate abuse of the interactive-use-only upstream.

This document contains two assessments produced back-to-back:

1. **Part 1 — UX rating (1–10 per dimension + overall)**
2. **Part 2 — Senior-tester evaluation (coverage, findings, recommended changes)**

---

## Part 1 — LLM-consumer UX rating

Grounded in: `get_server_capabilities` (full + `lean`), the resource list, a deliberate error (`chr8-140300616-T-X`), and the token-efficiency knobs (`response_mode=minimal`, `include_hints=false`).

### Overall: 8 / 10

A notably well-engineered MCP — clearly built around what an LLM needs, not just what a REST API exposes. The ceiling is mostly its genuinely slow, rate-limited upstream plus a few verbose-by-default payload choices. Nothing here is broken; the improvements are polish.

| Area | Rating | Why |
|---|---|---|
| Discoverability | 9 | Capabilities tool (full **and** `lean`), 5 typed resources, `recommended_workflows`, `_meta.next_commands` (ready-to-call), `see_also` cross-server hints, and a `capabilities_version` hash for cache invalidation. Among the best I've used. |
| Error handling | 9 | Structured envelope: `error_code`, `retryable`, `recovery_action`, plus `fallback_tool` **with** `fallback_args` and a narrative `recovery`. The invalid variant handed back an executable next step, not just "400". Best-in-class. |
| Safety / compliance | 9 | `research_use_only` + `unsafe_for_clinical_use` stamped on *every* payload including errors; dedicated research-use and citations resources; headline framing stays computational. Right call for a clinical-adjacent domain. |
| Observability | 8 | `request_id`, timing split (`elapsed_ms` vs `upstream_elapsed_ms`), `cache` hit/miss, `served_warm`, `cache_ttl_s` on every call. Gap: no proactive rate-limit/budget signal on success despite a rate-limited upstream. |
| Token efficiency | 8 | `minimal`/`compact`/`full`, `detail=lean` caps, independent `include_hints`/`include_see_also` toggles, version-hash caching, `params_by_reference` to avoid schema duplication. Knocked for verbose *defaults* (below). |
| Speed | 7 | Floor is the upstream (10–40s cold, "several req/min"). The server does everything right to hide it — 24h cache, `warmup`, warm containers (`served_warm:true` even on a cache miss), background-task support, batch fan-out — but an LLM still feels the latency. Honest 7. |

### Obvious improvements (from Part 1)

1. **Trim the hot-path payload.** Compact mode repeats the `threshold_basis` glossary string (`"Δ>=0.5 high; 0.2-0.5 moderate…"`) inside `interpretation` on *every* call — a static fact already in capabilities/`reference`. And `capabilities_version` is duplicated at both top-level and inside `_meta`. Dropping both shaves tokens on the most-used path.
2. **Signal rate-limit headroom proactively.** A `rate_limited` code and "several requests per user per minute" upstream exist, but success responses give no budget hint. Add `_meta.rate_limit` (remaining / reset, or a soft `retry_after`) so the client can *pace* a burst rather than discovering the limit by hitting the wall. Highest-value gap for an autonomous caller.
3. **Make hint-suppression sticky, or document the lifecycle.** No way to say "I've learned the workflow" once — `include_hints=false` must be re-threaded every call. Either honor a session-level default or explicitly recommend it in capabilities.
4. **Disambiguate the error fallback.** A malformed-ALT input returned a fallback to `resolve_variant` — which would also reject a bad ALT base; that path can loop. Distinguish "needs resolution" (HGVS/rsID/loose) from "malformed CHROM-POS-REF-ALT".
5. **Unify the cross-mode shape.** `minimal` reports `top:{class,score,position}` while `compact` nests `delta_scores:{…}`. Same fact, two shapes — a caller switching modes has to branch.

> Caveat: Part 1 is one short, warm session. The Speed and Observability scores especially could move with heavier use.

---

## Part 2 — Senior-tester evaluation

### Verdict

A **mature, unusually well-engineered MCP** — its error envelopes, observability, and token discipline are above almost anything observed in a consuming client. Testing surfaced **one real correctness/latency bug**, a **misleading error-recovery hint**, and **a handful of consistency/polish nits**. Nothing blocks production use.

### Coverage — what was tested

| Tool | Paths exercised | Verdict |
|---|---|---|
| `get_server_capabilities` | `lean` + `full` | OK — documents concurrency cap, `rate_budget`, task support, batch retry, cache contract |
| `resolve_variant` | HGVS, multiallelic rsID | OK — strand-flip correct; ambiguity surfaced as **data** (both alleles in `next_commands`), not an error |
| `predict_spliceai` | compact/full/minimal, raw/masked, mane/all, GRCh37/38, 5 error paths | OK — strong |
| `predict_pangolin` | minimal standalone | OK — clean envelope |
| `predict_splicing` | compact + minimal; agreement verdicts | OK — `concordant_high` **and** `discordant_subthreshold` observed |
| `predict_splicing_batch` | mixed good/terminal items, 26-item overflow | OK — per-item errors don't fail envelope; `validation_failed` precise |
| `warmup` | GRCh38/raw | OK — per-model `elapsed_ms` + honest "may still cold-start" caveat |
| 5 resources | all read | OK — consistent, research-use stamped |

**Error codes proven:** `invalid_input`, `ref_mismatch` (+`other_build_hint`), `unsupported_contig` (MT *and* `chr99`), `not_found`, `ambiguous`, `validation_failed`.

**Not reproduced:** `build_mismatch` (see Finding 1 — the case that *should* trigger it misbehaves), `rate_limited` (deliberately not stress-tested, to respect the interactive-use-only backend), `upstream_unavailable` / `internal_error` (transient / fault-only).

### Findings & recommended changes (ranked)

**1. [Bug — Medium] Out-of-range coordinate costs ~15s and returns the wrong error code.**
`chr1-260000000-A-G` is past the end of chr1 (GRCh38 chr1 ≈ 248.96 Mb). It slipped through the pre-flight checks, spent **14.6s at the scoring upstream**, and returned `not_found`. The capabilities doc explicitly promises out-of-range → `build_mismatch` and a "<0.5s" fast-fail. This wastes a slow, rate-limited upstream call on a coordinate that is invalid by arithmetic.
→ **Add a per-build contig-length table and reject `pos > contig_length` locally (≪1ms) as `build_mismatch` (or `invalid_input`)** before any Ensembl/scoring call. Fix this first.

**2. [UX — Low/Med] `ref_mismatch` recovery re-suggests `resolve_variant` with the *same* wrong-REF coordinate.**
For `chr8-140300616-A-G`, `fallback_args` echoes the unchanged bad coordinate to `resolve_variant` — which, with `check_ref=true`, would flag the same mismatch. `resolve_variant` only rescues HGVS/rsID inputs, not a coordinate with a wrong REF. The prose recovery is fine; the *structured* fallback is a dead end.
→ When the input is already a coordinate: drop the `resolve_variant` fallback, or make it actionable — if `other_build_hint` exists set `fallback_args.genome_build` to the matching build; if ALT matches the reference base, suggest the REF/ALT swap explicitly.

**3. [Consistency — Low] Same fact, different field names across response modes — forces callers to branch.**
- `predict_splicing` minimal → `spliceai_max` / `pangolin_max`; compact+ → `agreement.spliceai_max_delta` / `pangolin_max_delta`.
- `predict_spliceai` / `predict_pangolin` minimal → `top:{class,score,position}`; compact → nested `delta_scores:{…}` with no `top`.
→ Keep a **stable summary key in every mode** (always emit `top` + `max_delta_score`) and unify `_max` vs `_max_delta` naming. The client shouldn't have to detect the mode to find the headline number.

**4. [Cross-server hint — Low] The `gtex-link` see_also example passes a gene *symbol* into a `gencode_id` field.**
Full-mode `see_also` emits `get_median_expression_levels({gencode_id:["TRAPPC9"]})`, but gtex expects an Ensembl/GENCODE id — and the resolved `ENSG00000167632.19` is in the same payload. A "ready-to-call" hint that won't run undercuts the chaining promise.
→ Populate the gtex example with the resolved `gene_id`, not the symbol.

**5. [Polish — Low]** (a) For symbol-less lncRNAs the headline prints the raw `ENSG…` as the gene ("ENSG00000241860 — no predicted splicing impact"). (b) Batch items carry no per-item `request_id`, so correlating one slow/failed item in a 25-variant batch to server logs relies on the variant string. Both cosmetic.

**6. [Token — Low, confirmed across many calls]** The static glossary string `interpretation.threshold_basis` repeats on **every** compact and full payload, though it is already in capabilities + the `reference` resource.
→ Drop it from the hot path (or gate behind `full`). (Same as Part 1 improvement #1.)

### What's genuinely excellent (keep)

- **Error envelope** (`error_code` + `retryable` + `recovery_action` + `fallback_tool`/`fallback_args` + narrative + `next_commands`) is best-in-class.
- **`other_build_hint`** handles the single most common variant mistake (GRCh37↔38 confusion) in **0ms** with build-specific recovery text appended to `recovery`.
- **`mask=masked` `consequence.note`** proactively warns that masking suppressed an aberration raw mode would predict — prevents a real misinterpretation. (Verified: `donor_loss` 0.62→0, `aberrations` → empty, headline drops the exon-skipping claim.)
- **Pre-flight REF check ordering** — wrong REF fast-fails in <0.5s instead of a ~17s `not_found`.
- **Observability** (`request_id`, timing split, `cache: hit/miss/partial`, `served_warm`, `cache_age_s`) and **token discipline** (mode tiers that genuinely differ in size, `shared_by` transcript collapse, batch omitting per-item hints, lean path dropping repetitive fields).
- **Batch semantics**: per-item failures don't sink the envelope; terminal-vs-retryable split with `retry_variants`; verdict tallies + `summary_top_variant`.
- **`validation_failed`** returns field-level Pydantic detail ("List should have at most 25 items after validation, not 26") — the cap is enforced server-side, not silently truncated.

### Tester's caveats on this pass

Single warm session against a live upstream. Not exercised: `rate_limited` stress (to respect the interactive-use-only backend), `comprehensive` gene_set (documented slow / 503-prone), background-task (`task`) execution, and a true `build_mismatch` (Finding 1 is the closest path and argues it needs attention).

### Suggested follow-ups

- Construct a real liftover coordinate pair to force a genuine `build_mismatch`.
- Drive a background `task` call to verify the async path end-to-end.
- File Findings 1–6 as GitHub issues with the repro coordinates above.

---

### Appendix — representative repro coordinates

| Scenario | Input | Observed result |
|---|---|---|
| Happy path (both models) | `chr8-140300616-T-G` | `concordant_high`, SpliceAI Δ=0.83 / Pangolin Δ=0.85, exon skipping |
| Discordant weak signal | `16-2317763-T-A` (ABCA3) | `discordant_subthreshold`, SpliceAI 0.21 / Pangolin 0.05 |
| HGVS resolve | `NM_001089.3(ABCA3):c.875A>T` | → `16-2317763-T-A`, missense_variant |
| Ambiguous rsID | `rs6025` | `ambiguous:true`, alleles `1-169549811-C-A` / `1-169549811-C-T` |
| invalid_input | `chr8-140300616-T-X` | parse error + `resolve_variant` fallback |
| ref_mismatch | `chr8-140300616-A-G` | REF 'A' ≠ ref 'T', 495ms pre-flight |
| ref_mismatch + other_build_hint | `chr8-140300616-T-G` as `GRCh37` | 0ms, `other_build_hint` → GRCh38 |
| unsupported_contig (MT) | `chrM-8993-T-G` | → gnomad-link `get_mitochondrial_variant` |
| unsupported_contig (non-standard) | `chr99-100-A-T` | 0ms |
| **Out-of-range (Finding 1)** | `chr1-260000000-A-G` | **14.6s → not_found (should be fast build_mismatch)** |
| not_found path | `chr1-260000000-A-G` | not_found envelope verified |
| validation_failed | 26-item batch | field-level "at most 25 items… not 26" |
| Cold call latency | `chr1-100000-C-G` | 28s, `served_warm:false`, lncRNA `shared_by` collapse |
