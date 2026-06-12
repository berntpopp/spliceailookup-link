# MCP Assessment Resolution (v0.8.0 → v0.9.0)

Maps each finding in `docs/mcp-assessment-v0.8.0-2026-06-12.md` (overall 8/10) to its
fix in v0.9.0.

- Spec: `docs/superpowers/specs/2026-06-12-assessment-v0.8.0-fixes-design.md`
- Plan: `docs/superpowers/plans/2026-06-12-assessment-v0.8.0-fixes.md`

| Finding | Resolution |
|---|---|
| **F1 [Bug]** — out-of-range coord costs ~15s, returns `not_found` | `build_check.out_of_range` + `CoordinateRangeError`: a position beyond **both** builds' chromosome lengths fast-fails locally (<1ms) as **`invalid_input`** with both lengths in the message, before any Ensembl/scoring call. (Not `build_mismatch` — neither build can score it, so "switch build" would loop.) Capabilities/reference docs corrected. |
| **F2 [UX]** — `ref_mismatch` re-suggests `resolve_variant` with the same wrong-REF coord | `_ref_mismatch_fallback`: other-build hint → re-run the same predict tool on the matching `genome_build`; ALT == reference base → re-run with REF/ALT swapped (+ swap note in `recovery`); otherwise → `get_server_capabilities`. The dead-end `resolve_variant` echo is gone (ref_mismatch only fires on coordinate inputs, which `resolve_variant` cannot rescue). |
| **F3 [Consistency]** — same fact, different field names/locations per mode | Single-model: `top:{class,score,position}` + `max_delta_score` now in **every** mode (minimal/compact/full). Combined: `agreement:{verdict, spliceai_max_delta, pangolin_max_delta}` in **every** mode; the divergent minimal-only `spliceai_max`/`pangolin_max` are removed. One name, one location. |
| **F4 [Cross-server hint]** — gtex `see_also` passed a gene *symbol* into `gencode_id` | The resolved Ensembl `gene_id` is threaded through telemetry; gtex now gets `get_median_expression_levels({gencode_id:[ENSG…]})`, or `search_gtex_genes({query:symbol})` when only a symbol is known. The hint is runnable. |
| **F5a [Polish]** — symbol-less lncRNA headline printed a bare `ENSG…` | `_gene_label` renders `ENSG… (no gene symbol)` across single-model and combined headlines. |
| **F5b [Polish]** — batch items had no per-item `request_id` | Each batch item carries a unique `request_id` (success in `_meta`, error at top level) for log correlation. |
| **F6 [Token]** — `threshold_basis` glossary on every compact+full payload | Gated to `response_mode='full'` only; `interpretation.band` stays in every mode. The glossary remains in capabilities + `spliceailookup://reference`. |
| **Part 1 #1** — `capabilities_version` duplicated top-level **and** in `_meta` | `run_mcp_tool._stamp` no longer re-injects it into `_meta` when the payload already carries a top-level copy (i.e. the capabilities call). Prediction payloads keep their `_meta` provenance. |
| **Part 1 #2** — no proactive rate-limit headroom on success | `_meta.rate_budget = {limit, unit, min_interval_ms}` on every prediction success (incl. minimal + the batch envelope) advertises a soft client-pacing interval over the local concurrency semaphore. `rate_limited` errors additionally carry `remaining:0` + `retry_after_s` (per current MCP rate-limit guidance). New tunable `RATE_BUDGET_MIN_INTERVAL_MS` (default 12000). |
| **Part 1 #3** — sticky hint-suppression / lifecycle | Documented in capabilities `response_fields.hint_lifecycle` (stateless server ⇒ doc-only is the honest fix): after the first call, set `include_hints=false`/`include_see_also=false` for the session. |
| **Part 1 #5** — unify cross-mode shape | Same as F3 (single-model `top`; combined `agreement.*_max_delta`). |

## Breaking change

The F3 response-shape unification changes existing keys (removes minimal-only
`spliceai_max`/`pangolin_max`; moves the per-model maxes into `agreement{}`; adds `top`
to compact/full single-model; makes `threshold_basis` full-only). Bumped to **v0.9.0**;
the `capabilities_version` content hash recomputes, so warm clients re-discover.
Standalone vs combined request-echo behavior is preserved.

## Intended score impact

| Dimension | v0.8.0 | Target |
|---|---|---|
| Discoverability | 9 | 9–10 (hold; + hint_lifecycle) |
| Error handling | 9 | 9.5+ (F1 bug + F2 dead-end fixed) |
| Safety / compliance | 9 | 9–10 (hold) |
| Observability | 8 | 9.5+ (proactive rate_budget + retry_after + per-item request_id) |
| Token efficiency | 8 | 9.5+ (F6 + P1#1 + F3 dedup) |
| Speed | 7 | ~8 on the affected path (F1 removes a ~15s wasted call; pacing prevents retry-storms) |

**Overall target: >9.5/10.**
