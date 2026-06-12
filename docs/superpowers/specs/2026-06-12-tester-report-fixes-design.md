# spliceailookup-link — Tester-Report Fixes (Design)

**Date:** 2026-06-12
**Author:** MCP engineering (driven by the 2026-06-12 senior-tester report)
**Status:** Design — pending implementation
**Baseline:** v0.5.0 (eval pass 3, F11–F17 + #C1) → target **v0.6.0**
**Source report:** `docs/mcp-tester-report-2026-06-12.md` (grade 9/10)

## 1. Why

A live black-box senior-tester pass over the deployed **v0.5.0** server graded it
**9/10** and filed 8 prioritized findings (3 High, 3 Medium, 2 Low). Every finding
was re-confirmed against the current code, not just the report:

- **F1** wrong-REF is misclassified `not_found` — `scoring_client.py` maps upstream
  "did not return any scores" to `DataNotFoundError`; coordinate inputs are never
  ref-validated (`variant.normalize_coordinate` checks shape only), so a wrong
  reference base produces a *misleading* `not_found` recovery (suggests
  `comprehensive` / wider `max_distance` / `resolve_variant`, none of which fix a
  wrong REF).
- **F2** batch silently scores allele 0 of an ambiguous rsID — `prepare_variant`
  (`_common.py`) reads only `resolution["variant_id"]` and drops the
  `ambiguous` / `variant_ids` / `note` that `SpliceService.resolve` already
  produces. This affects **single `predict_*` too**, not only batch.
- **F3** `capabilities_version` lives only inside the 7.8 kB capabilities doc
  (`resources.py`); it is never echoed in prediction `_meta`, so a warm client
  must re-fetch the whole doc to detect drift.
- **F4** `comprehensive` + widened `max_distance` hard-times-out at the client; no
  server deadline turns it into a structured `upstream_unavailable`.
- **F5** `warmup` warmth scope/TTL is undocumented; a masked Pangolin call still
  cost 11.5 s after a basic/raw warmup.
- **F6** capabilities doc duplicates per-param prose that already lives in the tool
  schemas (SEP-1576 redundancy); no lean mode.
- **F7** `assess_agreement` labels a sub-threshold split (SpliceAI 0.31 /
  Pangolin 0.09) `discordant` even though neither crosses the high band — over-alarm.
- **F8** `build_mismatch` detection costs a full ~17.7 s scoring probe
  (`cross_build_probe`) when the position-length heuristic is ambiguous.

**Key structural insight:** F1 and F8 are the *same* missing capability — a cheap
reference-base lookup. The server already talks to Ensembl for resolution; one new
`/sequence/region` method lets the failure path distinguish *wrong REF* from *wrong
build* in ~300 ms instead of a 17 s scoring round-trip, and replaces a misleading
`not_found` with an accurate `ref_mismatch`. One mechanism closes two findings.

## 2. Goal & score model

Lift the dimensions the report scored below 9.5, without changing happy-path
latency and without a new vendor (the ref-check reuses Ensembl):

| Dimension | Report | Target | Lever (findings) |
|---|---:|---:|---|
| Error handling / robustness | 8 | 9.5+ | `ref_mismatch` (F1), `ambiguous` (F2), structured deadline (F4) |
| Token efficiency | 8 | 9.5 | echo `capabilities_version` (F3), lean capabilities mode (F6) |
| Output interpretability | 9 | 9.5+ | `discordant_subthreshold` verdict (F7) |
| Speed / latency | 8 | 8.5 | cheap Ensembl build disambiguation removes the 17 s probe (F8); fail-fast deadline (F4) |
| Observability | 9 | 9.5 | version hash on every `_meta` (F3) |
| Discoverability | 9 | 9.5 | lean mode + warmth/deadline docs (F5, F6) |

Safety stays 10; Composability/Input-ergonomics already strong. The latency
*ceiling* remains upstream-bound (background Tasks are the mitigation, already
shipped); we only remove the avoidable 17 s probe and convert a client hang into an
actionable envelope. Target overall: **> 9.5 / 10**.

## 3. Scope (decided)

In scope (v0.6.0), in priority order:

1. **F1+F8 — reference-base diagnostic.** New `ref_mismatch` error code; cheap
   Ensembl build disambiguation replacing the scoring cross-build probe.
2. **F2 — resolver ambiguity propagation.** New `ambiguous` error code; single and
   batch prediction fail an ambiguous rsID with callable per-allele alternates.
3. **F3 — echo `capabilities_version`** in every success/error `_meta`.
4. **F4 — server-side soft deadline** → structured `upstream_unavailable`.
5. **F6 — lean capabilities mode** (`detail="lean"`), params by-reference.
6. **F7 — `discordant_subthreshold` verdict** for both-below-high splits.
7. **F5 — document warmth scope/TTL**; optional `mask` arg on `warmup`.
8. Capabilities/glossary updates for all of the above; version bump → 0.6.0.

Out of scope: a new reference-genome dependency (we reuse Ensembl REST); pre-warming
every `(mask, gene_set, distance)` combination (Cloud Run autoscaling makes warmth
best-effort — we document honestly rather than over-promise); per-call suppression of
`next_commands`/`see_also` (the F3 version-echo + F6 lean mode are the higher-leverage
token wins; `minimal` mode already drops `see_also`); changing MCP tool *names*
(only additive fields and two new error codes).

## 4. F1 + F8 — Reference-base diagnostic (`ref_mismatch` + cheap build disambiguation)

**Problem.** Coordinate inputs are normalized for *shape* only. A wrong REF base
(`chr8-140300616-A-G`; true REF is `T`) sails through resolution, the model returns
"did not return any scores," and the server reports `not_found` with recovery advice
that cannot fix the real cause. Separately, when the position is valid in *both*
builds (the `chr8` case where `detect_build_mismatch` returns `None`), confirming a
build mismatch costs a full ~17.7 s scoring probe.

**Mechanism — one cheap Ensembl call on the failure path.**

`EnsemblVepClient.reference_base(chrom, pos, length, build) -> str | None`:
GET `{ensembl_url(build)}/sequence/region/human/{chrom}:{pos}..{pos+length-1}`
with `content-type=application/json`; return the uppercase `seq`. The
build-specific host (`rest.ensembl.org` vs `grch37.rest.ensembl.org`) is already in
`settings.ensembl_url`. `SpliceService.reference_base(...)` wraps it behind the
existing `alru_cache` (same TTL as scores) so repeats and the two-build probe are
free after the first lookup.

New diagnostic `diagnose_coordinate_failure(service, variant_id, requested_build)`
(new module `spliceailookup_link/mcp/tools/_diagnose.py`), called **only** on the
both-models-`not_found` path for a **coordinate** input (resolution is `None`):

1. Split `variant_id` → `chrom, pos, ref, alt`. Run only for simple ACGT `ref`
   (skip when `ref` is empty/contains `N`/looks like a symbolic allele → fall
   through to today's behavior).
2. Fetch `reference_base` at the requested build for `len(ref)` bases.
   - `ref == reference` → **genuine `not_found`** (well-formed, no transcript
     overlap). Unchanged behavior + recovery.
   - `ref != reference` → fetch `reference_base` at the **other** build:
     - matches other build only → **`BuildMismatchError(inferred=other)`** →
       `build_mismatch` (cheap; no scoring probe).
     - matches neither → **`RefMismatchError(observed=ref, reference=<requested
       base>, build)`** → `ref_mismatch`.
     - matches *both* (rare coincidence) → inconclusive: fall back to the existing
       scoring `cross_build_probe` (definitive, slow) before deciding.
3. Any Ensembl error / `reference_base is None` → **graceful fallback** to today's
   `cross_build_probe`, so the diagnostic can only improve, never regress.

**New error: `ref_mismatch`** (non-retryable, `recovery_action=reformulate_input`,
`fallback_tool=resolve_variant{variant}`). Recovery text:
> "The REF allele '{ref}' does not match the {build} reference base '{actual}' at
> {chrom}:{pos}. Correct the REF allele (it may be swapped with ALT, or from the
> other strand/build), or pass an HGVS/rsID to resolve_variant for canonical
> coordinates."

The `chr8` case (valid in both builds) now resolves via two cached `reference_base`
calls (~300 ms) instead of the 17 s scoring probe; the position-length heuristic
(`detect_build_mismatch`) stays as the zeroth-cost first pass for clearly
out-of-range positions.

**Wiring.** `predict_one` (combined) already centralizes the both-`not_found`
branch — call the diagnostic there in place of the bare `cross_build_probe`. The
single-model tools (`spliceai.py`, `pangolin.py`) gain the same diagnostic on their
`not_found` path via the shared helper, so all three prediction tools classify
identically. Cost is incurred only when a coordinate prediction already failed.

## 5. F2 — Propagate resolver ambiguity (`ambiguous` error code)

**Problem.** `SpliceService.resolve` already flags an ambiguous rsID
(`ambiguous: True`, `variant_ids: [C-A, C-T]`, plain-English `note`) and
`resolve_variant` surfaces it. But `prepare_variant` keeps only `variant_id` (allele
0), so `predict_splicing` and `predict_splicing_batch` silently score one allele —
the documented under-reporting risk in panel workflows.

**Decision — fail the item with a distinct `ambiguous` code, carrying callable
alternates.** When `resolution.get("ambiguous")`, `prepare_variant` raises
`AmbiguousVariantError(variant, candidates, note)` **before** any scoring →
error code `ambiguous` (non-retryable, `recovery_action=reformulate_input`). The
envelope carries `variant_ids` and `next_commands` = one `predict_splicing` per
candidate allele (mirroring `resolve_variant`'s `after_resolve_many`);
`fallback_tool=resolve_variant{variant}`. In `predict_splicing_batch` this is
isolated as a per-item error (the existing pattern), so the panel still returns every
other variant plus a clear, actionable "ambiguous — pick an allele" item.

**Why fail, not flag-and-score-first (alternative, rejected):** silent single-allele
scoring is exactly the risk the report flags; forcing an explicit allele choice is
consistent with `resolve_variant` and prevents an LLM from reporting one allele's
score as "the" answer. The cost (one variant not auto-scored) is paid back by
ready-to-call `next_commands` for each allele. `PreparedVariant` gains `ambiguous`,
`candidates`, `note`; the carrier `AmbiguousVariantError` lives in `mcp/errors.py`
beside `BuildMismatchError`.

## 6. F3 — Echo `capabilities_version` in every `_meta`

**Problem.** The freshness hash is only inside the 7.8 kB doc, so detecting drift
costs a full re-fetch.

**Change.** `resources.py` exposes a module-cached `get_capabilities_version() ->
str` (build the doc + hash once, lazily; cache the 12-char digest). `mcp/errors.py`
`_provenance_meta()` adds `"capabilities_version": get_capabilities_version()` so the
existing `_stamp` already merges it into every success and error `_meta` (~35 bytes/
response). A warm client compares the echoed hash and skips the doc until it changes.
The capabilities `response_fields.capabilities_version` text is updated to state the
hash is echoed on every response.

This directly *mitigates* F6's main cost (re-fetching the doc); the two are linked.

## 7. F4 — Server-side soft deadline → structured `upstream_unavailable`

**Problem.** `comprehensive` + large `max_distance` exceeds the client's MCP timeout;
the LLM sees a raw client-level hang with no envelope to act on.

**Change.** New setting `PREDICT_SOFT_DEADLINE_SECONDS: int = 55` (env-overridable;
`0` disables). `predict_one` wraps the scoring `asyncio.gather` in
`asyncio.wait_for(..., deadline)`; on `TimeoutError` it raises a `SpliceApiError`
("…exceeded the {N}s server deadline…") → classified `upstream_unavailable`
(retryable). The recovery text (already close) is sharpened to:
> "This call exceeded the server's {N}s deadline (comprehensive gene_set and/or a
> large max_distance are slow and may 503 upstream). Retry with gene_set='basic' or a
> smaller max_distance, or resubmit as a background task (task=…), which is not bound
> by this deadline."

55 s sits below common ~60 s MCP client timeouts while clearing legitimate cold calls
(report: up to ~40 s). **Background Tasks must bypass the deadline** (they are meant
to run long): the deadline is applied only when the call is *not* executing as a
task. Task-mode detection from the FastMCP `Context` is the one implementation
unknown — the plan resolves it explicitly, with a safe fallback (if reliable
detection is unavailable, set the foreground deadline generously near the 90 s
`REQUEST_TIMEOUT` and rely on `task=` for the slow path, still beating a client hang).
`upstream_unavailable` was untested in the report precisely because nothing produced
it deterministically; this makes it reachable and demonstrable.

## 8. F5 — Document warmth scope/TTL; optional `warmup(mask=…)`

**Problem.** A basic/raw warmup did not prevent an 11.5 s masked call; warmth scope
and TTL are undocumented and partly outside our control (Cloud Run autoscaling).

**Change (honest, low-effort).**
- `warmup` response gains `coverage` (e.g. `{"models":["spliceai","pangolin"],
  "mask":"raw","gene_set":"basic"}`) and a `note` that Cloud Run scales per-instance,
  so a subsequent call with different params or under concurrency may still
  cold-start; warmth decays after minutes of idle.
- `warmup` gains an optional `mask: Literal["raw","masked"] = "raw"` arg so a caller
  who knows they will use masked can warm that path. (We do **not** warm every combo
  by default — the upstream is rate-limited and autoscaling makes exhaustive warming
  futile.)
- Capabilities gains a `warmth` note: which path `warmup` covers, that TTL is
  upstream-controlled (idle scale-down, ~minutes), and that the robust mitigation for
  a guaranteed-cold first call is a background task, not reliance on warmup.

## 9. F6 — Lean capabilities mode (SEP-1576)

**Problem.** The 7.8 kB doc re-states per-parameter prose that already lives in each
tool's input schema — the exact redundancy SEP-1576 targets — and there is no lean
variant for token-sensitive discovery.

**Change.** `get_server_capabilities(detail: Literal["full","lean"] = "full")`.
`full` is unchanged (back-compatible default). `lean` returns: `server`,
`server_version`, `mcp_protocol_version`, `tools`, `recommended_workflows` (the
"which tool?" disambiguation), `agreement_verdicts`, `interpretation_bands`,
`error_codes`, `capabilities_version`, `descriptor_chars`, plus `params_by_reference`:
> "Per-parameter docs are in each tool's input schema (and spliceailookup://reference);
> omitted here to avoid duplication."

`lean` omits the verbose `parameters` block and trims `score_glossary` to one-line
keys. Expected ~2–3 kB vs 7.8 kB. The doc documents both modes. Combined with F3, a
warm client fetches `lean` once, then rides the echoed `capabilities_version` and
never re-fetches. (A future `$ref`-based dedup across tool schemas is noted as the
SEP-1576-native step but is out of scope here.)

## 10. F7 — `discordant_subthreshold` verdict

**Problem.** `assess_agreement` returns `discordant` for *any* cross-band split, so
0.31 vs 0.09 (moderate vs low) and 0.21 vs 0.05 read as a strong conflict even though
neither model crosses the high-confidence threshold (Δ≥0.5).

**Change — split `discordant` by whether a high-confidence call is involved:**

| Condition | Verdict |
|---|---|
| both ≥ 0.5 | `concordant_high` (unchanged) |
| both in [0.2, 0.5) | `concordant_moderate` (unchanged) |
| both < 0.2 | `concordant_low` (unchanged) |
| exactly one ≥ 0.5 | `discordant` — genuine strong-vs-weak conflict |
| neither ≥ 0.5, different bands | **`discordant_subthreshold`** (new) |

`discordant_subthreshold` detail: "the models differ in magnitude but neither crosses
the high-confidence threshold (Δ≥0.5); treat as a weak/uncertain signal, not a strong
conflict." Headline clause: "models differ on a weak signal (neither ≥0.5)". Wire the
new value into `_VERDICT_CLAUSE` (`_predict_shape.py`), the batch `verdict_counts`
histogram (`batch.py`), and capabilities `agreement_verdicts`. `discordant` now means
specifically "one model is high-confidence and the other is not" — a sharper, more
actionable signal.

## 11. Module map (600-LOC budget)

All edits stay well under the 600-line cap; no `.loc-allowlist` entry needed.

| Module | Now | Change |
|---|---:|---|
| `api/ensembl_client.py` | 54 | + `reference_base` (sequence/region) |
| `services/splice_service.py` | 213 | + cached `reference_base` wrapper |
| `mcp/tools/_diagnose.py` | new | `diagnose_coordinate_failure` (F1+F8) |
| `mcp/errors.py` | 373 | + `RefMismatchError`, `AmbiguousVariantError`, classify branches, recovery text, `capabilities_version` in provenance (F1/F2/F3) |
| `mcp/tools/_common.py` | 147 | `prepare_variant` raises `AmbiguousVariantError`; `PreparedVariant` fields (F2) |
| `mcp/tools/_predict.py` | 233 | deadline wrap (F4) + diagnostic wire-in (F1) |
| `mcp/tools/spliceai.py` / `pangolin.py` | 151 / 142 | diagnostic on not_found (F1) |
| `mcp/tools/_predict_shape.py` | 108 | `discordant_subthreshold` (F7) |
| `mcp/tools/batch.py` | 152 | new verdict counter (F7) |
| `mcp/tools/metadata.py` | 97 | `detail` param (F6); `warmup(mask=…)` + coverage (F5) |
| `mcp/resources.py` | 342 | lean mode, cached version accessor, new codes/verdict, warmth + deadline docs |
| `config.py` | 117 | + `PREDICT_SOFT_DEADLINE_SECONDS` |
| `__init__.py` / `pyproject.toml` | — | version → 0.6.0 |

## 12. Error-taxonomy delta

Adds two codes to the documented set (`resources.py` `error_codes` +
`get_reference_resource` taxonomy):

- **`ref_mismatch`** — retryable: false; when: "coordinate REF allele does not match
  the genome reference at that position in the requested build (likely a swapped REF/
  ALT, wrong strand, or wrong build)."
- **`ambiguous`** — retryable: false; when: "the input (e.g. an rsID) resolves to
  more than one ALT allele at the locus; pick one variant_id (see next_commands /
  variant_ids) and retry."

## 13. Testing (respx-mocked, deterministic; live paths `integration`)

- **F1/F8:** mock Ensembl `sequence/region`. Wrong-REF coordinate whose both-build
  bases differ from REF → `ref_mismatch` with the actual reference base in the
  message and `next_commands` to `resolve_variant`. Coordinate whose REF matches the
  *other* build's base (and `not_found` at requested) → `build_mismatch` without a
  scoring cross-build probe (assert the scoring client is not called for the probe).
  Correct-REF `not_found` (REF matches requested-build base) → still `not_found`.
  Ensembl-error during diagnosis → falls back to `cross_build_probe` (today's path).
- **F2:** StubService.resolve returns `ambiguous` → single `predict_splicing` returns
  `error_code="ambiguous"` with `variant_ids` and per-allele `next_commands`; batch
  isolates it as one failed item while other variants succeed; no allele is silently
  scored.
- **F3:** every success and error `_meta` carries `capabilities_version`, equal to
  `get_capabilities_version()` and stable across calls.
- **F4:** a scoring path that exceeds the deadline (mock a slow gather; set
  `PREDICT_SOFT_DEADLINE_SECONDS` low) → `upstream_unavailable`, retryable, with
  background-task guidance; a fast call is unaffected; a task-mode call is not
  deadline-bound.
- **F6:** `detail="lean"` omits `parameters`, includes `params_by_reference`,
  `capabilities_version` matches `detail="full"`; `full` byte-stable hash unchanged
  except for the documented additions.
- **F7:** 0.31/0.09 and 0.21/0.05 → `discordant_subthreshold` (not `discordant`);
  0.85/0.10 → `discordant`; both ≥0.5 / both-moderate / both-low unchanged; batch
  histogram counts the new verdict; headline clause present.
- **F5:** `warmup` response includes `coverage` + `note`; `warmup(mask="masked")`
  warms the masked path (assert the upstream params).
- **Regression:** existing F6/agreement/headline/batch tests still pass; capabilities
  `capabilities_version` changes from the v0.5.0 value and is internally stable.
- `make ci-local` green; coverage ≥ 80%; every module < 600 LOC.

## 14. Acceptance

- `make ci-local` green; all F1–F8 tests pass.
- `predict_splicing("chr8-140300616-A-G")` (wrong REF) returns `ref_mismatch` with the
  true reference base and a `resolve_variant` next step — not a misleading `not_found`.
- An ambiguous rsID in single and batch prediction returns `ambiguous` with callable
  per-allele alternates; no silent single-allele scoring anywhere.
- Every prediction `_meta` carries `capabilities_version`; `get_server_capabilities(
  detail="lean")` returns the trimmed doc with the same hash.
- A comprehensive + large-distance call returns a structured `upstream_unavailable`
  (with task guidance) instead of hanging the client.
- A both-builds-valid `build_mismatch` resolves via the Ensembl ref-check (~300 ms),
  not a 17 s scoring probe.
- 0.31/0.09 reports `discordant_subthreshold`, not `discordant`.
- Capabilities documents `ref_mismatch`, `ambiguous`, `discordant_subthreshold`, the
  soft deadline, warmth scope/TTL, and the lean mode.
- Re-grading against the report's dimensions clears **> 9.5 / 10** (Error handling,
  Token efficiency, Output interpretability, Observability each lift to ≥ 9.5).

*Research use only; not for clinical decision support. Splice predictions are
computational and must be interpreted alongside orthogonal evidence.*
