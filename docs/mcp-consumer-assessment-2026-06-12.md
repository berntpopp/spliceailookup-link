# SpliceAI Lookup Link — LLM-Consumer Assessment

**Date:** 2026-06-12
**Server under test:** `spliceailookup-link` v0.6.0 (MCP protocol `2025-11-25`, capabilities hash `784e4272d98e`)
**Author:** Claude (LLM consumer of the MCP), via the live MCP facade
**Method:** All findings are grounded in live tool calls against the running server — discovery surface, full parameter matrix, deliberate error-path probing, and cache/observability inspection. Upstream is interactive-use-only and rate-limited, so calls were paced in small waves and leaned on the 24h cache where possible.

This document collects two assessments produced in sequence:

1. **Part 1 — LLM UX evaluation:** a dimension-by-dimension rating of the server as experienced by an LLM client (discoverability, token efficiency, speed, observability, error handling, chaining, consistency).
2. **Part 2 — Senior-tester test report:** a structured test campaign across all 7 tools and 5 resources, with error-code coverage, issues ranked by severity, and prioritized change recommendations.

> Related pre-existing docs in this folder (not authored here): `mcp-evaluation.md`, `mcp-tester-report-2026-06-12.md`.

---

# Part 1 — LLM UX Evaluation

This is an evaluation of the server *as experienced by the LLM that has to consume it* — grounded in a cold prediction, a cache hit, both capabilities detail levels, an error path, and the minimal/compact response modes.

## Overall: 9/10

Top-tier MCP design. It is clearly built *for an LLM consumer* rather than ported from a REST API: headline-first responses, self-documenting thresholds, structured recovery, and cross-server chaining hints. The gaps keeping it from a 10 are token overhead in `_meta` and (at the time of the first pass) the perceived lack of *proactive* rate-limit signalling — the latter was partly corrected on deeper inspection (see note below).

## Per-dimension scores

| Dimension | Score | Evidence |
|---|---|---|
| Discoverability | 9 | `get_server_capabilities` with `lean`/`full` tiers, `recommended_workflows`, enumerated `error_codes`/`agreement_verdicts`/`interpretation_bands`, and tool descriptions that embed *when to use*, payload size, and cold-call latency. The "which tool? predict_splicing = BOTH models" disambiguation prevents wrong-tool calls. |
| Error handling / recovery | 9 | The invalid-input envelope carried `error_code`, `retryable:false`, `recovery_action`, `fallback_tool`, `next_commands`, and prose `recovery` that literally says "Do not retry unchanged." Gold standard for keeping an agent from looping. |
| Observability | 9 | Every response has `request_id`, `timing.elapsed_ms`, `upstream_elapsed_ms`, `cache` (hit/miss/partial + `cache_age_s` + `cache_ttl_s`), and a `capabilities_version` content hash so a warm client can detect drift and skip re-fetching. |
| Speed / latency management | 9 | 24h cache (second call returned `cache:"hit"`, `elapsed_ms:0`, `cache_age_s:60`), plus a `warmup` tool for cold containers and background-task support (`taskSupport=optional`) to avoid blocking 15–40s. Honest cold-call warnings set correct expectations. |
| Agentic chaining / interop | 9 | `_meta.next_commands` gives ready-to-call next steps; `see_also` points at sibling servers (gnomad, genereviews, gtex, uniprot). Cross-server orchestration hints are rare and genuinely useful. |
| Schema consistency | 8 | Uniform `success` envelope everywhere. Minor: response shape changes across modes (`minimal` returns `top`/`consequence_summary`; `compact` returns full `delta_scores`), so a parser must branch on mode. |
| Token efficiency | 7 | Good controls (`response_mode` minimal/compact/full, `lean` capabilities, params-by-reference per SEP-1576). But `_meta` is heavy and repeats on *every* call. On the `minimal` prediction, the `_meta` block was roughly **half the payload**. Weakest area. |

## Improvements (from this pass), ranked by value

1. **Let agents suppress `next_commands`/`see_also` once they know the workflow.** An `include_chaining_hints=false` flag (or auto-dropping them after the first call) would cut the dominant overhead in `minimal` mode. Matters most for `predict_splicing_batch`: a 50-variant panel should not repeat the 4-entry `see_also` per item. *(Part 2 note: the batch already drops these per item — so the remaining ask is just to extend that leanness to standalone calls.)*
2. **Expose the rate-limit budget proactively.** *(Partly already done — see correction below.)* Ensure `rate_limited`/`upstream_unavailable` errors carry a backoff hint.
3. **Fix the circular recovery prose in `resolve_variant`.** When `resolve_variant` itself returns `invalid_input`, its `recovery` text says "Call `resolve_variant`…" — the tool you are already in. The `fallback_tool` correctly points to `get_server_capabilities`; only the prose is circular.
4. **Hoist always-true constants.** `unsafe_for_clinical_use:true` and the full `capabilities_version` ride on every response. Cheap individually, but in batch contexts they add up.

