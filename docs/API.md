# SpliceAI Lookup — Reverse-Engineered API Contract

Captured 2026-06-11 from `https://spliceailookup.broadinstitute.org/` via Playwright
network interception + direct `curl` probes. Source of truth confirmed against
`github.com/broadinstitute/SpliceAI-lookup` (`google_cloud_run_services/server.py`,
`sai10k_predictions.py`).

## 1. Architecture of the live site

The web app is a **client-side orchestrator** (`index.html`, vanilla JS) that fans out to:

| Backend | Purpose | Owned by | In MCP scope? |
|---|---|---|---|
| `spliceai-{37,38}-xwkwwwxdwq-uc.a.run.app/spliceai/` | SpliceAI delta scores + SAI-10k consequence | Broad | **YES (core)** |
| `pangolin-{37,38}-xwkwwwxdwq-uc.a.run.app/pangolin/` | Pangolin splice gain/loss | Broad | **YES (core)** |
| `rest.ensembl.org/vep/human/hgvs/...` | HGVS/rsID → genomic `chrom-pos-ref-alt` + consequence | Ensembl | **YES (resolver)** |
| `myvariant.info/v1/variant/...?fields=dbnsfp` | CADD / dbNSFP scores | myvariant.info | No (3rd-party) |
| `gnomad.broadinstitute.org/api` (GraphQL) | allele frequencies, gene model | Broad | No → delegate to `gnomad-link` |
| `storage.googleapis.com/spliceai-lookup-reference-data/*.tsv.gz` | AlphaMissense, PrimateAI, PromoterAI (tabix byte-range) | Broad (static) | Future (needs client-side tabix) |

**Decision:** the MCP wraps the *splice-prediction core* (SpliceAI + Pangolin + SAI-10k
consequence) plus an Ensembl-VEP-backed variant resolver. Everything else is delegated
to existing siblings or marked future.

## 2. Scoring endpoints (SpliceAI & Pangolin)

`GET https://spliceai-38-xwkwwwxdwq-uc.a.run.app/spliceai/?<params>`
`GET https://pangolin-38-xwkwwwxdwq-uc.a.run.app/pangolin/?<params>`
(`-37-` hosts for GRCh37). Docker images: `weisburd/{spliceai,pangolin}-{37,38}`.

### Query parameters
| Param | Req | Type | API default | Site value | Meaning |
|---|---|---|---|---|---|
| `variant` | yes | str | — | `8-140300616-T-G` | `chrom-pos-ref-alt`; `chr` prefix optional |
| `hg` | yes | 37\|38 | — | 38 | genome build (also baked into host) |
| `distance` | no | int | **50** | 500 | max nt distance scanned; ≤~10000 for full SAI-10k |
| `mask` | no | 0\|1 | 0 | 0 | 0=raw, 1=masked (mask = hide gains at annotated / losses at unannotated sites). Recommend raw for alt-splicing, masked for variant interpretation. |
| `bc` | no | basic\|comprehensive | basic | basic | GENCODE gene set; **comprehensive is much slower** |
| `raw` | no | str | — | original input | echo of user's raw input string (cosmetic) |
| `variant_consequence` | no | str | — | VEP consequence | optional VEP `most_severe_consequence`, used to refine SAI-10k |

### SpliceAI success response (top-level keys)
`variant, hg, bc, distance, mask, raw, variant_consequence, genomeVersion, chrom, pos,
ref, alt, scores[], source, allNonZeroScores[], allNonZeroScoresStrand,
allNonZeroScoresTranscriptId, sai10kPredictions{}, sai10kPredictionsError`

