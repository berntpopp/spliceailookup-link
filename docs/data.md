# Data & upstreams

This server ships **no data**. There is no bundle, no snapshot, no SQLite index and no
`make data` step: every prediction is computed on demand by the upstream service and
cached in-process. Freshness therefore tracks the upstream exactly, and there is
nothing to rebuild.

## Upstream services

| Upstream | Used for | Auth |
|---|---|---|
| [SpliceAI Lookup](https://spliceailookup.broadinstitute.org) (Broad Institute) — SpliceAI, Pangolin and SpliceAI-10k Cloud Run backends | Splice-impact scoring on GRCh37 and GRCh38 | None |
| [Ensembl VEP REST](https://rest.ensembl.org) (`grch37.rest.ensembl.org` for GRCh37) | Variant resolution (HGVS / rsID → `CHROM-POS-REF-ALT`), reference-base and transcript-overlap pre-flights | None |

Neither upstream requires an API key. Both are reached over plain HTTPS with the
`USER_AGENT` from [Configuration](configuration.md).

## Upstream etiquette (load-bearing)

The Broad backends are documented as **"interactive use only, several requests per
user per minute"**, and an individual call to a cold Cloud Run container can take
30s+. Everything below follows from that, and none of it is decorative:

- `MAX_CONCURRENCY` defaults to **2**. Raising it is how you get rate-limited.
- `CACHE_TTL_MINUTES` defaults to **1440** (24h). Scores are deterministic per
  `(model, build, variant, distance, mask, gene_set)`, so a long TTL is safe and is
  the main thing keeping load off the upstream.
- `RATE_BUDGET_MIN_INTERVAL_MS` (**12000**) is published to callers as
  `_meta.rate_budget.min_interval_ms` so an autonomous agent paces a burst *proactively*
  instead of discovering the limit by hitting it.
- `warmup` exists so a client can pay the cold-start cost once, deliberately, before a
  burst — rather than inside the first real prediction.

Do not "optimise" these defaults without a reason that survives contact with the
upstream's terms.

## Provenance

Every prediction carries a `provenance` block naming the models and the transcript
annotation release of the Broad backend (`GENCODE_VERSION`, default `v44`, and
env-overridable so an operator can bump it when the backend updates without a code
change). The [upstream API contract](API.md) documents the endpoints, parameters and
the HTTP-200-with-an-`error`-field error model these values come from.

## Citation

Cite the **models and services**, not this wrapper. `get_server_capabilities` returns
the same list:

- **SpliceAI** — Jaganathan K, et al. Predicting Splicing from Primary Sequence with
  Deep Learning. *Cell* 2019;176(3):535-548. PMID:30661751.
- **Pangolin** — Zeng T, Li YI. Predicting RNA splicing from DNA sequence using
  Pangolin. *Genome Biology* 2022;23:103. PMID:35449021.
- **SpliceAI-10k** — Canson DM, et al. *Bioinformatics* 2023.
- **SpliceAI Lookup** — Broad Institute. https://spliceailookup.broadinstitute.org
- **Ensembl VEP** — https://rest.ensembl.org

## Terms

The code in this repository is MIT-licensed (see [LICENSE](../LICENSE)). It grants no
rights over the upstream services' outputs: predictions are produced by the Broad
SpliceAI Lookup service and Ensembl VEP under **their** terms of use, which you are
responsible for observing. Consult the upstream projects for those terms.

**Research use only. Not clinical decision support.** Mirror this disclaimer wherever
you surface these predictions.