### Correction discovered during Part 2

The full capabilities document documents a `concurrency` block (`max_concurrent_requests: 2`, `queue_wait_seconds: 30`), a 55s soft `prediction_deadline`, and a `rate_budget` object (`{limit, remaining, unit:'concurrent_requests'}`) returned on `rate_limited` errors — clarified as a **local concurrency cap, not a time-windowed rate limit**. So the rate-limit budget *is* exposed, just on the error envelope rather than proactively. Improvement #2 is therefore softened: the server already surfaces remaining concurrency on saturation.

---

# Part 2 — Senior-Tester Test Report

**Build:** server 0.6.0, protocol 2025-11-25, capabilities hash `784e4272d98e`
**Scope:** all 7 tools + 5 resources, full parameter matrix, 7 of 9 error codes triggered live.

## Verdict

**9/10 for the prediction/resolver tools; the batch tool drags to ~6.5 and is the one thing to fix before calling this production-grade.**

## What was exercised

| Tool | Coverage | Result |
|---|---|---|
| `get_server_capabilities` | `lean` + `full` | ✅ Both tiers; content-hash drift signal present |
| `resolve_variant` | coordinate, rsID, HGVS(transcript), GRCh37, invalid | ✅ Local-vs-VEP split, multiallelic `ambiguous` |
| `predict_spliceai` | raw/masked, mane/all, compact/full/minimal, GRCh37, distance | ✅ All behaviors matched contract |
| `predict_pangolin` | via `predict_splicing`/batch | ✅ signed + absolute score |
| `predict_splicing` | both models, agreement verdicts | ✅ Flagship; cleanest output |
| `predict_splicing_batch` | 5-item mixed valid/error | ⚠️ Works but self-saturates (Issue 1) |
| `warmup` | GRCh38/masked | ✅ Per-model honest timing |
| resources (5) | listed + read `reference` | ✅ Full error taxonomy, glossary |

### Per-tool scores

| Tool | Score |
|---|---|
| `get_server_capabilities` | 9.5 |
| `resolve_variant` | 9 |
| `predict_spliceai` | 9.5 |
| `predict_pangolin` | 9 |
| `predict_splicing` | 9.5 |
| `predict_splicing_batch` | 6.5 |
| `warmup` | 9 |

## Error-code coverage (live)

Triggered live (7/9): `invalid_input`, `ref_mismatch`, `build_mismatch`, `ambiguous`, `rate_limited`, `validation_failed`, `upstream_unavailable`.

Not triggered:

- `not_found` — every intent constructible without a known reference base resolved to another code first (a correct-REF intergenic locus could not be built blind).
- `internal_error` — only reachable via an unexpected fault, not deliberately.

Notable observed envelopes:

- `ref_mismatch`: "REF allele 'A' does not match the GRCh38 reference base 'T' at 8:140300616." (`fallback_tool: resolve_variant`).
- `build_mismatch`: GRCh38 coordinate sent as GRCh37 was caught in ~300 ms, before any scoring call, with `fallback_args` pre-filled with the inferred build.
- `validation_failed`: `max_distance=50000` returned a `field_errors:[{field:"max_distance", reason:"Input should be less than or equal to 10000"}]` array. The server validates defensively even though the client schema declared `maximum: 10000` (the harness did not enforce it client-side).
- `upstream_unavailable`: `MT-3243-A-G` standalone returned HTTP 503 after ~45s.

## What is genuinely excellent

- **Cache is at the upstream-payload layer, not the formatted-response layer.** A `transcripts=all response_mode=full` call returned `ref_alt_scores`, the `exon_model`, and **17 byte-identical transcripts collapsed into one `shared_by` block** — all as `cache:"hit"` in 0 ms. Richer projections of an already-seen variant cost nothing upstream. The single best design decision in the server.
- **Masked-mode false-negative guard.** Under `mask=masked`, `donor_loss` dropped 0.62→0 and `consequence.aberrations` went empty — but a `note` told the caller "this site has a non-trivial delta but no masked aberration — re-run raw." Prevents an agent concluding "no effect."
- **Build inference beats the documented behavior.** Docs say cross-build is probed "on not_found"; in practice the mismatch was caught proactively in ~300 ms with the corrected build pre-filled.
- **Nuanced agreement verdicts.** The F5 variant produced `discordant_subthreshold` with the right gloss ("models differ in magnitude but neither crosses 0.5; treat as weak, not a strong conflict") rather than crying `discordant`.
- **Uniformly actionable error envelopes** — every failure carried `retryable`, `recovery_action`, `fallback_tool`/`fallback_args`, prose `recovery`, and `next_commands`.

