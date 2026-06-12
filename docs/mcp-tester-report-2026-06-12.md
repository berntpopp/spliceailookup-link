# spliceailookup-link — MCP Consumer Evaluation & Senior-Tester Report

**Date:** 2026-06-12
**Server:** `spliceailookup-link` **v0.5.0**
**MCP protocol:** `2025-11-25`
**Evaluator:** Claude (acting as an LLM consumer, then as a senior MCP tester)
**Method:** Live black-box testing through the MCP facade. All seven tools exercised across happy paths, every parameter axis, the resolver, batch, and deliberate error/edge paths. Evidence (request IDs, timings, scores) captured verbatim from responses.

> Research-use-only server. Every payload carries `unsafe_for_clinical_use`. Nothing in this report is clinical guidance.

---

## Part 1 — Consumer-experience evaluation (dimensions)

Rated against Anthropic's "Writing effective tools for agents" (clear descriptions, meaningful/human-readable context, namespacing, token efficiency) plus MCP community best practice (search-first discovery, documented failure modes, observability).

| Dimension | Score | Basis |
|---|---:|---|
| Discoverability | 9 | `get_server_capabilities` is comprehensive (tools, per-param docs, "which tool?" disambiguation, workflows, score glossary, error codes, limits). Tool descriptions self-disambiguate ("use this for X; use predict_splicing for Y") — ideal for a deferred/search-first tool world. |
| Output interpretability | 9 | `headline` answers in one read; `interpretation.band` + `threshold_basis` inline; human-readable fields, not raw IDs. |
| Safety / scope clarity | 10 | `research_use_only` + `unsafe_for_clinical_use` on every `_meta`; explicit out-of-scope list that *delegates* to sibling servers. |
| Observability | 9 | `request_id`, `timing.elapsed_ms`, `cache` status, `upstream_elapsed_ms`, `cache_age_s`, `rate_budget` on rate-limit. Among the best observed. |
| Input ergonomics | 9 | Clean enums + defaults + min/max + `examples`; one `variant` field accepts CHROM-POS-REF-ALT, HGVS, rsID with auto-resolution. |
| Composability | 9 | `_meta.next_commands` ready-to-call; `see_also` cross-server hints (gnomad / genereviews / gtex / uniprot). |
| Token efficiency | 8 | `response_mode` tiers, compact default (~1.5 kB), transcript-collapse, `max_transcripts` cap, `capabilities_version` hash. Costs: 7.8 kB capabilities doc duplicates schema param docs (SEP-1576 redundancy); `_meta` is a sizeable fraction of a compact payload. |
| Speed / latency | 8 | Warm calls ~0.2–0.6 s; cold 10–40 s. Well-mitigated (`warmup`, background Tasks, 24 h cache) but ceiling is upstream; concurrency cap is 2. |
| Error handling / robustness | 8 | Documented 7-code taxonomy; HTTP-200+`error` gotcha handled; `cross_build_check` → `build_mismatch`; honest `resolve_caveat`. (See Part 2 findings for two real gaps.) |

**Overall consumer score: 9 / 10.** Checks best-practice boxes most servers miss — observability, cross-server `see_also`, one-line `headline`, `capabilities_version`, latency mitigations.

---

## Part 2 — Senior-tester session

### Coverage matrix

| Tool | Tested | Axes / cases exercised |
|---|---|---|
| `get_server_capabilities` | ✅ | Full doc; version + protocol confirmed |
| `warmup` | ✅ | GRCh38; per-model elapsed |
| `resolve_variant` | ✅ | HGVS, rsID (ambiguous), malformed input |
| `predict_spliceai` | ✅ | compact/raw; `full` + `transcripts=all`; wrong-REF; build mismatch; `comprehensive` + `max_distance=2000` |
| `predict_pangolin` | ✅ | `mask=masked` |
| `predict_splicing` | ✅ | compact flagship; `minimal` |
| `predict_splicing_batch` | ✅ | 5-variant mix: valid / not-found / malformed / ambiguous rsID / HGVS |

Parameter axes covered: `genome_build` (GRCh37 mismatch), `mask` (raw + masked), `gene_set` (basic + comprehensive), `transcripts` (mane + all), `response_mode` (compact + full + minimal), `max_distance` (500 + 2000), `cross_build_check` (on).

