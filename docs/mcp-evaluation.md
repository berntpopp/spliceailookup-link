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

*Research use only; not for clinical decision support. Splice predictions are
computational and must be interpreted alongside orthogonal evidence.*
