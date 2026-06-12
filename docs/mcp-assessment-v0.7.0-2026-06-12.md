# spliceailookup-link — MCP Consumer Evaluation & Senior-Tester Report (v0.7.0)

**Date:** 2026-06-12
**Server:** `spliceailookup-link` **v0.7.0**
**MCP protocol:** `2025-11-25`
**Evaluator:** Claude (acting first as an LLM consumer, then as a senior MCP tester)
**Method:** Live black-box testing through the MCP facade. All seven tools exercised across happy paths, every response mode, the parameter axes (mask, transcripts, gene_set, build), the resolver, batch, and deliberate error/edge paths. Evidence (request IDs, timings, scores) captured verbatim from responses (~25 live calls).

> Research-use-only server. Every payload carries `unsafe_for_clinical_use`. Nothing in this report is clinical guidance.
>
> Prior cycle: see `mcp-consumer-assessment-2026-06-12.md` and `mcp-tester-report-2026-06-12.md` (assessed v0.5.0). This document supersedes those for v0.7.0 and records findings not present in the earlier cycle (notably defect **D1**).

---

## Part 1 — Consumer-experience evaluation

As the LLM that has to drive this server, I exercised it across the dimensions an MCP should excel at before scoring: capabilities discovery (full + lean), a forced `not_found` error, a `minimal`/hints-off call to measure the token floor, an ambiguous rsID resolution, a warmup, and the reference resource.

### Overall: 8.5 / 10 — near reference-quality for an MCP

One of the better-instrumented MCP servers in use. The design clearly anticipates an LLM consumer rather than a human reading docs: every response is self-describing, errors say what to call next, and the token/latency knobs are real and labeled. What keeps it from 9–10 is upstream-bound cold-start latency and some `_meta` redundancy that an agent pays for on every call.

| Dimension | Score | Basis |
|---|---:|---|
| Discoverability | 9 | Layered: server instructions, `get_server_capabilities` (full/lean), two resources, rich self-disambiguating tool descriptions, `recommended_workflows`. |
| Observability | 9 | Every response carries `_meta` with `request_id`, `elapsed_ms`, `upstream_elapsed_ms`, `cache` hit/miss + age + TTL, `capabilities_version` hash. |
| Error handling | 9 | 10-code taxonomy, each with `retryable` + `recovery_action` + ready-to-call `fallback_args`; HTTP-200-error quirk handled correctly. |
| Ergonomics / chaining | 9 | `next_commands` are literal `{tool, arguments}`; `see_also` cross-server hints; HGVS/rsID auto-resolved inside `predict_*`; headline-first. |
| Safety / correctness | 9 | `research_use_only` everywhere; honest about the "normalized, not validated" coordinate gotcha. |
| Token efficiency | 8 | `response_mode` compact/full/minimal, `include_hints=false`, `detail=lean`, batch tool, explicit SEP-1576 de-duplication. |
| Speed / latency | 7 | Aggressive 24h cache (warm call ~0ms) + `warmup` + background tasks — but cold calls still block 13–40s, upstream-bound. |

### What it does notably well

- **Discoverability is layered and honest.** `detail=lean` returns the tool list, workflow recipes, agreement verdicts, error codes, and interpretation bands in ~1–2kB without re-stating per-parameter prose (explicitly citing SEP-1576). Tool descriptions say *when not to* use them ("ONE model only; use predict_splicing for BOTH"), the expected response size, and the cold-start cost.
- **Errors are actionable, not just classified.** A deliberate `chr8-999999999` miss returned `not_found` with `recovery_action: switch_tool`, pre-filled `fallback_args`, and prose suggesting `gene_set=comprehensive` or a wider window. Ambiguous `rs6025` returned both `variant_ids` *and* a `next_commands` entry per allele.
- **Observability is genuinely useful.** Separating `upstream_elapsed_ms` from total `elapsed_ms`, plus `cache: hit/miss` with `cache_age_s`, lets a client tell whether a slow call was the server or the upstream; the `capabilities_version` hash lets a warm client detect drift and skip re-fetching.

