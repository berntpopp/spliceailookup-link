# AGENTS.md

Shared repository instructions for agentic coding tools working in spliceailookup-link.

## Project

spliceailookup-link is an MCP server for splice-impact prediction. It wraps the
Broad Institute SpliceAI Lookup backends (the same SpliceAI / Pangolin / SAI-10k
predictions the website at spliceailookup.broadinstitute.org surfaces) plus an
Ensembl-VEP-backed variant resolver. FastAPI is a thin host providing `/health`
only; the MCP facade is the product.

Primary areas:

- `spliceailookup_link/` - Python package: MCP facade, HTTP clients, services,
  models, transports, server management
- `spliceailookup_link/mcp/` - hand-authored MCP facade (tools, resources, errors)
- `spliceailookup_link/api/` - httpx clients for the scoring APIs and Ensembl VEP
- `tests/` - unit (deterministic, respx-mocked) and integration (live) tests
- `docs/` - API contract, architecture, MCP connection docs
- `docs/superpowers/specs/` - design specs; `docs/superpowers/plans/` - plans
- `docker/` - Dockerfile + Compose

## Upstream contract (load-bearing facts)

- Scoring: `GET {spliceai|pangolin}-{37|38}-xwkwwwxdwq-uc.a.run.app/{spliceai|pangolin}/`
  with query params `variant` (chrom-pos-ref-alt), `hg` (37|38), `distance`,
  `mask` (0|1), `bc` (basic|comprehensive), optional `raw`, `variant_consequence`.
- **Errors are returned as HTTP 200 with an `error` field in the JSON body**,
  not as HTTP error codes. The client MUST inspect the body. Map "Unable to
  parse variant" -> invalid_input; "did not return any scores" -> not_found;
  5xx / timeout -> upstream_unavailable.
- SAI-10k consequence prediction is embedded under `sai10kPredictions` in the
  SpliceAI response (no separate endpoint).
- Resolver: Ensembl VEP `/vep/human/hgvs/<hgvs>?vcf_string=1` returns
  `vcf_string` (= chrom-pos-ref-alt) and `most_severe_consequence`.
- Upstream is "interactive use only, several requests per user per minute" and
  individual calls can take 30s+. Keep MAX_CONCURRENCY low, timeouts generous,
  caching aggressive, and carry the research-use disclaimer everywhere.

## Source Of Truth

- Use this file for shared repo-wide agent guidance.
- Keep `CLAUDE.md` lean and Claude-specific; it references this file.
- Prefer `Makefile` targets over ad hoc commands.
- Use `uv.lock` as the dependency lock source of truth.
- For multi-step work, write/update the spec in `docs/superpowers/specs/` and the
  plan in `docs/superpowers/plans/` before broad edits.

## Working Rules

- Do not revert or overwrite changes you did not make unless explicitly asked.
- Keep edits scoped; avoid unrelated refactors. Prefer existing patterns.
- Put tests under `tests/`; do not create alternate test roots.
- Use ASCII unless a file already requires non-ASCII content.
- Treat the SpliceAI Lookup APIs as external research data services. Keep MCP
  tools research-use scoped; never imply clinical decision support.
- Keep live upstream calls out of default local CI. Tests requiring API
  availability must be marked `integration`.
- MCP tool names, schemas, resources, and response modes are owned by
  `spliceailookup_link/mcp/`. REST is intentionally minimal (`/health` only).

## Commands

Required before claiming completion: `make ci-local`.

Useful: `make install lock format lint lint-fix lint-loc typecheck test
test-fast test-integration test-cov dev run-prod docker-build docker-up`.

## Coding Standards

- Use `uv` for dependency management; never direct `pip` installs.
- Modern Python typing: `list[str]`, `dict[str, int]`, `str | None`.
- Format and lint with Ruff; type check with mypy targeting Python 3.12.
- Keep MCP tool behavior and service behavior covered by unit tests.
- Preserve MCP tool names and response schemas unless the task calls for a
  breaking change.
- Default the image command to the unified FastAPI host plus MCP HTTP.

## File Size Discipline

Hard cap: **600 lines per Python module** in `spliceailookup_link/`.
Enforced by `make lint-loc` (wired into
`ci-local` and pre-commit). Tests are exempt. When a file approaches 500 lines,
plan a cohesive split (one module per responsibility) and keep facades/tool
names stable so call sites do not churn. Grandfather exceptions in
`.loc-allowlist` with an explicit ceiling only as a tracked temporary measure.

## Testing Notes

- `make test` runs deterministic unit tests from `tests/unit/`.
- `make test-integration` runs live SpliceAI/Pangolin/VEP tests; may fail when
  the upstream rate-limits or cold-starts.
- `make ci-local` runs formatting, linting, line-budget, type checking, tests.
- Treat failing checks as real issues unless you have clear evidence otherwise.
