# Configuration

Every setting is an environment variable prefixed **`SPLICEAILOOKUP_LINK_`** and is
optional — the defaults are tuned for the upstream's fragility and are safe for local
use. [`.env.example`](../.env.example) is the annotated template; copy it to `.env`.

Validate what the process actually resolved:

```bash
uv run spliceailookup-link config --validate
```

## Upstream endpoints

| Variable | Default | Notes |
|---|---|---|
| `SPLICEAI_URL_TEMPLATE` | `https://spliceai-{hg}-xwkwwwxdwq-uc.a.run.app/spliceai/` | `{hg}` is substituted with `37` or `38`. |
| `PANGOLIN_URL_TEMPLATE` | `https://pangolin-{hg}-xwkwwwxdwq-uc.a.run.app/pangolin/` | Same substitution. |
| `ENSEMBL_GRCH38_URL` | `https://rest.ensembl.org` | Variant resolver (GRCh38). |
| `ENSEMBL_GRCH37_URL` | `https://grch37.rest.ensembl.org` | Variant resolver (GRCh37) — a different host, not a query parameter. |
| `USER_AGENT` | `spliceailookup-link/0.1 (research MCP; …)` | Sent to every upstream. |
| `GENCODE_VERSION` | `v44` | The documented transcript-annotation release of the Broad backend, surfaced in each prediction's `provenance`. Env-overridable so an operator can bump it when the backend updates, without a code change. |

## Request handling

The upstream is *"interactive use only, several requests per user per minute"* and a
cold call can take 30s+. These defaults are deliberately conservative; raising them
is how you get rate-limited.

| Variable | Default | Notes |
|---|---|---|
| `REQUEST_TIMEOUT` | `90` | Seconds. Cold Cloud Run starts are slow. |
| `MAX_CONCURRENCY` | `2` | In-flight upstream calls. **Do not raise casually** — the upstream is rate-limited. |
| `QUEUE_WAIT_TIMEOUT` | `30` | Seconds a call waits for a concurrency slot. |
| `MAX_RETRIES` | `3` | Upstream retry attempts. |
| `BATCH_RETRY_BACKOFF_SECONDS` | `1.0` | Caps the jittered backoff when `predict_splicing_batch` retries a per-item retryable failure (`rate_limited` / `upstream_unavailable`) once inside the batch. Tests set `0` for determinism. |
| `PREDICT_SOFT_DEADLINE_SECONDS` | `55` | Foreground soft deadline: a `comprehensive` gene set with a large `max_distance` can outlive the client's MCP timeout, so the server returns a structured `upstream_unavailable` before the client gives up. `0` disables. Background tasks bypass it. |
| `RATE_BUDGET_MIN_INTERVAL_MS` | `12000` | Recommended soft spacing between cache-miss scoring calls, surfaced as `_meta.rate_budget.min_interval_ms` (and `retry_after_s` on a `rate_limited` error) so an autonomous caller paces a burst instead of discovering the limit by hitting it. |

## Caching

Scores are deterministic per `(model, build, variant, distance, mask, gene_set)`, so
the cache is long-lived by design and is the main thing keeping load off the upstream.

| Variable | Default | Notes |
|---|---|---|
| `CACHE_SIZE` | `1024` | Entries (in-process). |
| `CACHE_TTL_MINUTES` | `1440` | 24h. |
| `WARM_THRESHOLD_MS` | `5000` | A response counts as "warm" if it was a cache hit or the upstream answered faster than this (cold starts are ~13s+, warm calls sub-second). Surfaced as `_meta.served_warm` so a client can choose blocking vs. background execution. |

## Preflight checks

Both trade one cheap Ensembl lookup for a slow, useless upstream round-trip. Disable
them only where Ensembl is unreachable.

| Variable | Default | Notes |
|---|---|---|
| `PREFLIGHT_REF_CHECK_ENABLED` | `true` | Validates the coordinate REF against the Ensembl reference base *before* dispatching to scoring — a fast `ref_mismatch` instead of a ~17s `not_found`. |
| `PREFLIGHT_OVERLAP_CHECK_ENABLED` | `true` | Asks Ensembl whether any transcript overlaps `[pos-max_distance, pos+max_distance]`. A conclusive zero means neither gene set can score the variant, so `not_found` returns in <0.5s instead of a ~20s cold round-trip. Conservative: any inconclusive or non-zero result falls through to real scoring, so it never invents a `not_found`. |

## Transport

Streamable HTTP only — there is **no stdio entry point**. See
[Deployment](deployment.md) for the request-boundary allowlists and TLS.

| Variable | Default | Notes |
|---|---|---|
| `MCP_TRANSPORT` | `unified` | `unified` (FastAPI `/health` + MCP at `MCP_PATH`) or `http`. |
| `MCP_HOST` | `127.0.0.1` | |
| `MCP_PORT` | `8603` | |
| `MCP_PATH` | `/mcp` | A leading `/` is added if omitted. |
| `ALLOWED_HOSTS` | `["localhost","127.0.0.1","::1"]` | Exact Host allowlist — see [Deployment → request boundary](deployment.md#request-boundary). |
| `ALLOWED_ORIGINS` | `[]` | Exact browser-Origin allowlist (request admission). |
| `CORS_ORIGINS` | `*` | Browser **response** headers, comma-separated. Distinct from `ALLOWED_ORIGINS`. |

## Background tasks

Every prediction tool opts into the MCP background-task protocol, which FastMCP backs
with Docket.

| Variable | Default | Notes |
|---|---|---|
| `DOCKET_URL` | `memory://` | In-process, and correct for the single-process unified host. For a multi-worker deployment set `SPLICEAILOOKUP_LINK_DOCKET_URL=redis://…` (the FastMCP-native `FASTMCP_DOCKET_URL` is also honoured). |
| `WARMUP_STAY_WARM_ESTIMATE_SECONDS` | `900` | Conservative Cloud Run idle-decay estimate reported by `warmup` as `stay_warm_estimate_s`. An estimate, not a guarantee. |

## Logging

`structlog`, configured by two variables.

| Variable | Default | Notes |
|---|---|---|
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `json` | `json` for production; `console` for a human-readable dev log (also enabled by `serve --dev`). |

## CLI

A single `typer` console script with `rich` output:

```bash
spliceailookup-link serve --transport unified --host 127.0.0.1 --port 8603
spliceailookup-link config --validate                      # show + validate resolved configuration
spliceailookup-link health --url http://127.0.0.1:8603     # probe /health
spliceailookup-link version
```

`--transport` accepts `unified` or `http` only.
