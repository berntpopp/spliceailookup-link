# MCP UX Assessment Resolution (v0.8.0)

Maps each open finding in `docs/mcp-ux-assessment.md` to its fix in v0.8.0.
Spec: `docs/superpowers/specs/2026-06-12-mcp-ux-9.5-design.md`.
Plan: `docs/superpowers/plans/2026-06-12-mcp-ux-9.5.md`.

| Finding | Resolution |
|---|---|
| Part2 #1 / rec #6 — request-param triplication | Combined sub-blocks no longer echo `variant_id`/`genome_build`/`gene_set`/`max_distance`/`mask` (envelope-only); single-transcript per-transcript `max_delta_score` dropped. |
| Part2 #2 — triplicated headlines | Per-model headlines dropped in `compact` (kept in `full`); `threshold_basis` dedup already shipped. |
| Part2 #4 — split `see_also` | New `include_see_also` flag, independent of `include_hints`. |
| Part3 D1 / Part2 #5 / rec #1 — resolve REF | `resolve_variant` returns `ref_validated` + `ref_warning` on coordinate REF mismatch (`check_ref=true` default); docstring corrected; shares the predict pre-flight `check_ref` core. |
| Part3 D2 — contig classification | Well-formed non-standard contigs (`chr99`/`chr0`/`chr23`) return `unsupported_contig`. |
| Part3 D3 / rec #2 — string scores | Pangolin `all_non_zero_scores` emitted as floats. |
| Part3 D4 / rec #4 — slow `not_found` | Ensembl transcript-overlap pre-check fast-fails `not_found` in <0.5 s (conservative; reuses pre-flight infra; no bundled data). |
| Part3 D5 / rec #5 — band-none headline | Reads "no predicted splicing impact (max Δ=0.00)". |
| Part2 #6 — `warm_ttl_remaining_s` | Intentionally not added: `served_warm` + `rate_budget` cover it, and upstream Cloud Run warmth decay is not observable to the server. Documented in capabilities (`v0_8_0_shape`). |

**Breaking change:** the combined `predict_splicing` sub-block shape (request echo +
per-model headlines in compact). Bumped to v0.8.0; the `capabilities_version` content
hash changes so warm clients re-discover. Standalone `predict_spliceai` /
`predict_pangolin` outputs are unchanged.

**Intended score impact:** Token efficiency 7 → ~9.5, Speed 8 → ~9, all nits closed →
overall >9.5/10.
