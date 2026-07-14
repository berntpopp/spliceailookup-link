# spliceailookup-link

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![CI](https://github.com/berntpopp/spliceailookup-link/actions/workflows/ci.yml/badge.svg)](https://github.com/berntpopp/spliceailookup-link/actions/workflows/ci.yml)
[![Conformance](https://github.com/berntpopp/spliceailookup-link/actions/workflows/conformance.yml/badge.svg)](https://github.com/berntpopp/spliceailookup-link/actions/workflows/conformance.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An MCP server (Streamable HTTP) for **splice-impact prediction**. It wraps the Broad
Institute's [SpliceAI Lookup](https://spliceailookup.broadinstitute.org) backends — the
same **SpliceAI**, **Pangolin** and **SpliceAI-10k** consequence predictions the website
surfaces — plus an Ensembl-VEP-backed variant resolver, as LLM-ergonomic tools.

> [!IMPORTANT]
> Research use only. Not clinical decision support. Do not use for diagnosis,
> treatment, triage, or patient management.

## Why

SpliceAI Lookup is a website with Cloud Run endpoints behind it, not an API you can
hand to an agent:

- **Its errors are HTTP 200.** A failure comes back as a normal `200` response with an
  `error` field in the JSON body, so a naive client reads "unparseable variant" as a
  result. This server inspects every body and returns a typed error envelope instead.
- **Scoring accepts only `CHROM-POS-REF-ALT`.** An HGVS string or an rsID has to be
  resolved first; `resolve_variant` does it via Ensembl VEP.
- **The two models live at two endpoints**, and the SpliceAI-10k consequence is buried
  inside the SpliceAI payload. `predict_splicing` runs both, surfaces the consequence,
  and says whether the models agree.
- **The upstream is fragile on purpose**: "interactive use only, several requests per
  user per minute", with cold calls taking 30s+. This server caches hard, caps
  concurrency, fast-fails wrong-build and no-transcript variants before the slow
  dispatch, paces callers via `_meta.rate_budget`, and emits progress notifications and
  MCP background tasks so an agent can fire-and-continue instead of blocking.

## Quick start

Hosted — no install:

```bash
claude mcp add --transport http spliceailookup-link https://spliceailookup-link.genefoundry.org/mcp
```

Local (Python 3.12+, [uv](https://github.com/astral-sh/uv)). There is no data build
step — the server proxies the live upstreams:

```bash
uv sync --group dev
cp .env.example .env      # optional: override upstream hosts, limits, allowlists
make dev                  # FastAPI /health + MCP at http://127.0.0.1:8603/mcp
claude mcp add --transport http spliceailookup-link http://127.0.0.1:8603/mcp
```

```text
predict_splicing(variant_id="chr8-140300616-T-G")
-> TRAPPC9 (GRCh38): SpliceAI Δ=0.83; Pangolin Δ=0.85; models agree; predicted exon skipping.
```

Streamable HTTP is the only transport — there is no stdio entry point, and TLS is
terminated at your proxy. See [Deployment](docs/deployment.md).

## Tools

| Tool | Purpose |
|---|---|
| `predict_splicing` | Front door: SpliceAI **and** Pangolin plus the SpliceAI-10k consequence, with model agreement |
| `predict_spliceai` | SpliceAI delta scores (optionally the SAI-10k consequence) |
| `predict_pangolin` | Pangolin splice gain/loss scores |
| `predict_splicing_batch` | Score a whole gene panel in one call; fanned out server-side, and one bad variant does not fail the batch |
| `resolve_variant` | HGVS / rsID / loose coordinates → canonical `CHROM-POS-REF-ALT` + gene + consequence |
| `get_server_capabilities` | Discovery: tools, parameters, score glossary, limits, citations, `capabilities_version` hash |
| `warmup` | Pre-warm the cold upstream Cloud Run containers before a burst |

Leaf names are unprefixed per **Tool-Naming Standard v1**, and the variant argument is
the fleet-canonical `variant_id` (`variant_ids` for the batch tool). `serverInfo.name`
is **`spliceailookup-link`**, but the router's namespace token is **`spliceai`**: behind
the [genefoundry-router](https://github.com/berntpopp/genefoundry-router) gateway these
surface as `spliceai_<tool>` — e.g. `spliceai_predict_splicing`. The gateway adds the
prefix; the leaf names stay bare.

## Data & provenance

Nothing is bundled or redistributed here: every prediction is computed on demand by the
upstream and cached in-process (`CACHE_TTL_MINUTES`, default 1440). Freshness tracks the
upstream; there is no snapshot to rebuild.

- **Scoring** — Broad Institute [SpliceAI Lookup](https://spliceailookup.broadinstitute.org)
  Cloud Run backends (SpliceAI, Pangolin, SpliceAI-10k), GRCh37 + GRCh38. Each response's
  `provenance` names the backend's transcript-annotation release.
- **Resolution** — [Ensembl VEP REST](https://rest.ensembl.org). Neither upstream needs
  an API key; both are interactive-use-only and rate-limited, which is why the defaults
  are conservative.

Cite the models, not this wrapper:

- **SpliceAI** — Jaganathan K, et al. *Cell* 2019;176(3):535-548. PMID:30661751.
- **Pangolin** — Zeng T, Li YI. *Genome Biology* 2022;23:103. PMID:35449021.
- **SpliceAI-10k** — Canson DM, et al. *Bioinformatics* 2023.
- **SpliceAI Lookup** — Broad Institute, https://spliceailookup.broadinstitute.org
- **Ensembl VEP** — https://rest.ensembl.org

## Documentation

- [Configuration](docs/configuration.md) — every `SPLICEAILOOKUP_LINK_*` variable, the tuned defaults and why they are low, and the CLI.
- [Deployment](docs/deployment.md) — transport, TLS, the exact Host/Origin allowlists, Docker, and multi-worker background tasks.
- [Architecture](docs/architecture.md) — what the facade absorbs, the response contract, and what is deliberately out of scope.
- [Data & upstreams](docs/data.md) — the upstream services, rate-limit etiquette, provenance and citations.
- [Upstream API contract](docs/API.md) — the reverse-engineered SpliceAI Lookup / VEP behaviour this server is built against.
- [AGENTS.md](AGENTS.md) — conventions for humans and coding agents working in this repo.

## Contributing

See [AGENTS.md](AGENTS.md) for repository conventions. `make ci-local` is the
definition-of-done gate: format, lint, line budget, README standard, typecheck, tests.

## License

[MIT](LICENSE) © 2026 Bernt Popp — the code in this repository. It grants no rights over
the upstream services' outputs: predictions come from the Broad SpliceAI Lookup service
and Ensembl VEP under **their** terms of use, and the model papers above must be cited.
See [Data & upstreams](docs/data.md).