## Issues found, ranked

**1. (HIGH) `predict_splicing_batch` self-saturates its own concurrency cap and misclassifies the resulting failure.**
In a 5-item batch, the mitochondrial item returned `rate_limited` ("max 2 concurrent upstream requests"). Run **standalone**, the identical input reveals the true cause is `upstream_unavailable` (503). So the batch (a) let three upstream-needing items contend for two slots and failed the loser instead of queuing it through the documented 30 s wait, and (b) reported the contention *symptom* (`rate_limited`) rather than the *root cause* (503). For the documented 25-variant panel use case, a couple of slow items can spuriously fail otherwise-valid siblings, undercutting the tool's reason to exist. Total batch wall-time was 41 s, near the 55 s soft deadline — the slow MT item starved the queue. The batch's per-item `rate_limited` error also did **not** include the `rate_budget` object that the capabilities doc advertises for that code.

**2. (MEDIUM) Unsupported contigs fail slow instead of fast.**
`MT-3243-A-G` spent ~45 s before the backend 503'd. A mitochondrial/non-standard contig is knowable up front — a pre-flight allowlist should short-circuit it to `not_found`/`invalid_input` with a clear "mitochondrial contig not supported by the splicing models" in <1 s, instead of burning a 45 s concurrency slot (which is also what triggered Issue 1).

**3. (LOW) Cross-build ID inconsistency.**
GRCh37 F5 returned `gene_id` `ENSG00000198734.13_12` and `transcript_id` `ENST00000367797.9_9` (double-versioned GENCODE-on-GRCh37 style); GRCh38 returned the clean `ENSG00000198734.13`. Downstream joins across builds will mismatch. Normalize or document the `_NN` suffix.

**4. (LOW) `resolve_variant` self-referential recovery.**
When `resolve_variant` itself returns `invalid_input`, the prose says "Call `resolve_variant` to normalize…" — the tool you are already in. (`fallback_tool` correctly points to `get_server_capabilities`; only the prose is circular.)

**5. (INFO) Token overhead on standalone calls.**
Every standalone success repeats `next_commands` + the 4-entry `see_also`. The **batch already drops these from per-item results** (good) — so the fix is to offer the same leanness to token-sensitive standalone callers via an opt-out flag.

## Recommended changes (prioritized)

1. **Make the batch scheduler internal and resilient.** Queue items through the concurrency cap so valid items never `rate_limited` each other; auto-retry per-item `rate_limited`/`upstream_unavailable` once within the batch budget; in `summary`, split terminal failures (`invalid_input`, `ref_mismatch`) from retryable ones and emit a `retry_variants` list. Highest-leverage fix.
2. **Pre-flight unsupported inputs** (MT/non-standard contigs, quick assembly sanity check) to fail fast rather than after a 45 s 503.
3. **Normalize Ensembl gene/transcript IDs across builds** (strip the GENCODE re-version suffix) or document it in the field glossary.
4. **De-circularize `resolve_variant`'s recovery text** — point to format examples / `get_server_capabilities`.
5. **Add an optional `include_hints=false`** to drop `see_also`/`next_commands` on standalone calls (batch already does this).
6. **Confirm `rate_budget` is attached to per-item batch `rate_limited` errors** (it was present in the contract but absent from the observed batch item).

## What could not be tested (disclosed)

- `not_found` and `internal_error` were never independently triggered (see error-code coverage above).
- `gene_set=comprehensive` was deliberately avoided (documented as 503-prone with large windows; the MT 503 corroborates the risk).
- **Background-task execution (MCP Tasks) is uninspectable from this client** — a `task` field cannot be attached to a `tools/call` through the harness. The contract (`task_support: optional`, eligible tools, in-process `memory://` backend, session-local) is well documented but unverified; covering it needs a harness that speaks raw MCP Tasks.

## Bottom line

The resolver and single/dual prediction tools are among the best-engineered MCP tools tested — the caching, error contracts, and interpretive guardrails are exemplary. The batch tool is the weak link; fixing its internal scheduling and error attribution would bring the whole server to a clean 9–9.5.
