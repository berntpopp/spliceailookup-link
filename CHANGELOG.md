# Changelog

All notable changes to `spliceailookup-link` are documented here. This project
adheres to [Semantic Versioning](https://semver.org/).

## [3.0.4] - 2026-07-11

### Security

- **Security (defense in depth): guard the FastMCP-core not-found reflection
  surface. FastMCP core echoes the caller's OWN requested tool name / resource
  URI / prompt name back to the caller and to logs BEFORE backend middleware
  runs. A new `NotFoundGuard` middleware preflights the tool name (unknown ->
  fixed name-free `not_found` envelope) and fixes the `on_read_resource`
  boundary; a protocol-handler backstop covers the unknown-tool return path and
  the unknown-prompt surface; a validation-log scrub filter neutralizes the
  FastMCP/MCP-SDK records ("Handler called", "Tool cache miss for", "Failed to
  validate request") that would echo the caller-supplied name/URI (and its
  control/zero-width/bidi/NUL code points) into a log sink. Caller
  self-reflection surface; research use only.**

## [3.0.3] - 2026-07-11

### Security

- **Security (defense in depth): caller-visible error messages are sanitized of
  control/zero-width/bidi/NUL code points; upstream SpliceAI/Ensembl/scoring
  error bodies are no longer echoed; the arg-validation frame redacts unexpected
  argument names; the recent-error telemetry sink is sanitized. Research use
  only.**

## [3.0.2] - 2026-07-11

### Security

- **Re-enabled FastMCP 3.4.4 strict Host/Origin protection** with configurable
  allowlists (`ALLOWED_HOSTS` / `ALLOWED_ORIGINS`). Deploy prerequisite: set the
  proxied public host in `ALLOWED_HOSTS` or router federation will 421.
- **Fixed FastMCP 3.4.4 `ValidationError` handling** (regression from the
  3.2 → 3.4.4 upgrade).

## [3.0.1] — 2026-07-07

### Security

- **Base `docker-compose.yml` now loopback-binds its published host port**
  (`127.0.0.1:${SPLICEAILOOKUP_LINK_HOST_PORT:-8603}:8603`). Copying the base
  compose to a server previously bound `0.0.0.0`, which bypasses the host
  firewall and would publish the unauthenticated backend on the public IP.
  Production continues to reach the container only via the reverse proxy
  (`prod`/`npm` overlays drop the port with `ports: !reset []`). Added a unit
  guard (`tests/unit/test_docker_compose_loopback.py`) asserting every published
  base-compose port is loopback-bound.

## [3.0.0] — 2026-07-03

### Changed — adopted the ratified GeneFoundry Response-Envelope Standard v1 (BREAKING)

**Response shape changed for every tool** (pre-alpha, no shims): single-item
tools (`predict_spliceai`, `predict_pangolin`, `predict_splicing`,
`resolve_variant`, `get_server_capabilities`, `warmup`) now nest their domain
payload under a `result` object (`{"success": true, "result": {...}, "_meta": {...}}`)
instead of returning the fields flat at the top level next to `success`/`_meta`.
`predict_splicing_batch` is unchanged (it already returned the SS1 collection
frame: a `results` array plus sibling `count`/`summary`/`summary_top_variant`).

- **Errors are now delivered in-band, reversing v2.2.0**: a failing tool call
  returns a normal MCP result with `isError: true` **and** the flat error
  envelope (`error_code`, `message`, `retryable`, `recovery_action`,
  `fallback_tool`, `next_commands`, `_meta`, ...) present as
  `structuredContent` on that same result, instead of a raised
  `fastmcp.exceptions.ToolError` carrying only a text message. Verified
  against the installed fastmcp 3.4.2: a tool may return a
  `fastmcp.tools.tool.ToolResult(is_error=True, structured_content=...)`,
  which `Tool.convert_result` passes straight through untouched.
- **Typed cold-latency hints**: `predict_spliceai` / `predict_pangolin`
  (`cost_tier: "medium"`, `expected_cold_latency_ms: 30000`),
  `predict_splicing` (`"high"`, `60000`), and `predict_splicing_batch`
  (`"high"`, scaled by submitted item count) now carry these fields in
  `_meta` so a caller can plan for a slow call before it blocks a turn.
- **`execution.taskSupport` was already correctly declared** (via `task=True`
  on the prediction tools, backed by the installed fastmcp 3.4.2
  `Tool.task_config.mode` -> `ToolExecution(taskSupport="optional")` on the
  wire MCP `Tool`); this release adds the `_meta` latency hint alongside it.
- Batch per-item failures are unchanged: they stay embedded in a successful
  `predict_splicing_batch` envelope so one bad variant never fails its
  siblings.

## [2.2.1] — 2026-06-29

### Security — adopted the GeneFoundry Container & Deployment Hardening Standard v1

Added a `docker/docker-compose.prod.yml` hardening overlay (read-only rootfs +
explicit writable tmpfs, `cap_drop: ALL`, `no-new-privileges`, `init`, and
memory/CPU/PID limits; expose-only with `ports: !reset []`), added a
`HEALTHCHECK` to the Dockerfile, digest-pinned the `python:3.14-slim` base image,
added a root `.dockerignore`, added a `container-security` CI workflow (Trivy
scan failing on fixable HIGH/CRITICAL + CycloneDX SBOM artifact), and fixed the
CORS middleware to never pair wildcard origins with `allow_credentials=True`.

## [2.2.0] — 2026-06-16

### Changed — MCP tool errors are now raised, fleet-uniform (`isError: true`)

Tool failures now surface as an **MCP error result** (`isError: true`) carrying
a `fastmcp.exceptions.ToolError`, instead of an in-band `{"success": false, …}`
tool result. This matches the rest of the GeneFoundry `*-link` fleet
(`gtex-link` / `genereviews-link` `error_passthrough`), so a single
gateway/agent error-handling path works across every server.

- The **full structured envelope is preserved**: the `ToolError` message is the
  same compact-JSON body as before (`error_code`, `message`, `retryable`,
  `recovery_action`, `fallback_tool`, `fallback_args`, `recovery`,
  `next_commands`, `_meta`, plus the situational `field_errors` /
  `variant_ids` / `other_build_hint` / `nearest_transcript` / `rate_budget`).
  Clients that consume the structured recovery fields keep working — they now
  read them from the error result's content rather than a success body.
- `ToolError` is passed through `mask_error_details=True` unredacted by design,
  so the structured body always reaches the client.
- **Batch is intentionally unchanged**: `predict_splicing_batch` still returns a
  successful envelope whose per-item failures stay in-band under `results[…]`
  (one bad variant must never fail its siblings). Argument-level failures
  (e.g. an over-cap batch) raise, like every other top-level tool.

Migration: a client that branched on `result["success"] is False` should branch
on the MCP error result (`isError`) and JSON-decode the error content instead.

## [2.1.0] — 2026-06-15

### Added

- Self-contained Docker NPM deployment overlay
  (`docker/docker-compose.npm.yml`) + `.env.docker.example` for deployment
  behind nginx-proxy-manager at `spliceailookup-link.genefoundry.org`.

## [2.0.0] — 2026-06-15

### Breaking — GeneFoundry Logging & CLI Standard v1 (closes #3)

Adopts the fleet-wide **GeneFoundry Logging & CLI Standard v1**. This is a
front-end + logging change only: the **MCP tool surface, services, and
`/health` / `/mcp` endpoints are unchanged**, so the `genefoundry-router`
gateway is unaffected.

**CLI: `argparse` → `typer`.**

- New `spliceailookup_link/cli.py` is a single `typer.Typer(...)` app
  (`no_args_is_help=True`, `rich` output) with commands `serve`, `config
  [--validate]`, `health [--url]`, and `version`. There is **no bare-serve**;
  start the server with `spliceailookup-link serve …`.
- `serve` options: `--transport {unified,http}` (default `unified`), `--host`,
  `--port`, `--mcp-path`, `--log-level`, `--disable-docs`, `--dev`.
- Single console script `spliceailookup-link = "spliceailookup_link.cli:app"`.
  The root `server.py` / `mcp_server.py` scripts and the
  `spliceailookup-link-mcp` entry point are **removed**.

**Logging: stdlib `logging` → `structlog`.**

- `spliceailookup_link/logging_config.py` configures structlog on the canonical
  processor chain (`merge_contextvars → add_log_level → TimeStamper(iso) →
  StackInfoRenderer → format_exc_info → static fields`) and renders **JSON in
  production / `ConsoleRenderer` in dev**, selected by `LOG_FORMAT` (default
  `json`). The `asgi-correlation-id` request id is bound onto every event and
  surfaced as `request_id`.

**Transport / ops.**

- **stdio is removed everywhere** (config Literals, server manager, Docker,
  Makefile, README). Streamable HTTP only; MCP at `/mcp`, health at `/health`.
- Docker `CMD`, the `Makefile` (`dev` / `run-prod`), and the README now invoke
  `spliceailookup-link serve …`.
- New `asgi-correlation-id` dependency. Config drops `MCP_LOG_LEVEL` /
  `STDIO_LOG_LEVEL` and adds `LOG_FORMAT`.

**Migration:** replace `python server.py --transport …` /
`spliceailookup-link --transport …` with `spliceailookup-link serve
--transport …`; drop any `stdio` transport or `spliceailookup-link-mcp` usage
and connect over Streamable HTTP at `/mcp`.

## [1.0.0] — 2026-06-15

### Breaking — GeneFoundry Tool-Naming Standard v1 (closes #2)

Adopts the fleet-wide **GeneFoundry Tool-Naming & Normalization Standard v1** so
this server composes cleanly behind the `genefoundry-router` gateway (mounted under
the namespace token **`spliceai`**; tools surface as `spliceai_<tool>`). No
deprecation aliases — the old argument names are removed immediately.

**Argument renames (fleet canon, Rule 4):**

- `variant` → `variant_id` on `predict_splicing`, `predict_spliceai`,
  `predict_pangolin`, and `resolve_variant`.
- `variants` → `variant_ids` on `predict_splicing_batch`.

`_meta.next_commands` and error `fallback_args` hints now emit the canonical
`variant_id` / `variant_ids` keys so they remain directly callable.

**`response_mode` enum:** now the fleet ladder `minimal | compact | standard |
full` (added `standard`, which behaves as `compact` in this server). `compact`
remains the default.

**Migration:** rename the argument in every call —
`predict_splicing(variant=...)` → `predict_splicing(variant_id=...)`, and
`predict_splicing_batch(variants=[...])` → `predict_splicing_batch(variant_ids=[...])`.
Tool names are unchanged (`predict_*`, `resolve_variant`,
`get_server_capabilities`, `warmup`).

### Notes

- Tool names were already unprefixed `verb_noun` snake_case and within the length
  budget. Per issue #2 (resolution A), `predict` is part of this server's
  canonical verb set (ML splice-score inference); the `ops`-tagged `warmup`
  utility is exempt from the verb rule.
- `serverInfo.name` remains the stable identity `spliceailookup-link`; the gateway
  namespace token `spliceai` is now documented in the README (Rule 5).
- CI guard added: `tests/unit/test_tool_names.py` asserts every registered tool
  name matches `^[a-z0-9_]{1,50}$`, starts with a canonical verb (or is
  `ops`-tagged), does not self-prefix `spliceai_`, and that the variant argument
  and `response_mode` enum follow the fleet canon (Rule 8).
