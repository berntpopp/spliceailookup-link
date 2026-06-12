# SpliceAILookup-Link MCP — UX Assessment (LLM Consumer Perspective)

**Date:** 2026-06-12
**Server:** `spliceailookup-link` v0.7.0 (MCP protocol `2025-11-25`)
**Capabilities version:** `14036e02d674`
**Evaluator:** Claude (the LLM that actually consumes the tools)

This document captures three things produced in the same session:

1. A representative variant lookup, used as ground-truth evidence for the
   assessment (the "previous" result).
2. A full UX assessment of the MCP across discoverability, token efficiency,
   speed, observability, error handling, agentic chaining, and safety framing.
3. A senior-MCP-tester report: a structured black-box test campaign (~35 live
   calls) across the whole tool/resource/error surface, with defects ranked by
   severity and prioritized recommendations.

Ratings reflect the experience of an LLM calling these tools, not a human
reading a website.

---

## Part 1 — Evidence: a representative lookup

Query: **SpliceAI scores for `chr8-140300616-T-G` (GRCh38)** via `predict_spliceai`.

SpliceAI predicts a **strong splice-disrupting effect** in **TRAPPC9**
(MANE Select `ENST00000438773.4` / `NM_001160372.4`, minus strand):

| Delta            | Score | Position |
|------------------|-------|----------|
| **Acceptor loss**| **0.83** | −2 bp |
| Donor loss       | 0.62  | −147 bp |
| Acceptor gain    | 0.04  | −32 bp |
| Donor gain       | 0.00  | +66 bp |

- **Max Δ = 0.83 → high-confidence** (convention: Δ≥0.5 high, 0.2–0.5 moderate).
- The acceptor loss sits **2 bp from the intron 10 acceptor boundary**, and the
  SAI-10k consequence calculator predicts **exon skipping**.