Error codes observed: `invalid_input` ✅, `not_found` ✅, `build_mismatch` ✅.
Not exercised: `rate_limited` (did not saturate the cap of 2), `validation_failed`, `upstream_unavailable` (the comprehensive path hard-timed-out at the client instead of returning this envelope), `internal_error`.

### Per-tool results & ratings

#### `warmup` — 8 / 10
Clean, fast (`706 ms`, both models `ok`, per-model `elapsed_ms`). **Caveat:** a subsequent `mask=masked` Pangolin call still took **11.5 s** despite the warm-up, suggesting warmth is param/scope-specific or decays quickly. Warmth scope and TTL are undocumented.

#### `resolve_variant` — 9.5 / 10
- HGVS `NM_001089.3(ABCA3):c.875A>T` → `16-2317763-T-A`, gene `ABCA3`, `missense_variant`; correctly strand-flipped (minus strand), with chaining `next_commands`.
- rsID `rs6025` → **flagged `ambiguous: true`**, returned *both* candidate IDs (`C-A`, `C-T`), a plain-English `note`, and `next_commands` for each. Exemplary ambiguity UX.
- Malformed input → `invalid_input` in **0 ms** (failed locally, no upstream call), with `recovery_action`, `fallback_tool`, prose `recovery`, and `next_commands`.

#### `predict_spliceai` — 9 / 10
- Compact/raw: TRAPPC9 acceptor loss **Δ=0.83 @ −2 bp**, `band: high`, SAI-10k `exon_skipping`.
- `full` + `transcripts=all`: confirmed **transcript-collapse** (one block + `shared_by` of 17 byte-identical transcripts), `ref_alt_scores`, `exon_model`, `consequence.transcript_info`. Returned as a **cache hit** (`elapsed_ms: 0`) despite new presentation params — proving the cache key is on scoring params, not response shape. `full`-mode `see_also` correctly carried example args.

#### `predict_pangolin` — 8.5 / 10
- `mask=masked`: splice loss **Δ=0.85 @ −2 bp**, `band: high` — identical to raw (expected; the variant sits on an annotated acceptor, so masking suppresses nothing). Cache miss took **11.5 s** (see `warmup` caveat).

#### `predict_splicing` (flagship) — 9.5 / 10
- Both models + `agreement.verdict: concordant_high` + merged `headline`. `cache: "partial"` (SpliceAI half reused at `cache_age_s: 353`, Pangolin fetched fresh) — partial caching is a standout.
- `minimal`: tight payload (`gene`, `agreement.verdict`, `spliceai_max`, `pangolin_max`, `band`, `headline`) — correct token tiering.

#### `predict_splicing_batch` — 8.5 / 10
5 variants → **3 ok / 2 failed**, per-item errors isolated (do not sink the batch), each item echoes its `variant`, plus a `summary` (verdict breakdown) and `summary_top_variant`. Per-item `_meta` trimmed to cache only — good economy. **Two findings below.**

#### `get_server_capabilities` — 9 / 10
Comprehensive, stable `capabilities_version` content hash, resource list. Main cost is size (7.8 kB) and param-doc redundancy with the tool schemas.

---

## Consolidated findings (prioritized)

### High
1. **Wrong REF allele is misclassified as `not_found` with misleading recovery.**
   `chr8-140300616-A-G` (true REF is `T`) returned `not_found` advising `gene_set='comprehensive'` / wider `max_distance` / `resolve_variant` — none of which address the actual problem (incorrect reference base). The server's own `resolve_caveat` admits coordinates are normalized, not validated.
   **Fix:** add a cheap reference-base check (or a `dry_run`/validate mode) and a distinct error (e.g. `ref_mismatch`) with corrective guidance. Converts a misleading slow-ish failure into an instant, accurate one.

2. **Batch silently resolves an ambiguous rsID to the first allele.**
   `rs6025` in `predict_splicing_batch` scored only `1-169549811-C-A` with **no `ambiguous` flag** on the item — inconsistent with `resolve_variant`, which flags it and returns both alleles. Risk: silent under-reporting in panel workflows.
   **Fix:** surface ambiguity per batch item (flag + alternates), or fail that item with `ambiguous`/`invalid_input` so the caller chooses.

3. **Echo `capabilities_version` in every prediction `_meta`.** (carried from Part 1)
   The freshness hash lives only inside the 7.8 kB capabilities doc, so a warm client must re-fetch the whole doc to detect drift. Echoing the hash on every response lets clients skip the doc until it actually changes. Highest leverage, near-zero cost.

