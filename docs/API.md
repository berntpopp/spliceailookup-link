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
