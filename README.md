# spliceailookup-link

An MCP (Model Context Protocol) + REST server that grounds **splicing-impact
prediction** for genetic variants in the Broad Institute's
[SpliceAI Lookup](https://spliceailookup.broadinstitute.org) backends — the same
**SpliceAI**, **Pangolin**, and **SpliceAI-10k** consequence predictions the
website surfaces — exposed as LLM-ergonomic tools.

Part of the `-link` family (gnomad-link, gtex-link, pubtator-link,
genereviews-link) and built to the same conventions: FastMCP, a thin FastAPI
host, structured error envelopes, `_meta.next_commands` chaining, capabilities
discovery, and a research-use disclaimer.

> Research use only; not for clinical decision support.

## Features

- **predict_splicing** — one call resolves the variant, runs SpliceAI **and**
  Pangolin, includes the SpliceAI-10k consequence prediction (exon skipping,
  intron retention, frameshift), and reports whether the two models agree.
- **predict_spliceai** / **predict_pangolin** — single-model delta scores.
- **predict_splicing_batch** — score a whole gene panel in one call (server-side
  fan-out under the concurrency cap; per-variant errors don't fail the batch).
- **resolve_variant** — HGVS / rsID / loose coordinates → canonical
  `CHROM-POS-REF-ALT` via Ensembl VEP (also returns gene + consequence;
  multi-allelic rsIDs return a structured `variant_ids` list, never a stringified one).
- **get_server_capabilities** — tools, parameters, score glossary, limits, plus a
  `capabilities_version` content hash so warm clients can skip re-fetching it.
- **warmup** — pre-warm the cold upstream before a burst.
- GRCh37 + GRCh38; `raw`/`masked`; `basic`/`comprehensive` gene sets;
  MANE-only or all transcripts; `compact`/`full`/`minimal` responses.
- Build-mismatch pre-flight **and** an opportunistic cross-build probe on
  `not_found`, aggressive caching, conservative rate limiting (the upstream is
  interactive-use-only), and cross-server `see_also` hints to gnomad-link /
  genereviews-link / gtex-link.
- **Long-running calls are first-class**: every prediction tool emits MCP
  progress notifications and opts into the 2025-11-25 background-task protocol
  (`task=True`), so an agent can fire-and-continue instead of blocking on a 30 s+
  cold call.
- **Runtime observability**: every `_meta` carries `request_id` and
  `timing.elapsed_ms`; prediction payloads add `cache` (`hit`/`miss`/`partial`)
  and `upstream_elapsed_ms`.

## Quick start

```bash
uv sync --group dev          # install
cp .env.example .env         # optional: override hosts / limits
make dev                     # FastAPI /health + MCP HTTP at http://127.0.0.1:8603/mcp
# equivalently: uv run spliceailookup-link serve --transport unified --port 8603
```

## MCP integration

The recommended transport is **streamable HTTP over HTTPS**, matching the sibling
`-link` servers (gnomad-link, gtex-link, pubtator-link, genereviews-link). Put the
server behind a TLS-terminating reverse proxy and connect to its `https://` URL.

Hosted (HTTPS — Claude Code):

```bash
claude mcp add --transport http spliceailookup-link https://spliceailookup-link.example.org/mcp
```

Hosted (HTTPS — Claude Desktop / claude.ai connectors, `claude_desktop_config.json`):

```json
{ "mcpServers": { "spliceailookup-link": { "type": "http", "url": "https://spliceailookup-link.example.org/mcp" } } }
```

Local development (HTTP on loopback only):

```bash
make dev   # serves http://127.0.0.1:8603/mcp
claude mcp add --transport http spliceailookup-link http://127.0.0.1:8603/mcp
```

> Streamable HTTP is the only transport — there is no stdio entry point. TLS is
> terminated at your proxy (nginx / Caddy / npm); the app itself serves plain
> HTTP on its port, exactly like the sibling `-link` deployments.

## Example

```text
predict_splicing(variant_id="NM_001089.3(ABCA3):c.875A>T", genome_build="GRCh38")
# -> headline: "ABCA3 (GRCh38): SpliceAI Δ=0.02; Pangolin Δ=0.05; models agree."
predict_splicing(variant_id="chr8-140300616-T-G")
# -> headline: "TRAPPC9 (GRCh38): SpliceAI Δ=0.83; Pangolin Δ=0.85; models agree; predicted exon skipping."
```

## Tools

| Tool | Purpose |
|---|---|
| `get_server_capabilities` | Discovery: tools, parameters, glossary, limits, citations |
| `resolve_variant` | HGVS / rsID / loose input → `CHROM-POS-REF-ALT` + gene + consequence |
| `predict_spliceai` | SpliceAI delta scores (+ optional SAI-10k consequence) |
| `predict_pangolin` | Pangolin splice gain/loss scores |
| `predict_splicing` | Combined SpliceAI + Pangolin + consequence (headline tool) |
| `predict_splicing_batch` | Score many variants (gene panel) in one envelope, fanned out server-side |
| `warmup` | Pre-warm the upstream Cloud Run containers before a burst |

Tool names follow the **GeneFoundry Tool-Naming & Normalization Standard v1**:
leaf tools are unprefixed `verb_noun` snake_case, the variant identifier argument
is the fleet-canonical `variant_id` (`variant_ids` for the batch tool), and
`response_mode` uses the `minimal | compact | standard | full` ladder.

## GeneFoundry router namespace

`serverInfo.name` is the stable server identity **`spliceailookup-link`**. When
federated behind the [`genefoundry-router`](https://github.com/berntpopp/genefoundry-router)
gateway, this server is mounted under the namespace token **`spliceai`**
(`mount(namespace="spliceai")`), so leaf tools surface at the gateway as
`spliceai_<tool>` — e.g. `predict_splicing` → `spliceai_predict_splicing`. Leaf
tools therefore stay unprefixed; the gateway adds the `spliceai_` prefix.

## Configuration

All environment variables are prefixed `SPLICEAILOOKUP_LINK_` (see `.env.example`).
Key knobs: the scoring/Ensembl host templates, `REQUEST_TIMEOUT` (default 90s),
`MAX_CONCURRENCY` (default 2 — the upstream is rate-limited), `CACHE_TTL_MINUTES`
(default 1440), `RATE_BUDGET_MIN_INTERVAL_MS` (default 12000 — the soft client-pacing
interval surfaced as `_meta.rate_budget`), and `MCP_TRANSPORT`/`MCP_HOST`/`MCP_PORT`/`MCP_PATH`.

Background tasks use FastMCP's Docket backend. `DOCKET_URL` defaults to
`memory://` (in-process, correct for the single-process unified host); set
`SPLICEAILOOKUP_LINK_DOCKET_URL=redis://…` (or the FastMCP-native
`FASTMCP_DOCKET_URL`) for a multi-worker deployment.

## CLI

A single `typer` console script (`spliceailookup-link`) with `rich` output:

```bash
spliceailookup-link serve --transport unified --host 127.0.0.1 --port 8603
spliceailookup-link config --validate     # show + validate resolved configuration
spliceailookup-link health --url http://127.0.0.1:8603   # probe /health
spliceailookup-link version
```

`--transport` accepts `unified` or `http` (Streamable HTTP only — there is no
stdio transport). Logging is `structlog`: set `SPLICEAILOOKUP_LINK_LOG_FORMAT`
to `json` (default, production) or `console` (dev; also enabled by `--dev`).

## Development

```bash
make ci-local        # format-check + lint + line-budget + typecheck + tests
make test            # deterministic unit tests
make test-integration   # live SpliceAI/Pangolin/VEP tests (marked, may be slow)
make docker-build && make docker-up
```

## Scope & boundaries

In scope: SpliceAI / Pangolin / SAI-10k splice prediction + variant resolution.
Out of scope (delegated): allele frequency & ClinVar (gnomad-link), gene–disease
context (genereviews-link), expression (gtex-link), liftover (gnomad-link), and
the AlphaMissense / PrimateAI / PromoterAI / CADD annotations shown on the website.

## Citations

- **SpliceAI** — Jaganathan K, et al. *Cell* 2019;176(3):535-548. PMID:30661751.
- **Pangolin** — Zeng T, Li YI. *Genome Biology* 2022;23:103. PMID:35449021.
- **SpliceAI-10k** — Canson DM, et al. *Bioinformatics* 2023.
- **SpliceAI Lookup** — Broad Institute, https://spliceailookup.broadinstitute.org.
- **Ensembl VEP** — https://rest.ensembl.org.

## License

MIT — see [LICENSE](LICENSE).
