# v0.9.0 Assessment Resolution — spliceailookup-link v0.10.0

**Date:** 2026-06-12
**Resolves:** `docs/mcp-assessment-v0.9.0-2026-06-12.md` (scored 9/10)
**Design / plan:** `docs/superpowers/specs/2026-06-12-mcp-v0.10.0-above-9.5-design.md`,
`docs/superpowers/plans/2026-06-12-mcp-v0.10.0-above-9.5.md`

Every concrete recommendation from the v0.9.0 assessment (Parts 1 and 2) is
addressed below. Tests are deterministic (respx / `StubService`); none hit the
live upstream. `make ci-local` passes.

## Two empirical findings up front

1. **Tool annotations were already correct.** The assessment's #1 item ("MCP tool
   annotations absent from all 7 schemas") was a **client-side observability gap**,
   not a code gap. All 7 tools have carried
   `readOnlyHint/idempotentHint/openWorldHint` since the initial commit, and
   FastMCP 3.4.2 serializes them into `tools/list` — verified by
   `test_all_tools_serialize_readonly_annotations`. The tester's client (Fable 5)
   simply did not surface annotations to the evaluating model. v0.10.0 therefore
   (a) locks the behavior with a regression test and (b) advertises read-only/
   idempotent status in the capabilities doc + server instructions, so a client
   that hides `tools/list` annotations still conveys the property.
2. **`structuredContent` already emitted; `outputSchema` deliberately declined.**
   FastMCP returns each dict result as `structured_content` (the June-2025 best
   practice). A rigid `outputSchema` was declined on purpose: these payloads are
   polymorphic by `response_mode` and partial-failure shape, so a fixed schema
   would fight the deliberate shaping.

## Recommendation → change → evidence

| v0.9.0 recommendation | Change (workstream) | Tests |
|---|---|---|
| **P2 Rec 1 / P1.1** Add tool annotations | Already present + serialized; locked in + `tool_safety` block in capabilities + instructions line (W1) | `test_all_tools_serialize_readonly_annotations`, `test_capabilities_advertises_tool_safety` |
| **P2 Rec 2 / Finding 1** Dedup batch by resolved `variant_id` | Resolve → group by canonical id → score unique once → re-expand; `summary.upstream_calls_saved` + `unique_variants`, `_meta.deduped`, per-copy `cache:"deduped"` + `served_from` (W2) | `test_batch_dedups_coordinate_and_hgvs`, `test_batch_distinct_variants_not_deduped`, updated `test_f12_*`, `test_batch_scores_each_variant_once_envelope` |
| **P2 Rec 3 / P1.2 / Finding 2** Model/build provenance in payloads | `provenance` block on `predict_spliceai/pangolin/splicing` (compact+full; omitted in minimal); once on the batch envelope; capabilities `data_sources` versioned (GENCODE v44 basic) from one source (W3) | `test_predict_*_carries_provenance`, `test_minimal_omits_provenance`, `test_batch_envelope_carries_provenance`, `test_capabilities_data_sources_versioned` |
| **P2 Finding 5** Nearest-transcript distance on `not_found` | Best-effort Ensembl overlap (≤100 kb) → `nearest_transcript` + recovery guidance (widen `max_distance` ≤10 kb, else "intergenic") (W4) | `test_not_found_includes_nearest_transcript`, `test_not_found_far_transcript_advises_intergenic`, `test_not_found_without_nearest_is_unchanged` |
| **P2 Finding 4 / Rec 4** Clarify `basic` gene-set scope | `gene_set` doc states GENCODE v44 basic includes non-coding genes (lncRNA) (W5) | `test_basic_gene_set_documents_noncoding` |
| **P1.3 / Token-efficiency** Hot-path `_meta` verbosity | Non-breaking: kept the deliberate v0.8.0 warm-client `capabilities_version` signal; surfaced the `include_hints=false` trim via `token_tips` + sharper field docs (W6) | `test_capabilities_has_token_tips`, `test_include_hints_false_drops_capabilities_version` |
| **P1.4** `warmup` coverage | `mask="both"` warms raw+masked in one call; `stay_warm_estimate_s` added; single-mask shape preserved (W7) | `test_warmup_default_reports_stay_warm_estimate`, `test_warmup_both_masks` |
| **P1.5** Client-supplied correlation id | Optional `correlation_id` on the 5 callable tools, echoed in `_meta.correlation_id` on success and error (W8) | `test_correlation_id_echoed_on_success/_on_error`, `test_no_correlation_id_means_no_field`, `test_correlation_id_on_resolve_and_batch` |

## Notes on scope discipline
- **W6 kept non-breaking.** Dropping `capabilities_version` from per-call `_meta`
  would have reversed a deliberate v0.8.0 decision (P1#1, with tests asserting its
  presence). The assessment offered surfacing the trim guidance as an
  alternative; that path was taken.
- **Provenance values** (`GENCODE v44 basic`, Broad-modified SpliceAI / refactored
  Pangolin, Ensembl VEP) reflect the documented Broad backend configuration and
  are env-overridable (`SPLICEAILOOKUP_LINK_GENCODE_VERSION`); the upstream does
  not assert versions per call, and `provenance.note` says so.
- **No live calls** were added to default CI; the nearest-transcript lookup is
  best-effort and degrades to prior behavior on any Ensembl fault.

Version bumped 0.9.0 → 0.10.0; the capabilities content hash changes accordingly
(new `tool_safety`/`token_tips`, versioned `data_sources`, doc clarifications).
