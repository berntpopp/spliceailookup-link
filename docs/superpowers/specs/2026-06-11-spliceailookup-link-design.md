# spliceailookup-link — Design Spec

Date: 2026-06-11
Status: Approved-for-build (autonomous goal directive)
Author: reverse-engineered from `spliceailookup.broadinstitute.org` + sibling `-link` conventions

## 1. Purpose

An MCP server that grounds **splicing-impact prediction** for genetic variants in the
Broad Institute's SpliceAI Lookup backends — the same SpliceAI / Pangolin / SAI-10k
predictions the website surfaces — exposed as LLM-ergonomic tools that match the
`-link` family conventions (gnomad-link, gtex-link, pubtator-link, genereviews-link).

Non-goal: re-implement the splice-prediction models. We wrap the public scoring APIs.

## 2. Scope (v1)

**In scope** — the splice-prediction core + variant resolution:
- SpliceAI delta scores (acceptor/donor gain/loss) + embedded SAI-10k consequence prediction.
- Pangolin splice gain/loss scores.
- Variant resolution: HGVS (transcript `c.`/genomic `g.`), rsID, and loose
  `chrom-pos-ref-alt` (incl. `chr` prefix and whitespace/tab-delimited) → canonical
  `chrom-pos-ref-alt` + build + VEP `most_severe_consequence` (via Ensembl VEP).
- GRCh37/GRCh38 build-mismatch pre-flight (adapted from gnomad-link `build_check.py`).

**Out of scope (documented as future / delegated):**
- AlphaMissense, PrimateAI, PromoterAI (static tabix files — need client-side byte-range tabix).
- dbNSFP / CADD (third-party `myvariant.info`).
- gnomAD allele frequencies → delegate to **gnomad-link**.
- Gene–disease context → delegate to **genereviews-link** / expression to **gtex-link**.
- Liftover → delegate to **gnomad-link** `liftover_variant`.
Cross-server delegation is surfaced as `_meta.see_also` hints (not callable `next_commands`,
which are same-server only).

## 3. Stack & conventions (match the family)

- Python ≥3.12, `uv` + `hatchling`. FastMCP `>=3.2,<4`, `mcp[cli] >=1.27,<2`. Pydantic v2.
- **MCP-first** (pubtator pattern): native `FastMCP(name="spliceailookup-link",
  mask_error_details=True, instructions=...)`, `register_*_tools(mcp, *, service_factory)`.
- `UnifiedServerManager` with 3 transports (unified / http / stdio); `server.py` (CLI main +
  module-level `app`) and `mcp_server.py` (stdio). Console scripts `spliceailookup-link`,
  `spliceailookup-link-mcp`.
- httpx async client + token-bucket rate limiter + retry/backoff + correlation header.
- Envelope: `run_mcp_tool` injects `success`/`_meta`, returns structured error dicts
  (`error_code`, `retryable`, `recovery_action`, `field_errors`); `_meta.next_commands`;
  `relax_output_schema`; `READ_ONLY_OPEN_WORLD` annotations; `response_mode` compact/full/minimal;
  `headline`; `recommended_citation`; `RESEARCH_USE_NOTICE`.
- Env prefix `SPLICEAILOOKUP_LINK_`; resource scheme `spliceailookup://`.
- 600-LOC/file discipline, AGENTS.md/CLAUDE.md split, `.claude/skills/`, ruff(100)/mypy/pytest,
  multi-stage Docker, GitHub Actions, README in family order.

## 4. Tool surface (v1)

| Tool | Purpose |
|---|---|
| `get_server_capabilities` | discovery: tools, params, builds, limits, citations, workflow |
| `resolve_variant` | HGVS/rsID/loose input → `{variant_id, genome_build, gene, consequence, ...}` |
| `predict_spliceai` | SpliceAI delta scores (+ `include_consequence` SAI-10k aberrations) for a variant |
| `predict_pangolin` | Pangolin splice gain/loss scores for a variant |
| `predict_splicing` | **headline tool**: resolve-if-needed + SpliceAI + Pangolin merged, one call |

Shared params: `variant` (or `variant_id`), `genome_build` (`GRCh37`/`GRCh38`, default 38),
`max_distance` (default 500, like the site; doc the 50 API default), `mask`
(`raw`/`masked`, default `raw`), `gene_set` (`basic`/`comprehensive`, default `basic`,
warn comprehensive is slow), `transcripts` (`mane`/`all`, default `mane`),
`response_mode` (`compact`/`full`/`minimal`).

## 5. Upstream contract (see .investigation/API-REVERSE-ENGINEERING.md)

- Scoring: `GET https://{spliceai|pangolin}-{37|38}-xwkwwwxdwq-uc.a.run.app/{spliceai|pangolin}/`
  with `variant, hg, distance, mask, bc, raw, variant_consequence`. Hosts configurable.
- **Errors return HTTP 200 with an `error` field** → client must inspect body, map to
  `invalid_input` (parse failures) / `not_found` (no overlapping transcript) / `upstream_unavailable` (503/timeout).
- Resolver: Ensembl VEP `/vep/human/hgvs/<hgvs>?vcf_string=1` → `vcf_string` + `most_severe_consequence`.
- Constraints: rate limit "several/min"; latency up to ~36 s; `distance=500+comprehensive` 503s.
  → conservative rate limiter, ≥90 s timeout, caching, research-use disclaimer.

## 6. Error taxonomy

`invalid_input` (bad variant format / VEP can't resolve), `not_found` (no scores —
variant outside annotated transcripts), `build_mismatch` (coords look like the other
build → recovery: re-call with correct `genome_build`), `rate_limited` (retryable),
`upstream_unavailable` (503/timeout, retryable), `internal_error`.

## 7. Citations / safety

- SpliceAI: Jaganathan et al., *Cell* 2019 (PMID 30661751).
- Pangolin: Zeng & Li, *Genome Biology* 2022 (PMID 35449021).
- SAI-10k calculator: Canson et al. (SpliceAI-10k). Ensembl VEP for resolution.
- `RESEARCH_USE_NOTICE` + `_meta.unsafe_for_clinical_use=True` on every response.

## 8. Testing

- Unit: respx-mocked client (canned SpliceAI/Pangolin/VEP bodies from `.investigation/`),
  envelope/error mapping, build_check, variant normalization, capabilities, profile filtering,
  response shaping. Coverage floor 80%.
- Integration (marked, off by default): live calls to the documented examples.
- Playwright cross-check: the captured site behaviors map 1:1 to tool outputs.