`scores[]` item (one per transcript; MANE first):
`DS_AG, DS_AL, DS_DG, DS_DL` (delta scores: Acceptor/Donor Gain/Loss, 0–1),
`DP_AG, DP_AL, DP_DG, DP_DL` (delta positions, nt rel. to variant),
`DS_{AG,AL,DG,DL}_REF`, `DS_{AG,AL,DG,DL}_ALT` (raw ref/alt site scores),
`SCORES_FOR_INSERTED_BASES[]`,
`g_id` (ENSG), `g_name` (symbol), `t_id` (ENST), `t_priority` (`MS`=MANE Select),
`t_refseq_ids[]`, `t_strand` (+/-), `t_type`, `EXON_STARTS[]`, `EXON_ENDS[]`,
`CDS_START`, `CDS_END`.

`sai10kPredictions.aberrations[]` item:
`aberration_type` (e.g. `exon_skipping`, intron retention, ...),
`affected_region{region_type, region_number, distance_to_boundary, nearest_boundary}`,
plus coding/consequence fields: `size_is_coding`, `consequence`, `status`
(e.g. `frameshift`), `introduces_stop_codon`, `extension...`.

### Pangolin success response
Top-level mirrors SpliceAI. `scores[]` item:
`DS_SG, DS_SL` (splice gain/loss delta; loss is negative),
`DP_SG, DP_SL` (positions), `SG_REF, SG_ALT, SL_REF, SL_ALT`,
`g_id, g_name, t_id, t_priority, t_refseq_ids, t_strand, t_type`.
Plus `source` (`pangolin:model:cache`), `allNonZeroScores[]`,
`allNonZeroScoresStrand`, `allNonZeroScoresTranscriptId`.

### Error model — **HTTP 200 with an `error` field** (not HTTP error codes)
```json
{"variant":"...","hg":"38","distance":"50","mask":"0","source":"spliceai",
 "error":"Unable to parse variant: notavariant"}
```
Observed errors:
- `Unable to parse variant: <x>` (bad format / impossible position)
- `The SpliceAI model did not return any scores for <x>. This may be because the variant
  does not overlap any exons or introns defined by the GENCODE 'basic' annotation.`

### Timing & limits (measured)
- cached / error: ~0.4 s; normal: ~13–22 s; `comprehensive`: ~36 s.
- `distance=500 & bc=comprehensive`: **HTTP 503** (timed out at ~42 s) — heaviest combo.
- Documented rate limit: *"intended for interactive use only … not more than several
  requests per user per minute."* → MCP must rate-limit, cache, and carry a research-use notice.

## 3. Variant resolver (Ensembl VEP)
`GET https://rest.ensembl.org/vep/human/hgvs/<HGVS>?vcf_string=1` (GRCh38;
`grch37.rest.ensembl.org` for GRCh37). Accepts transcript HGVS (`NM_...:c....`),
genomic HGVS (`8:g....`). Example:
`NM_001089.3:c.875A>T` → `seq_region_name=16, vcf_string="16-2317763-T-A",
most_severe_consequence="missense_variant", assembly_name="GRCh38"`.
The `vcf_string` is exactly the `chrom-pos-ref-alt` the scoring API needs; the
`most_severe_consequence` feeds the scoring `variant_consequence` param.

Input normalization the site performs before scoring:
- strip `(GENE)` and ` (p.Xxx)` annotations from HGVS,
- whitespace/tab-delimited `6\t31740453\tG\tT` → `6-31740453-G-T`,
- `chrN-pos-ref-alt` → `N-pos-ref-alt` (strip `chr`),
- HGVS / rsID → VEP → `vcf_string`.

All three task examples are **hg38**:
`NM_001089.3(ABCA3):c.875A>T` → `16-2317763-T-A`; `chr8-140300616-T-G`;
`6 31740453 G T` → `6-31740453-G-T`.

## 4. MCP facade response contract (this server, not upstream)

The sections above describe the *upstream* APIs. The MCP facade reshapes them; the
authoritative facade contract is `get_server_capabilities` / `spliceailookup://capabilities`
and `spliceailookup://reference`. Key facade behaviors:

