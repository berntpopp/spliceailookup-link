# Architecture

FastAPI is a thin host that serves `/health` only — **the MCP facade is the product**.
The interesting code is the layer between a model's request and a fragile,
interactive-use-only upstream. This document is what that layer adds; the upstream's
own contract is reverse-engineered in [API.md](API.md).

```
MCP client ──▶ facade (spliceailookup_link/mcp/) ──▶ services ──▶ api/ (httpx)
                                                                   ├─▶ SpliceAI  (Cloud Run)
                                                                   ├─▶ Pangolin  (Cloud Run)
                                                                   └─▶ Ensembl VEP REST
```

## The upstream problems this facade absorbs

| Upstream reality | What the facade does |
|---|---|
| Errors are returned as **HTTP 200 with an `error` field in the body**, not as HTTP status codes. | Inspects every body and maps it to a typed error: *"Unable to parse variant"* → `invalid_input`; *"did not return any scores"* → `not_found`; 5xx / timeout → `upstream_unavailable`. |
| Scoring accepts only `CHROM-POS-REF-ALT`. | `resolve_variant` resolves HGVS / rsID / loose coordinates via Ensembl VEP first. Multi-allelic rsIDs return a structured `variant_ids` list — never a stringified one — plus a note telling the caller to pick one. |
| SpliceAI and Pangolin are separate endpoints, and the SpliceAI-10k consequence is buried under `sai10kPredictions` in the SpliceAI payload. | `predict_splicing` runs both models in one call, surfaces the consequence prediction (exon skipping, intron retention, frameshift), and reports model `agreement`. |
| A wrong genome build silently yields `not_found` after a slow round-trip. | Build-mismatch **pre-flight**, plus an opportunistic **cross-build probe** fired on `not_found` — so a GRCh37 coordinate sent as GRCh38 is diagnosed instead of merely failing. |
| Cold Cloud Run calls take 30s+ and the service is rate-limited. | Aggressive caching, a low concurrency cap, two pre-flight fast-fails, a soft foreground deadline, client pacing (`_meta.rate_budget`), and `warmup`. |

The two pre-flights (`PREFLIGHT_REF_CHECK_ENABLED`, `PREFLIGHT_OVERLAP_CHECK_ENABLED`)
each trade one cheap Ensembl lookup for a slow, useless scoring round-trip, and are
deliberately conservative: an inconclusive answer always falls through to real scoring,
so they never invent a `not_found`. See [Configuration](configuration.md).

## Worked examples

`variant_id` takes any supported form — HGVS, rsID or coordinates — because resolution
happens before scoring:

```text
predict_splicing(variant_id="NM_001089.3(ABCA3):c.875A>T", genome_build="GRCh38")
-> headline: "ABCA3 (GRCh38): SpliceAI Δ=0.02; Pangolin Δ=0.05; models agree."

predict_splicing(variant_id="chr8-140300616-T-G")
-> headline: "TRAPPC9 (GRCh38): SpliceAI Δ=0.83; Pangolin Δ=0.85; models agree; predicted exon skipping."
```

## Long-running calls are first-class

Every prediction tool emits MCP **progress notifications** and opts into the 2025-11-25
**background-task protocol** (`task=True`), so an agent can fire-and-continue instead
of blocking on a 30s+ cold call. `_meta.served_warm` tells a client whether the last
comparable call was fast, which is the signal for choosing blocking vs. background
execution. Background tasks bypass the foreground soft deadline.

The task backend is Docket; the single-process default is in-memory. Multi-worker
deployments need Redis — see [Deployment](deployment.md#multi-worker-deployments).

## Response contract

- **`response_mode`** — the fleet ladder `minimal | compact | standard | full`. Start
  compact and widen only if needed.
- **Domain parameter surface** — GRCh37 and GRCh38; `raw` or `masked` scores;
  `basic` or `comprehensive` gene sets; MANE-only or all transcripts.
- **`_meta` on every response** — `request_id` and `timing.elapsed_ms`; prediction
  payloads add `cache` (`hit` / `miss` / `partial`), `upstream_elapsed_ms`,
  `served_warm`, and `rate_budget`.
- **`_meta.next_commands`** — ready-to-run follow-up calls, present on success *and*
  on error, so a model can recover without guessing.
- **`see_also`** — cross-server hints to the sibling `-link` servers (gnomad-link,
  genereviews-link, gtex-link) for questions this server deliberately does not answer.
- **`get_server_capabilities`** returns a `capabilities_version` content hash, so a
  warm client can compare it and skip re-fetching the descriptor.
- **Batch semantics** — `predict_splicing_batch` fans out server-side under the
  concurrency cap; a per-variant failure is embedded in the successful batch envelope
  rather than failing its siblings.

Envelope shapes follow the GeneFoundry Response-Envelope Standard v1.

## Scope & boundaries

**In scope:** SpliceAI / Pangolin / SpliceAI-10k splice prediction, and variant
resolution in service of it.

**Out of scope, delegated to siblings:** allele frequency and ClinVar
(`gnomad-link`), gene–disease context (`genereviews-link`), expression (`gtex-link`),
liftover (`gnomad-link`), and the AlphaMissense / PrimateAI / PromoterAI / CADD
annotations also shown on the SpliceAI Lookup website. This server hints at them via
`see_also` instead of reimplementing them.

## Code layout

| Path | Responsibility |
|---|---|
| `spliceailookup_link/mcp/` | The hand-authored MCP facade: tools, resources, error envelopes. Owns tool names and schemas. |
| `spliceailookup_link/api/` | `httpx` clients for the scoring APIs and Ensembl VEP. |
| `spliceailookup_link/services/` | Orchestration: caching, concurrency, pre-flights, telemetry. |
| `spliceailookup_link/` (root) | Config, variant parsing, transports, CLI. |
| `tests/unit/` | Deterministic, `respx`-mocked. `tests/integration/` hits the live upstream and is marked `integration`. |

Conventions, the 600-line module budget, and the upstream contract's load-bearing
facts live in [AGENTS.md](../AGENTS.md).