### Obvious improvements (consumer view)

1. **Trim `_meta` on the lean paths.** `response_mode=minimal` + `include_hints=false` still carries `cache_ttl_s`, `cache_age_s`, `capabilities_version`, and `unsafe_for_clinical_use`. Strip to `request_id` + timing in minimal/hints-off; emit `capabilities_version` only on change.
2. **Resolve the `ambiguous` success-vs-error inconsistency** (see D3).
3. **Give batch a truncation/size contract** ("up to ~25x a single compact result" has no stated cap; mirror the sibling gnomad-link truncation contract).
4. **Surface a "was this warm?" signal** (e.g. `served_warm`) so a client can choose blocking vs background without parsing `upstream_elapsed_ms`.
5. **List resource URIs in the lean capabilities output** — `spliceailookup://reference` is currently only discoverable via a buried `params_by_reference` note.

---

## Part 2 — Senior-tester report

**Scope:** 7/7 tools exercised across ~25 live calls — happy paths, every response mode, parameter variations (mask, transcripts, gene_set, build), and 6 of 10 error codes deliberately triggered.
**Verdict: production-quality, with one genuine correctness defect in build/REF disambiguation and one latency defect worth fixing.**

### Coverage

| Tool | Exercised | Result |
|---|---|---|
| `get_server_capabilities` | `lean` + `full` | Both render; `lean` ~1–2kB, `full` ~9.8kB, hash-versioned |
| `resolve_variant` | rsID, HGVS (paren form), garbage | VEP resolution correct; one semantic nit (D3) |
| `predict_spliceai` | raw, masked, minimal, full+`all`, errors | All modes; transcript-collapse + full payload correct |
| `predict_pangolin` | raw/basic | Signed-score direction reported |
| `predict_splicing` | dual-model | `agreement.verdict: concordant_high` correct |
| `predict_splicing_batch` | 4 mixed valid/invalid | Per-item errors isolated; summary buckets correct |
| `warmup` | GRCh38/raw | Per-model `elapsed_ms` + coverage |