Cross-checked with the dual-model flagship (`predict_splicing`): SpliceAI Δ=0.83,
Pangolin Δ=0.85, agreement verdict **`concordant_high`** ("both models predict a
strong splicing effect"). The two independent models agree.

> Research-use computational predictions only — not clinical decision support.
> Interpret alongside orthogonal evidence.

### Surface exercised for the assessment

- `get_server_capabilities` (`lean` + `full`) — discovery
- `predict_spliceai` in `minimal` / `compact` / `full` — token efficiency
- `resolve_variant` (`rs6025`, an ambiguous rsID) — resolution path
- `predict_splicing` — dual-model flagship with agreement verdict
- `predict_splicing_batch` (3 variants, one deliberately malformed) — batch + per-item errors
- An invalid variant (`not-a-real-variant`) — error envelope

---

## Part 2 — UX Assessment

### Overall: 8.5 / 10

One of the better-engineered MCPs in use today. It is clearly designed *for an
LLM consumer* rather than ported from a REST API: headline-first responses,
ready-to-call chaining hints, structured recovery on errors, and honest
telemetry. The main weak spot is payload redundancy in the default and batch
shapes — good trimming knobs exist, but the defaults leave tokens on the table.

### Ratings by dimension

| Dimension | Score | Evidence |
|-----------|-------|----------|
| **Discoverability** | 9 | `get_server_capabilities` with `lean`/`full`; embedded `recommended_workflows`, agreement verdicts, error taxonomy, interpretation bands, resource list; a built-in "Which tool?" disambiguator; `capabilities_version` hash lets a warm client skip re-fetching. Tool descriptions self-disambiguate ("ONE model only; use predict_splicing for BOTH") and warn about cost/latency. |
| **Token efficiency** | 7 | Strong primitives — three `response_mode`s, `include_hints=false`, `lean` capabilities (~1–2kB vs ~4kB), headlines that remove the need to parse structure. Held back by heavy defaults (see improvements 1–4). |
| **Speed** | 8 | Honest about 10–40s cold starts; offers `warmup`, a 24h cache (`cache_ttl_s: 86400`), server-side batch fan-out, and **background task support** to avoid blocking. Warm calls ~300–1500ms; cache hits effectively instant. Bounded by an upstream it cannot control, and handles that bound about as well as possible. |
| **Observability** | 9 | `request_id` on every call; **split timing** (`elapsed_ms` vs `upstream_elapsed_ms`); cache `hit`/`miss`/`partial` + `cache_age_s` + `served_warm`; batch `summary` with a full failure taxonomy (`ok` / `terminal_failed` / `retryable_failed` / `retried`). Among the best observed. |
| **Error handling** | 9.5 | Structured envelopes with `error_code`, `retryable`, `recovery_action`, `fallback_tool` + `fallback_args`, prose `recovery`, and `next_commands`. Per-item batch errors are isolated, not fatal. Ambiguous `rs6025` returned *both* alleles plus a ready next-command for each. |
| **Agentic chaining** | 9 | `next_commands` ({tool, arguments}), cross-server `see_also` (gnomad / genereviews / gtex / uniprot), fallback args on failure, and semantic `agreement` verdicts (`concordant_high`, `discordant_subthreshold`) instead of leaving the model to compare raw numbers. |
| **Safety framing** | 9 | `unsafe_for_clinical_use` on every response, `research_use_only`, explicit `threshold_basis` on each interpretation. |

### Obvious improvements

1. **De-duplicate echoed request params (biggest win).** In `predict_splicing`,
   `genome_build` / `gene_set` / `max_distance` / `mask` / `variant_id` are
   repeated at the envelope **and** inside `spliceai{}` **and** inside
   `pangolin{}` — 3× each. `max_delta_score` is echoed at model and transcript
   level. In a 25-variant batch this multiplies into real waste. Hoist shared
   request context to the envelope once and drop it from the sub-blocks.

2. **Trim constant / triplicated fields in `compact`.** `threshold_basis` is a
   fixed string already in capabilities; the three headlines (per-model ×2 +
   combined) overlap. Keep them in `full`, drop the per-model ones and the
   constant `threshold_basis` in `compact`.

3. **Add a `verdict-only` / `headline-only` mode for batch.** A gene-panel
   triage currently returns ~25× a full compact result. A mode returning just
   `{variant, gene, agreement.verdict, max_delta, headline}` per item would make
   panels dramatically cheaper, with drill-down via a single-variant call.

4. **Split `see_also` from `next_commands`.** `include_hints=false` drops both
   together, but `see_also` ships 4 cross-server entries on *every* predict.
   Gate it separately, or emit it once per session / only on the first call —
   the chaining `next_commands` is the part worth keeping hot.

5. **Validate (or at least flag) REF at resolve time.** `resolve_variant`
   documents that coordinate inputs are "normalized, not validated," so a wrong
   reference allele silently passes and only fails at prediction. Checking REF
   against the reference during resolution would surface the error one hop
   earlier.

6. **Expose a warmth / rate budget signal.** `served_warm` is a useful boolean,
   but a `warm_ttl_remaining_s` or a rate-limit-remaining hint would let the
   model pace a burst instead of discovering decay by cold-starting.

### Bottom line

The *contracts* — errors, observability, discovery, chaining — are excellent and
clearly LLM-aware. The main lever remaining is making the **default payload
shape** as lean as the available knobs already allow.

---

## Part 3 — Senior-Tester Test & Evaluation Report

**Method:** black-box, ~35 live calls across discovery, the full parameter
matrix, resolution robustness, the error taxonomy, concurrency, and
cross-build / cross-tool consistency.

### Verdict

**Production-quality, agent-aware, and unusually well-instrumented.** Every
happy path, every enum, and both boundary distances behave as documented; the
error envelopes are best-in-class; the capabilities doc is exhaustive and
internally consistent with the resources. No correctness bug was found in the
predictions — only a misleading doc string, a couple of consistency nits, and
one latency-shaped opportunity. **Score: 9/10 as a tested artifact.**

### Coverage exercised

| Area | Exercised | Result |
|------|-----------|--------|
| `get_server_capabilities` | `lean`, `full` | both; `full` documents concurrency / deadline / rate_budget / batch semantics |
| `resolve_variant` | rsID (2-allele), transcript HGVS, genomic HGVS, colon coords, GRCh37, wrong-REF coord | all resolve; wrong-REF coord passes through unchecked (see D1) |
| `predict_spliceai` | compact/minimal/full, raw/masked, mane/all, distance 1/500/10000, basic/comprehensive | all pass |
| `predict_pangolin` | full (standalone) | adds `ref_alt_scores` + `all_non_zero_scores` (see D3) |
| `predict_splicing` | compact, minimal, GRCh37 cross-build | verdicts: concordant_high / concordant_low / discordant_subthreshold |
| `predict_splicing_batch` | 3 items incl. malformed | per-item error isolated; summary taxonomy correct |
| `warmup` | raw + masked | measured 14.3s cold SpliceAI warm |
| Resources | capabilities / usage / reference / citations / research-use | all readable, consistent |
| Error codes reproduced | invalid_input, ref_mismatch, unsupported_contig, not_found, ambiguous (2- & 3-allele) | 6 of 10 |

Not force-reproduced (documented, hard to trigger deterministically):
`build_mismatch`, `rate_limited`, `upstream_unavailable`, `validation_failed`,
`internal_error`. A deliberate 4-call burst against the concurrency cap of 2
did **not** spuriously `rate_limited` — the 30s queue absorbed it, including a
24s cold call.

### Defects & findings (by severity)

| # | Sev | Finding | Evidence | Recommended fix |
|---|-----|---------|----------|-----------------|
| **D1** | **Med** | `resolve_variant` schema says a wrong REF "passes resolution and **only fails at prediction time**." (a) `predict_*` actually catches it **pre-flight in <0.5s**; (b) `resolve_variant` itself silently returns a **bogus `variant_id`** for a wrong-REF coordinate — a resolve-only caller gets a wrong answer with no warning. | `predict_spliceai chr8-140300616-A-G` → `ref_mismatch` in 479ms; `resolve_variant chr8-140300616-A-G` → `success`, `variant_id:"8-140300616-A-G"`, 0ms. Capabilities `resolve_caveat` contradicts the schema wording. | Reword the schema to match reality; run the same Ensembl reference-base pre-check inside `resolve_variant` (or return a `ref_warning`). |
| **D2** | Low | Out-of-range numeric contig misclassified. `chrM` → `unsupported_contig`, but `chr99` → `invalid_input`, though the doc defines `unsupported_contig` as "MT or non-standard." | `chr99-1000-A-G` → `invalid_input`; `chrM-100-A-G` → `unsupported_contig`. | Route well-formed-but-unknown contigs to `unsupported_contig`, or document the numeric-range → `invalid_input` rule. |
| **D3** | Low | Type inconsistency: Pangolin `full` returns `all_non_zero_scores` values as **strings** (`"0.92"`) while every other score is a float. | `predict_pangolin … response_mode=full` → `{"SL_REF":"0.92", …}`. | Emit floats for `SL_REF/SL_ALT/SG_REF/SG_ALT`. |
| **D4** | Low | `not_found` costs a full cold upstream round-trip (~20s); `ref_mismatch` is a <0.5s local pre-flight. The most common negative is the slowest error. | `9-22124478-A-G` → `not_found` after 20.4s. | Add a local transcript-overlap pre-check to fast-fail `not_found` (mirrors the REF pre-flight). Needs local gene-model data. |
| **D5** | Polish | Null-result headline reads awkwardly. | `max_distance=1` → `"TRAPPC9 — none acceptor gain (Δ=0.00 at +0 bp)"`. | For band `none`: `"no predicted splicing impact (max Δ=0.00)"`. |
| **D6** | Info | A `variant_id` is **not build-portable**: rs6025 → `1-169549811-C-T` (GRCh38) vs `1-169519049-T-C` (GRCh37); the REF base legitimately differs. Handled correctly (build-aware pre-flight passed each build's REF). | Two `resolve_variant` calls + the GRCh37 `predict_splicing` all succeeded with the build-correct REF. | One-line note in cross-build guidance. Not a bug. |

### Strengths verified (not just claimed)

- **Build-aware REF validation is correct** across GRCh37/GRCh38 — the strongest
  correctness signal in the run (D6).
- **Error UX is best-in-class**: every envelope carries `recovery_action`,
  `fallback_tool` + `fallback_args`, prose `recovery`, and `next_commands`;
  `ambiguous` returns *all* alleles (verified at 2 and 3 alleles) with a ready
  next-command per allele; batch isolates per-item failures and splits terminal
  vs retryable.
- **`masked` mode ships an explanatory `note`** when it suppresses an aberration
  that `raw` would show — a real guardrail against misreading the empty list.
- **`transcripts=all` collapses** 18 identical transcript blocks into one
  `shared_by:[…]` — genuine token savings.
- **Observability**: `request_id`, split `elapsed_ms` / `upstream_elapsed_ms`,
  `cache` hit/miss/partial + `cache_age_s` / `cache_ttl_s`, `served_warm` on
  every call.

### Prioritized recommendations

1. **(D1 — do first)** Fix the `resolve_variant` REF-validation contract: reword
   the schema *and* add a resolve-time REF check or warning. It's the only
   finding that can hand a caller a wrong result silently.
2. **(D3)** Make `all_non_zero_scores` numeric — trivial, removes a parsing trap.
3. **(D2)** Tighten contig classification (or document it).
4. **(D4)** Pre-flight `not_found` to cut ~20s off a common negative.
5. **(D5)** Polish the band-`none` headline.
6. **(carried from Part 2, reconfirmed)** Trim default-payload redundancy —
   `genome_build` / `gene_set` / `max_distance` / `mask` / `variant_id` are
   echoed at the envelope **and** each model sub-block; `threshold_basis`
   repeats every call.

### Reconciliation with Part 2

Deeper testing **retired three** of the Part 2 "improvement" suggestions — they
were already implemented and missed on the lean path:

- *"Expose a warmth / rate budget"* → already present: `_meta.rate_budget
  {limit, remaining, unit}` on `rate_limited`, plus a warmth-decay caveat in
  capabilities.
- *"Add a verdict-only batch mode"* → `response_mode=minimal` already yields a
  lean per-item shape (`gene` + `agreement.verdict` + `spliceai_max` /
  `pangolin_max` + headline); the batch tool accepts it.
- *"Validate REF at resolve time"* → `predict_*` already does (fast pre-flight);
  only `resolve_variant` itself still doesn't — which is exactly D1.

Net: the token-efficiency score (7) stands, but discoverability and
observability deserve the high marks — the `full` capabilities doc answers most
questions if read.