### Medium
4. **`comprehensive` gene_set + widened `max_distance` hard-times-out at the client** rather than returning a graceful `upstream_unavailable`/timeout envelope. Docs warn it is "much slower; may 503," but the observed behavior was a client-level timeout with no structured error to act on.
   **Fix:** enforce a server-side deadline that returns `upstream_unavailable` (retryable, with guidance), and/or recommend background Tasks for this path explicitly in the error.

5. **`warmup` did not prevent an 11.5 s subsequent call.** Warmth appears param/scope-specific or short-lived.
   **Fix:** document warmth scope (which param combos it covers) and TTL; consider warming the masked/raw paths the caller intends to use.

6. **Trim `_meta` redundancy / capabilities-doc size.** (carried from Part 1)
   `next_commands` + (collapsed) `see_also` ride every compact payload — multiplied across a 25-variant batch. Consider gating chaining hints, and a leaner capabilities mode (tool list + hash + glossary, params by-reference; cf. SEP-1576).

### Low
7. **`discordant` verdict may over-alarm for sub-threshold pairs.** SpliceAI `0.31` vs Pangolin `0.09` (and `0.21` vs `0.05`) were labeled `discordant` even though neither crosses the high band.
   **Fix:** add nuance for "both below high-confidence" (e.g. a `concordant_low`/`both_subthreshold` qualifier) so a magnitude split between two weak signals doesn't read as a strong conflict.

8. **`build_mismatch` detection costs a full extra probe (~17.7 s observed).** Correct and valuable, but expensive. A coordinate/build heuristic fast-path could short-circuit obvious cases before the second-build round-trip.

---

## What worked especially well

- **Error envelopes** are best-in-class: `error_code`, `retryable`, `recovery_action`, `fallback_tool`/`fallback_args`, prose `recovery`, and `next_commands` on every failure — including per-item batch errors.
- **Caching**: cross-call, presentation-independent, partial (per-model), 24 h TTL, with `cache`/`cache_age_s` observability.
- **Resolver ambiguity handling** (`resolve_variant` on `rs6025`).
- **`agreement.verdict`** + merged `headline` make the flagship a true one-call answer.
- **Cross-server `see_also`** turns a single server into an ecosystem entry point.

## Overall

**Server grade: 9 / 10.** A mature, well-instrumented MCP. The highest-value fixes are correctness-adjacent and cheap: distinct `ref_mismatch` handling, surfacing batch ambiguity, and echoing `capabilities_version`. The comprehensive-path timeout is the main robustness gap worth a deadline + structured error.

---

### Appendix — selected evidence

| Case | request_id | Result | Timing |
|---|---|---|---|
| `warmup` GRCh38 | `76d4192cdce9` | warmed (spliceai 324 ms / pangolin 382 ms) | 706 ms |
| resolve HGVS ABCA3 | `b10d6d98f68d` | `16-2317763-T-A` missense | 1430 ms |
| resolve `rs6025` | `477bbbc1353a` | ambiguous (2 alleles) | 612 ms |
| resolve malformed | `3469c5c33b2c` | `invalid_input` | 0 ms |
| `predict_splicing` chr8 | `306b7ea30fc9` | concordant_high (SAI 0.83 / Pang 0.85) | 257 ms (partial cache) |
| pangolin masked | `fcf5a9e53817` | Δ=0.85 | 11459 ms (miss) |
| spliceai full/all | `367edc874b8b` | collapse + 17 shared_by | 0 ms (hit) |
| wrong REF A-G | `fa2dbca7b5c2` | `not_found` (misclassified) | 643 ms |
| build mismatch | `2654b79a12a4` | `build_mismatch` | 17670 ms |
| batch (5) | `544b0a7481ba` | 3 ok / 2 failed | 2144 ms |
| `comprehensive` + dist 2000 | — | client timeout (no envelope) | timed out |

**Sources (best-practice basis):**
- [Writing effective tools for AI agents — Anthropic](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Code execution with MCP — Anthropic](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [15 Best Practices for Building MCP Servers in Production — The New Stack](https://thenewstack.io/15-best-practices-for-building-mcp-servers-in-production/)
- [SEP-1576: Mitigating Token Bloat in MCP — modelcontextprotocol](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1576)
- [MCP Token Optimization: 4 Approaches Compared — StackOne](https://www.stackone.com/blog/mcp-token-optimization/)
