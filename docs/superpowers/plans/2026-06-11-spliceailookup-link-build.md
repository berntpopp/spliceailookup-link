# spliceailookup-link — Build Record

Date: 2026-06-11
Spec: `docs/superpowers/specs/2026-06-11-spliceailookup-link-design.md`
API contract: `docs/API.md` (reverse-engineered from the live site + repo).

## Delivered (v1)

- Package `spliceailookup_link/` matching the `-link` family conventions
  (uv + hatchling, FastMCP 3.x MCP-first, `UnifiedServerManager` with
  unified/http/stdio transports, `run_mcp_tool` envelope, `_meta.next_commands`,
  capabilities tool + 5 resources, build_check, research-use disclaimer,
  600-LOC discipline, AGENTS.md/CLAUDE.md split, Docker, GitHub Actions CI).
- 5 MCP tools: `get_server_capabilities`, `resolve_variant`, `predict_spliceai`,
  `predict_pangolin`, `predict_splicing` (combined headline tool).
- httpx client layer: `ScoringClient` (SpliceAI + Pangolin), `EnsemblVepClient`
  (HGVS/rsID resolution), shared `BaseHTTPClient` (retry/concurrency/error taxonomy).
- Upstream `error`-in-200 handling, GRCh37/38 build-mismatch pre-flight, caching.

## Verification

- `make ci-local` green (ruff format+lint, 600-LOC budget, mypy strict-ish, tests).
- 84 unit tests (respx-mocked, offline) + 3 live integration tests pass; coverage 81%.
- stdio transport verified via the MCP client SDK (initialize, list_tools/resources,
  call_tool incl. build_mismatch).
- Live cross-check: `predict_splicing("chr8-140300616-T-G")` reproduces the website —
  "TRAPPC9 (GRCh38): SpliceAI Δ=0.83; Pangolin Δ=0.85; models agree; predicted exon
  skipping"; `resolve_variant("NM_001089.3(ABCA3):c.875A>T")` → 16-2317763-T-A (ABCA3).

## Out of scope / future

- AlphaMissense / PrimateAI / PromoterAI tabix scores (client-side byte-range).
- dbNSFP / CADD (delegate hint to myvariant.info), gnomAD (delegate to gnomad-link),
  liftover (delegate to gnomad-link), gene-disease/expression context (genereviews/gtex).
- `transcripts="all"` returns every overlapping transcript; consider gene/transcript
  pagination if payloads grow.
- TestPyPI + GHCR release workflows (siblings ship these).