- **`_meta` observability.** Every envelope carries `request_id`, `timing.elapsed_ms`,
  and `served_warm` (true on a cache hit or a sub-cold-start upstream answer —
  `WARM_THRESHOLD_MS`, default 5 s — so a client can choose blocking vs a background
  task without parsing `upstream_elapsed_ms`). Prediction payloads add
  `cache` (`hit`|`miss`|`partial`) and, on a miss, `upstream_elapsed_ms`.
- **Lean `_meta`.** When `response_mode=minimal` or `include_hints=false`, the
  repetitive `capabilities_version`, `cache_ttl_s`, and `cache_age_s` are dropped to
  save tokens; `request_id`, `timing`, `cache`, `served_warm`, and the
  `unsafe_for_clinical_use` research-use flag are always kept. Fetch
  `capabilities_version` from `get_server_capabilities` when needed.
- **`ref_mismatch` is pre-flight and fast.** A coordinate whose REF does not match the
  requested build's reference base is rejected as `ref_mismatch` *before* the slow
  scoring dispatch (an Ensembl reference-base check, ~sub-second), instead of a
  misleading ~17 s `not_found`. If the REF happens to match the other build's base,
  the error carries a secondary `other_build_hint` but stays a `ref_mismatch` — it is
  **not** redirected to `build_mismatch`. `build_mismatch` fires only when the position
  cannot belong to the requested build (out of chromosome range, or the variant only
  scores on the other build).
- **`resolve_variant` ambiguity.** When an input maps to multiple ALT alleles, the
  singular `variant_id` is `null` (so a caller cannot silently pick one); the candidates
  are in `variant_ids[]` with one `next_commands` entry per allele.
- **`resolve_variant` REF check (v0.8.0).** Coordinate inputs are validated against the
  requested build by default (`check_ref=true`, one Ensembl lookup): `ref_validated:true`
  on a match, `ref_validated:false` + a `ref_warning` on mismatch (still returning the
  normalized `variant_id` — `resolve` normalizes, it does not block). Set `check_ref=false`
  to skip the lookup. HGVS/rsIDs are resolved and validated via VEP.
- **Lean combined shape (v0.8.0, breaking).** `predict_splicing` carries the request
  params (`variant_id`/`genome_build`/`gene_set`/`max_distance`/`mask`) on the **envelope
  only**; the `spliceai{}`/`pangolin{}` sub-blocks no longer repeat them, and per-model
  headlines appear only in `response_mode=full`. Standalone `predict_spliceai` /
  `predict_pangolin` keep the request params. `include_see_also` (default true) gates the
  cross-server `see_also` hints independently of `include_hints`.
- **Contig classification (v0.8.0).** A well-formed coordinate on a non-standard contig
  (e.g. `chr99`, `chr0`, `chr23`) returns `unsupported_contig`, not `invalid_input`.
- **Fast-fail `not_found` (v0.8.0).** A coordinate with no transcript overlapping
  `[pos-max_distance, pos+max_distance]` (Ensembl overlap pre-check) returns `not_found`
  in <0.5 s instead of a ~20 s cold round-trip. Conservative: any inconclusive/non-zero
  overlap falls through to real scoring, so the pre-check never invents a `not_found`.
- **Numeric scores (v0.8.0).** Pangolin `all_non_zero_scores` values are floats (were
  strings). Pangolin `signed_score` preserves the original negative magnitude.
- **`tx_start`/`tx_end`.** In `response_mode=full`, `transcripts[].exon_model` and
  `consequence.transcript_info` carry `tx_start`/`tx_end` derived from the exon arrays
  (`min(EXON_STARTS)` / `max(EXON_ENDS)`) when upstream leaves them null.
- **Batch size contract.** `predict_splicing_batch` accepts `max_items=25`; more returns
  `validation_failed` (enforced, not truncated). The envelope `_meta` echoes
  `items_submitted` and `max_items`; each item is ≈ one compact `predict_splicing` result.