**Error codes triggered:** `invalid_input`, `not_found`, `ref_mismatch`, `ambiguous`, `build_mismatch`, `unsupported_contig`.
**Not triggered** (by design — would require faults or abuse): `rate_limited`, `validation_failed`, `upstream_unavailable`, `internal_error`.
**Untested:** `gene_set=comprehensive` (documented 503/timeout risk — left to the maintainer's integration suite) and MCP background tasks (protocol-level `task` field not exposed by the test client).

### Defects

#### D1 — `build_mismatch` misclassifies a wrong REF allele as a wrong build. [Medium]

Same coordinate `8:140300616` (a real, repeatedly-scored GRCh38 locus; true REF is `T`), two wrong-REF inputs, two different diagnoses:

- `chr8-140300616-A-G` → `ref_mismatch` (correct; took 17.4s)
- `chr8-140300616-C-A` → `build_mismatch` ("appears to use GRCh37 coordinates"), recommends re-running on GRCh37, which then dead-ends at `not_found`.

The cross-build heuristic fires whenever the typo'd REF coincidentally matches the *other* build's base at that coordinate, producing confidently-wrong recovery guidance for a common error class (swapped/mistyped REF). It is also internally inconsistent — the same mistake yields `ref_mismatch` for one allele and `build_mismatch` for another.

Reproduction (verbatim):
- `predict_spliceai(variant="chr8-140300616-C-A")` → `build_mismatch`, `fallback_args={variant:"8-140300616-C-A", genome_build:"GRCh37"}`
- following that fallback → `predict_spliceai(variant="chr8-140300616-C-A", genome_build="GRCh37")` → `not_found` (dead end; user never learns the real issue is a one-char REF typo on GRCh38).

**Fix:** when the requested-build coordinate is itself valid/scorable, prefer `ref_mismatch`; only assert `build_mismatch` when the coordinate does not fit the requested build at all. If genuinely ambiguous, report `ref_mismatch` as primary and mention the other-build possibility as secondary rather than redirecting outright.

#### D2 — `ref_mismatch` detection costs ~17s. [Medium]

A wrong REF that does *not* match the other build (`A-G`) took **17.4s** to reject, versus 0.5s when the build-mismatch path short-circuits and 0ms for local `invalid_input`. The error message proves the server already knows the true reference base (`'T' at 8:140300616`), so it could reject locally — the 17s strongly implies the REF check happens at/after upstream dispatch.

**Fix:** do the reference-base check *before* dispatching to the scoring backend, ideally against a local faidx/2bit reference. This fails fast (<1s) *and* frees an upstream scoring slot (the concurrency budget is only 2).

#### D3 — `ambiguous` has split success/error semantics. [Low–Medium]

`resolve_variant("rs6025")` returns `success: true` with `ambiguous: true` **and a populated singular `variant_id` = the first allele**, while `predict_*`/batch return `success: false, error_code: "ambiguous"`. An agent that checks only `success` on the resolver will silently proceed with one of two alleles.

**Fix:** when ambiguous, set the singular `variant_id` to `null` (forcing a choice from `variant_ids[]`), or have the resolver also return a non-success envelope for consistency with the taxonomy that lists `ambiguous` as an error code.

#### D4 — `_meta` not trimmed on the lean paths. [Low]

`response_mode=minimal` + `include_hints=false` still carries `capabilities_version`, `cache_ttl_s`, and `unsafe_for_clinical_use` on every call. For high-volume agent use these bytes repeat. **Fix:** in minimal/hints-off, reduce `_meta` to `request_id` + timing; emit `capabilities_version` only when it changes.

#### D5 — `transcript_info.tx_start`/`tx_end` are `null` in full mode. [Low]

Both are `null` even though `exon_model.exon_starts[0]` / `exon_ends[-1]` carry the data. Minor completeness gap.

### What's genuinely strong

The error envelope is the best part — every failure carries `retryable`, `recovery_action`, a pre-filled `fallback_tool`/`fallback_args`, and prose; batch isolates per-item failures and correctly splits `terminal_failed` vs `retryable_failed`; the dual-model `agreement` verdict, masked-score suppression (with a `consequence.note` pointing back to raw), 17-transcript byte-identical collapse, and 24h caching (warm calls return in 0ms) all behaved exactly as the capabilities document promises. The `full` capabilities doc is unusually honest — it documents the concurrency cap (2), the soft deadline (55s), warmth decay, and the masked-vs-raw aberration subtlety up front.

### Prioritized recommendations

1. **Fix D1** — the build/REF disambiguation is the only defect that produces *misleading* output; everything else is latency or polish. Bias toward `ref_mismatch` when the requested-build coordinate is valid.
2. **Fix D2** — move the reference-base check local and ahead of upstream dispatch; biggest single UX win and it protects the scarce concurrency budget.
3. **Resolve D3** — make `ambiguous` behave consistently so agents can't silently pick an allele.
4. **D4/D5** — token and completeness polish; low effort, do opportunistically.
5. **Add integration coverage** for the two paths not safely drivable live: `gene_set=comprehensive` (verify graceful 503 → `upstream_unavailable` mapping) and `rate_limited` (verify the `_meta.rate_budget` shape when >2 fresh calls collide).

---

## Evidence appendix — observed timings

| Call | Latency | Notes |
|---|---:|---|
| `get_server_capabilities` (lean/full) | 0ms | Local, no upstream |
| Warm/cached prediction | 0ms | `cache: hit` |
| `predict_spliceai` masked (miss) | 0.42s | Warmed path |
| `predict_splicing` (dual) | 0.32s | `cache: partial` |
| `resolve_variant` rsID / HGVS | 0.8s / 0.9s | Ensembl VEP |
| `predict_splicing_batch` (4 items) | 15.8s | 1 cached + not_found probe + VEP resolve |
| `build_mismatch` | 0.5–1.4s | Cross-build probe short-circuits |
| **`ref_mismatch`** | **17.4s** | See D2 |

_All findings are from live calls against the running v0.7.0 server; research-use-only data, no clinical interpretation implied._
