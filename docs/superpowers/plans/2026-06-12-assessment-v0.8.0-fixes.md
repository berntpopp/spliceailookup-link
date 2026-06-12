# v0.9.0 Assessment Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every finding in `docs/mcp-assessment-v0.8.0-2026-06-12.md` (F1–F6 + Part 1 polish) to push the spliceailookup-link MCP from 8/10 to >9.5/10, releasing as v0.9.0.

**Architecture:** Localized, finding-scoped edits to the hand-authored MCP facade. One intentional breaking change (response-shape unification, F3) gated by the existing `capabilities_version` content hash + a version bump. No new runtime dependencies. Honest framing throughout (no fabricated quota numbers; the rate signal is a soft pacing interval over the local concurrency semaphore).

**Tech Stack:** Python 3.12, FastMCP, Pydantic, pytest (+pytest-asyncio auto mode), respx (unmocked here — unit tests use the `StubService` fixture in `tests/conftest.py`), Ruff, mypy. Spec: `docs/superpowers/specs/2026-06-12-assessment-v0.8.0-fixes-design.md`.

**Test conventions:** async test functions (no decorator needed), `mcp` + `stub_service` fixtures, `structured(result)` to unwrap a `call_tool` result. Canonical happy variant `chr8-140300616-T-G` (TRAPPC9, `g_id=ENSG00000167632.19`, SpliceAI max Δ=0.83 acceptor_loss @ -2, Pangolin max Δ=0.85 splice_loss @ -2, verdict `concordant_high`).

**Commit discipline:** one commit per task; run `make test` (or the named subset) before each commit. Final `make ci-local` must be green. All commits end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

---

## File map

| File | Change |
|---|---|
| `spliceailookup_link/mcp/build_check.py` | F1: `max_length`, `out_of_range` |
| `spliceailookup_link/mcp/errors.py` | F1 classify; F2 `_ref_mismatch_fallback`; P1#1 `_stamp` dedup; P1#2 `rate_budget_snapshot` + `CoordinateRangeError` |
| `spliceailookup_link/mcp/tools/_common.py` | F1 raise in `prepare_variant`; F4 `gene_id` param in `see_also` |
| `spliceailookup_link/mcp/tools/_diagnose.py` | F2 carry `alt` on `RefMismatchError` |
| `spliceailookup_link/mcp/shaping.py` | F3 single-model `top`; F5a `_gene_label`; F6 gate `threshold_basis` |
| `spliceailookup_link/mcp/tools/_predict_shape.py` | F3 combined maxes in minimal; F5a; F6 |
| `spliceailookup_link/mcp/tools/_predict.py` | F4 `gene_id` telemetry; F6 top-level strip |
| `spliceailookup_link/mcp/tools/{spliceai,pangolin,combined}.py` | F4 pass `gene_id`; P1#2 success `rate_budget` |
| `spliceailookup_link/mcp/tools/_batch_runner.py` | F5b per-item `request_id`; P1#2 envelope `rate_budget` |
| `spliceailookup_link/config.py` | P1#2 `RATE_BUDGET_MIN_INTERVAL_MS` |
| `spliceailookup_link/mcp/resources.py` | docs: error taxonomy, shape note, rate_budget, `hint_lifecycle` |
| `spliceailookup_link/__init__.py`, `pyproject.toml` | version 0.8.0 → 0.9.0 |
| `tests/unit/test_assessment_v0_8_0.py` | new: all finding tests |
| `docs/mcp-assessment-v0.8.0-2026-06-12-resolution.md`, `README.md`, `docs/API.md` | docs |

---

## Task 1: F1 — out-of-range coordinate fast-fails as `invalid_input`

**Files:**
- Modify: `spliceailookup_link/mcp/build_check.py`
- Modify: `spliceailookup_link/mcp/errors.py`
- Modify: `spliceailookup_link/mcp/tools/_common.py`
- Test: `tests/unit/test_assessment_v0_8_0.py` (new), `tests/unit/test_build_check.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_assessment_v0_8_0.py`:

```python
"""End-to-end tests for the v0.9.0 assessment fixes (F1-F6 + Part 1)."""

from __future__ import annotations

from spliceailookup_link.mcp.build_check import out_of_range
from tests.conftest import StubService, structured


# ---------------- F1: out-of-range coordinate ----------------

def test_out_of_range_helper_detects_beyond_both_builds() -> None:
    assert out_of_range("chr1", 260_000_000) == (248_956_422, 249_250_621)
    assert out_of_range("1", 260_000_000) == (248_956_422, 249_250_621)
    # in-range in at least one build -> not out of range (build_mismatch territory)
    assert out_of_range("1", 249_000_000) is None
    # ordinary in-range -> None
    assert out_of_range("8", 140_300_616) is None
    # MT / non-standard -> None (handled elsewhere)
    assert out_of_range("chrM", 8993) is None
    assert out_of_range("chr99", 100) is None


async def test_out_of_range_returns_invalid_input_without_scoring(
    mcp, stub_service: StubService
) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr1-260000000-A-G"}))
    assert data["success"] is False
    assert data["error_code"] == "invalid_input"
    assert "248,956,422" in data["message"] and "249,250,621" in data["message"]
    assert data["fallback_tool"] == "get_server_capabilities"
    # ZERO upstream / Ensembl traffic: arithmetic-only rejection
    assert stub_service.score_calls == []
    assert stub_service.refbase_calls == []
    assert stub_service.overlap_calls == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -x -q`
Expected: FAIL (`ImportError: cannot import name 'out_of_range'`).

- [ ] **Step 3: Add length lookups to `build_check.py`**

Append after `likely_build`:

```python
def max_length(chrom: str) -> int | None:
    """Largest standard-build length for a contig, or None for MT/non-standard."""
    c = _strip_chr(chrom)
    if c in ("M", "MT") or c not in _GRCH38_LENGTHS:
        return None
    return max(_GRCH38_LENGTHS[c], _GRCH37_LENGTHS.get(c, 0))


def out_of_range(chrom: str, pos: int) -> tuple[int, int] | None:
    """Return (grch38_len, grch37_len) when pos exceeds BOTH builds; else None.

    A position past every supported build cannot score in any build, so it is an
    invalid coordinate (not a build_mismatch -- there is no build to switch to).
    """
    c = _strip_chr(chrom)
    if c in ("M", "MT") or c not in _GRCH38_LENGTHS:
        return None
    g38 = _GRCH38_LENGTHS[c]
    g37 = _GRCH37_LENGTHS.get(c, 0)
    if pos > g38 and pos > g37:
        return (g38, g37)
    return None
```

- [ ] **Step 4: Add `CoordinateRangeError` + classification to `errors.py`**

After `AmbiguousVariantError` (around line 113) add:

```python
class CoordinateRangeError(ValueError):
    """Raised when a coordinate's position exceeds the chromosome length in all builds."""

    def __init__(self, *, chrom: str, pos: int, grch38_len: int, grch37_len: int):
        self.chrom = chrom
        self.pos = pos
        super().__init__(
            f"Position {pos:,} exceeds the length of chr{chrom.removeprefix('chr')} in all "
            f"supported builds (GRCh38 {grch38_len:,}, GRCh37 {grch37_len:,}). Verify the "
            "coordinate; if you have an HGVS/rsID, resolve_variant can derive valid coordinates."
        )
```

In `_classify`, add a branch BEFORE the generic `ValueError` branch (it subclasses `ValueError`) and before `UpstreamInputError/VariantParseError` is fine too — place it right after the `AmbiguousVariantError` branch:

```python
    if isinstance(exc, CoordinateRangeError):
        return "invalid_input", False, _FALLBACK_TOOL, None
```

In `_recovery_text`, special-case the out-of-range message inside the `invalid_input` branch is unnecessary — instead, surface the exception's own message via `recovery`. Simplest: in `_recovery_text`, add at the top of the `invalid_input` block a check is awkward (no exc here). Instead, in `mcp_tool_error`, after building the payload, override recovery for `CoordinateRangeError`:

```python
    if isinstance(exc, CoordinateRangeError):
        payload["recovery"] = (
            "The position is beyond the chromosome length in every supported build, so no "
            "build can score it. Verify the coordinate against the reference. resolve_variant "
            "cannot rescue a bad coordinate -- only an HGVS/rsID input."
        )
```

`_envelope_message` already returns the (developer-authored, safe) exception text for `invalid_input`.

- [ ] **Step 5: Raise it in `prepare_variant` (`_common.py`)**

Add the import and the check. In the `coordinate` branch, immediately after `_reject_unsupported_contig(parsed.value)`:

```python
        _reject_unsupported_contig(parsed.value)
        from spliceailookup_link.mcp.build_check import out_of_range

        chrom_s, pos_s, _, _ = parsed.value.split("-", 3)
        lengths = out_of_range(chrom_s, int(pos_s))
        if lengths is not None:
            from spliceailookup_link.mcp.errors import CoordinateRangeError

            raise CoordinateRangeError(
                chrom=chrom_s, pos=int(pos_s), grch38_len=lengths[0], grch37_len=lengths[1]
            )
        inferred = detect_build_mismatch(parsed.value, genome_build)
```

(Keep the existing `detect_build_mismatch` line that follows.) Use a local import for `CoordinateRangeError` to avoid a module cycle (`_common` ↔ `errors`), matching the existing local-import pattern in this file.

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py tests/unit/test_build_check.py -q`
Expected: PASS.

- [ ] **Step 7: Regression sweep**

Run: `python -m pytest tests/unit -q`
Expected: PASS (this change only adds a new pre-flight branch; nothing else relies on out-of-range coords).

- [ ] **Step 8: Commit**

```bash
git add spliceailookup_link/mcp/build_check.py spliceailookup_link/mcp/errors.py \
  spliceailookup_link/mcp/tools/_common.py tests/unit/test_assessment_v0_8_0.py
git commit -m "fix(F1): reject out-of-range coordinates as invalid_input before scoring"
```

---

## Task 2: F2 — actionable `ref_mismatch` fallback (no resolve_variant loop)

**Files:**
- Modify: `spliceailookup_link/mcp/errors.py`
- Modify: `spliceailookup_link/mcp/tools/_diagnose.py`
- Test: `tests/unit/test_assessment_v0_8_0.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_assessment_v0_8_0.py`:

```python
# ---------------- F2: ref_mismatch fallback is actionable, never a loop ----------------

async def test_ref_mismatch_wrong_ref_falls_back_to_capabilities(
    mcp, stub_service: StubService
) -> None:
    # REF 'A' wrong in both builds; not a swap (ALT 'G' != ref base 'T').
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "T"}
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-A-G"}))
    assert data["error_code"] == "ref_mismatch"
    assert data["fallback_tool"] == "get_server_capabilities"
    assert data["fallback_args"] is None
    # the dead-end resolve_variant echo must be gone
    assert not (
        data["fallback_tool"] == "resolve_variant"
        and (data.get("fallback_args") or {}).get("variant") == "8-140300616-A-G"
    )


async def test_ref_mismatch_other_build_redirects_to_same_tool_other_build(
    mcp, stub_service: StubService
) -> None:
    # REF 'A' matches the GRCh37 base -> re-run predict on GRCh37.
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "A"}
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-A-G"}))
    assert data["error_code"] == "ref_mismatch"
    assert data["fallback_tool"] == "predict_spliceai"
    assert data["fallback_args"] == {"variant": "8-140300616-A-G", "genome_build": "GRCh37"}
    assert data["other_build_hint"]["build"] == "GRCh37"


async def test_ref_mismatch_swap_suggests_swapped_variant(
    mcp, stub_service: StubService
) -> None:
    # ALT 'T' equals the reference base 'T' at this locus -> likely REF/ALT swap.
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "C"}
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-A-T"}))
    assert data["error_code"] == "ref_mismatch"
    assert data["fallback_tool"] == "predict_spliceai"
    assert data["fallback_args"] == {"variant": "8-140300616-T-A", "genome_build": "GRCh38"}
    assert "swap" in data["recovery"].lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k ref_mismatch -q`
Expected: FAIL (current fallback is `resolve_variant` with the same coord).

- [ ] **Step 3: Carry `alt` on `RefMismatchError`**

In `errors.py` `RefMismatchError.__init__`, add an `alt` parameter and store it:

```python
    def __init__(
        self,
        *,
        variant_id: str,
        observed_ref: str,
        reference_base: str,
        build: str,
        chrom: str,
        pos: int,
        alt: str = "",
        other_build_hint: dict[str, str] | None = None,
    ):
        self.variant_id = variant_id
        self.observed_ref = observed_ref
        self.reference_base = reference_base
        self.build = build
        self.alt = alt
        self.other_build_hint = other_build_hint
```

(Keep the existing `super().__init__(...)` message.)

In `_diagnose.py` `_ref_mismatch_error`, parse and pass `alt`:

```python
def _ref_mismatch_error(
    variant_id: str, requested_build: GenomeBuild, check: RefCheck
) -> RefMismatchError:
    hint: dict[str, str] | None = None
    if check.other_build:
        hint = {
            "build": check.other_build,
            "note": (
                f"REF '{check.observed_ref}' matches the {check.other_build} reference base at "
                f"{check.chrom}:{check.pos}; if you intended {check.other_build}, re-run with "
                f"genome_build={check.other_build}, or call resolve_variant for canonical "
                "CHROM-POS-REF-ALT."
            ),
        }
    try:
        _c, _p, _r, alt = split_variant_id(variant_id)
    except VariantParseError:
        alt = ""
    return RefMismatchError(
        variant_id=variant_id,
        observed_ref=check.observed_ref or "",
        reference_base=check.requested_base or "",
        build=requested_build,
        chrom=check.chrom or "",
        pos=check.pos or 0,
        alt=alt,
        other_build_hint=hint,
    )
```

- [ ] **Step 4: Add `_ref_mismatch_fallback` and wire it in `_classify`/`mcp_tool_error`**

In `errors.py`, add a helper near `_fallback_for`:

```python
def _ref_mismatch_fallback(
    exc: "RefMismatchError", context: McpErrorContext
) -> tuple[str, dict[str, Any] | None]:
    """An actionable fallback for a coordinate ref_mismatch (never the same-coord loop)."""
    tool = context.tool_name if context.tool_name in _PREDICTION_TOOLS else "predict_splicing"
    if exc.other_build_hint:
        return tool, {"variant": exc.variant_id, "genome_build": exc.other_build_hint["build"]}
    ref, alt, base = exc.observed_ref, exc.alt, exc.reference_base
    if ref and alt and base and len(ref) == len(alt) == len(base) and alt.upper() == base.upper():
        try:
            chrom, pos, r, a = exc.variant_id.split("-", 3)
            swapped = f"{chrom}-{pos}-{a}-{r}"
            return tool, {"variant": swapped, "genome_build": exc.build}
        except ValueError:
            pass
    return _FALLBACK_TOOL, None
```

In `_classify`, replace the `RefMismatchError` branch:

```python
    if isinstance(exc, RefMismatchError):
        tool, args = _ref_mismatch_fallback(exc, context)
        return "ref_mismatch", False, tool, args
```

In `mcp_tool_error`, where `RefMismatchError` + `other_build_hint` is handled, also append the swap sentence when the fallback is a swap. Replace that block with:

```python
    if isinstance(exc, RefMismatchError):
        if exc.other_build_hint:
            payload["other_build_hint"] = exc.other_build_hint
            payload["recovery"] = f"{payload['recovery']} {exc.other_build_hint['note']}"
        elif (
            exc.observed_ref
            and exc.alt
            and exc.reference_base
            and exc.alt.upper() == exc.reference_base.upper()
            and len(exc.observed_ref) == len(exc.alt) == len(exc.reference_base)
        ):
            payload["recovery"] = (
                f"{payload['recovery']} The ALT base matches the reference here, so the most "
                "likely cause is a REF/ALT swap; the fallback re-runs with REF/ALT swapped."
            )
```

(Add `from typing import TYPE_CHECKING` use is unnecessary — `RefMismatchError` is defined in this module, so drop the quotes in the helper signature: use `exc: RefMismatchError`.)

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k ref_mismatch -q`
Expected: PASS.

- [ ] **Step 6: Regression sweep**

Run: `python -m pytest tests/unit/test_errors.py tests/unit/test_diagnose.py tests/unit/test_ux_9_5.py -q`
Expected: PASS — but `test_diagnose.py`/`test_errors.py` may assert the old `resolve_variant` ref_mismatch fallback. If so, update those assertions to the new behavior (capabilities for plain wrong-REF; same-tool+other-build for the other-build case). Re-run until green.

- [ ] **Step 7: Commit**

```bash
git add spliceailookup_link/mcp/errors.py spliceailookup_link/mcp/tools/_diagnose.py \
  tests/unit/test_assessment_v0_8_0.py tests/unit/test_errors.py tests/unit/test_diagnose.py
git commit -m "fix(F2): make ref_mismatch fallback actionable (other-build / swap / capabilities)"
```

---

## Task 3: F3a — single-model `top` in every response mode

**Files:**
- Modify: `spliceailookup_link/mcp/shaping.py`
- Test: `tests/unit/test_assessment_v0_8_0.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
# ---------------- F3: stable summary keys across modes ----------------

async def test_spliceai_top_present_in_all_modes(mcp) -> None:
    for mode in ("minimal", "compact", "full"):
        data = structured(
            await mcp.call_tool(
                "predict_spliceai", {"variant": "chr8-140300616-T-G", "response_mode": mode}
            )
        )
        assert data["top"] == {"class": "acceptor_loss", "score": 0.83, "position": -2}, mode
        assert data["max_delta_score"] == 0.83, mode


async def test_pangolin_top_present_in_all_modes(mcp) -> None:
    for mode in ("minimal", "compact", "full"):
        data = structured(
            await mcp.call_tool(
                "predict_pangolin", {"variant": "chr8-140300616-T-G", "response_mode": mode}
            )
        )
        assert data["top"]["class"] == "splice_loss", mode
        assert data["top"]["score"] == 0.85, mode
        assert data["max_delta_score"] == 0.85, mode
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k "top_present" -q`
Expected: FAIL (compact/full have no `top`).

- [ ] **Step 3: Compute `top` once in shaping and attach in all modes**

In `shaping.py`, add a helper (after `band`):

```python
def _top_delta(transcripts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The single strongest {class, score, position} across the reported transcripts."""
    best_class = best_score = best_pos = None
    for t in transcripts:
        for name, d in (t.get("delta_scores") or {}).items():
            s = (d or {}).get("score")
            if s is not None and (best_score is None or s > best_score):
                best_score, best_class, best_pos = s, name, (d or {}).get("position")
    if best_class is None:
        return None
    return {"class": best_class, "score": best_score, "position": best_pos}
```

In `shape_spliceai`, after `result["interpretation"] = ...` and before `if truncated`, add:

```python
    top = _top_delta(shaped)
    if top is not None:
        result["top"] = top
```

Do the same in `shape_pangolin`.

Update `_minimal_single_model` to reuse the precomputed `top` instead of recomputing: replace its delta-scan with a read of the passed result. Since `_minimal_single_model` receives the full `result` dict, change it to:

```python
def _minimal_single_model(result: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model": result["model"],
        "variant_id": result["variant_id"],
        "genome_build": result["genome_build"],
        "gene": (result.get("transcripts") or [{}])[0].get("gene"),
        "max_delta_score": result.get("max_delta_score"),
        "interpretation": {"band": band(result.get("max_delta_score"))},
        "headline": result["headline"],
    }
    if result.get("top") is not None:
        out["top"] = result["top"]
    cons = result.get("consequence") or {}
    aberr = (cons.get("aberrations") or [{}])[0].get("type") if cons else None
    if aberr:
        out["consequence_summary"] = aberr
    return out
```

(`shape_spliceai`/`shape_pangolin` already call `_minimal_single_model(result)` at the end with the full result, so `top` is available.)

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k "top_present" -q`
Expected: PASS.

- [ ] **Step 5: Regression sweep**

Run: `python -m pytest tests/unit/test_shaping.py tests/unit/test_tools.py -q`
Expected: may fail where a test asserts the exact compact dict (now has an extra `top`). Update those assertions to include `top`. Re-run until green, then `python -m pytest tests/unit -q`.

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/shaping.py tests/unit/
git commit -m "feat(F3a): emit stable top{class,score,position} in every single-model mode"
```

---

## Task 4: F3b — combined per-model maxes unified into `agreement{}` in every mode

**Files:**
- Modify: `spliceailookup_link/mcp/tools/_predict_shape.py`
- Test: `tests/unit/test_assessment_v0_8_0.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
async def test_combined_maxes_in_agreement_all_modes(mcp) -> None:
    for mode in ("minimal", "compact", "full"):
        data = structured(
            await mcp.call_tool(
                "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": mode}
            )
        )
        ag = data["agreement"]
        assert ag["verdict"] == "concordant_high", mode
        assert ag["spliceai_max_delta"] == 0.83, mode
        assert ag["pangolin_max_delta"] == 0.85, mode
        # the divergent minimal-only names are gone
        assert "spliceai_max" not in data, mode
        assert "pangolin_max" not in data, mode
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k combined_maxes -q`
Expected: FAIL (minimal emits `spliceai_max`/`pangolin_max` at top level; agreement in minimal has only `verdict`).

- [ ] **Step 3: Rewrite `minimal_combined`**

In `_predict_shape.py`, replace `minimal_combined`:

```python
def minimal_combined(result: dict[str, Any], gene: str | None) -> dict[str, Any]:
    """Headline-tier projection of a combined predict_splicing result."""
    ag = result.get("agreement") or {}
    out: dict[str, Any] = {
        "variant_id": result["variant_id"],
        "genome_build": result["genome_build"],
        "gene": gene,
        "agreement": {
            "verdict": ag.get("verdict"),
            "spliceai_max_delta": ag.get("spliceai_max_delta"),
            "pangolin_max_delta": ag.get("pangolin_max_delta"),
        },
        "interpretation": {"band": result["interpretation"]["band"]},
        "headline": result["headline"],
    }
    if result.get("molecular_consequence"):
        out["molecular_consequence"] = result["molecular_consequence"]
    return out
```

(`assess_agreement` already populates `spliceai_max_delta`/`pangolin_max_delta` when both models scored; when one model is missing it returns `{verdict:"incomplete", detail:...}` with no maxes, so `ag.get(...)` yields `None` — acceptable and consistent.)

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k combined_maxes -q`
Expected: PASS.

- [ ] **Step 5: Regression sweep**

Run: `python -m pytest tests/unit/test_predict_shape.py tests/unit/test_batch.py -q`
Expected: `test_batch.py` reads `r.get("spliceai_max")` in `_batch_runner._result_max_delta` — that path still works for compact items (uses `spliceai.max_delta_score`); minimal batch items now lack `spliceai_max` but batch uses compact internally, so unaffected. Update any predict_shape test asserting old minimal keys. Then `python -m pytest tests/unit -q`.

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/tools/_predict_shape.py tests/unit/
git commit -m "feat(F3b): unify combined per-model maxes into agreement{} across all modes"
```

---

## Task 5: F6 — `threshold_basis` only in `full` mode

**Files:**
- Modify: `spliceailookup_link/mcp/shaping.py`
- Modify: `spliceailookup_link/mcp/tools/_predict.py`
- Test: `tests/unit/test_assessment_v0_8_0.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
# ---------------- F6: threshold_basis only in full ----------------

async def test_threshold_basis_only_in_full_single_model(mcp) -> None:
    compact = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    assert "threshold_basis" not in compact["interpretation"]
    assert compact["interpretation"]["band"] == "high"
    full = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
        )
    )
    assert "threshold_basis" in full["interpretation"]


async def test_threshold_basis_only_in_full_combined(mcp) -> None:
    compact = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert "threshold_basis" not in compact["interpretation"]
    full = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
        )
    )
    assert "threshold_basis" in full["interpretation"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k threshold_basis -q`
Expected: FAIL (compact currently includes `threshold_basis`).

- [ ] **Step 3: Gate it in single-model shaping**

In `shaping.py`, change both `shape_spliceai` and `shape_pangolin`:

```python
    result["interpretation"] = {"band": band(max_overall)}
    if response_mode == "full":
        result["interpretation"]["threshold_basis"] = THRESHOLD_BASIS
```

- [ ] **Step 4: Strip it from the combined top-level interpretation in compact/minimal**

In `_predict.py`, after `result["interpretation"] = combined_interpretation(sai_max, pang_max)`:

```python
    result["interpretation"] = combined_interpretation(sai_max, pang_max)
    if response_mode != "full":
        result["interpretation"].pop("threshold_basis", None)
```

(`combined_interpretation` still emits it; `minimal_combined` already keeps only `band`, so the strip covers the compact path. Leave `_predict_shape.combined_interpretation` as-is for the `full` path.)

- [ ] **Step 5: Run tests + regression**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k threshold_basis -q`
Expected: PASS.
Run: `python -m pytest tests/unit -q` — fix any test asserting `threshold_basis` in compact (it now belongs to full).

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/shaping.py spliceailookup_link/mcp/tools/_predict.py tests/unit/
git commit -m "perf(F6): drop static threshold_basis glossary from compact/minimal (full-only)"
```

---

## Task 6: P1#1 — no `capabilities_version` duplication on the capabilities call

**Files:**
- Modify: `spliceailookup_link/mcp/errors.py`
- Test: `tests/unit/test_assessment_v0_8_0.py`

- [ ] **Step 1: Write failing test**

Append:

```python
# ---------------- P1#1: capabilities_version not duplicated ----------------

async def test_capabilities_version_not_duplicated_in_meta(mcp) -> None:
    data = structured(await mcp.call_tool("get_server_capabilities", {}))
    assert "capabilities_version" in data  # top-level (the document's own hash)
    assert "capabilities_version" not in data["_meta"], "must not duplicate in _meta"


async def test_prediction_still_carries_version_in_meta(mcp) -> None:
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    assert "capabilities_version" not in data  # no top-level on predictions
    assert "capabilities_version" in data["_meta"]  # provenance lives in _meta here
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k capabilities_version -q`
Expected: FAIL (first test — `_meta` currently carries a duplicate).

- [ ] **Step 3: Guard `_stamp` in `run_mcp_tool`**

In `errors.py` `_stamp`, change the lean/version block:

```python
        if not lean_meta and "capabilities_version" not in envelope:
            meta["capabilities_version"] = get_capabilities_version()
```

(Top-level `capabilities_version` only exists on the capabilities document, so prediction payloads keep their `_meta` copy; the capabilities call no longer duplicates.)

- [ ] **Step 4: Run tests + regression**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k capabilities_version -q`
Expected: PASS.
Run: `python -m pytest tests/unit -q`.

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/errors.py tests/unit/test_assessment_v0_8_0.py
git commit -m "perf(P1#1): drop duplicate capabilities_version from _meta on the capabilities call"
```

---

## Task 7: P1#2 — proactive `rate_budget` on success + `retry_after_s` on rate_limited

**Files:**
- Modify: `spliceailookup_link/config.py`
- Modify: `spliceailookup_link/mcp/errors.py`
- Modify: `spliceailookup_link/mcp/tools/spliceai.py`, `pangolin.py`, `combined.py`
- Modify: `spliceailookup_link/mcp/tools/_batch_runner.py`
- Test: `tests/unit/test_assessment_v0_8_0.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
# ---------------- P1#2: proactive rate budget ----------------

async def test_success_carries_rate_budget(mcp) -> None:
    for tool in ("predict_spliceai", "predict_pangolin", "predict_splicing"):
        data = structured(await mcp.call_tool(tool, {"variant": "chr8-140300616-T-G"}))
        rb = data["_meta"]["rate_budget"]
        assert rb["limit"] == 2
        assert rb["unit"] == "concurrent_requests"
        assert rb["min_interval_ms"] == 12000
        assert "remaining" not in rb  # success: no fabricated remaining


async def test_rate_budget_present_on_minimal(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "chr8-140300616-T-G", "response_mode": "minimal"}
        )
    )
    assert data["_meta"]["rate_budget"]["min_interval_ms"] == 12000


async def test_rate_limited_error_carries_retry_after(mcp, stub_service: StubService) -> None:
    from spliceailookup_link.api import RateLimitedError

    stub_service.score_error = RateLimitedError("saturated")
    data = structured(await mcp.call_tool("predict_spliceai", {"variant": "chr8-140300616-T-G"}))
    assert data["error_code"] == "rate_limited"
    rb = data["_meta"]["rate_budget"]
    assert rb["limit"] == 2
    assert rb["remaining"] == 0
    assert rb["retry_after_s"] == 12
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k "rate_budget or retry_after" -q`
Expected: FAIL.

- [ ] **Step 3: Add the setting**

In `config.py`, after `WARM_THRESHOLD_MS`:

```python
    # Proactive client-pacing signal. The upstream is "several requests/min"; this is the
    # recommended soft minimum spacing between cache-miss scoring calls so an autonomous
    # caller paces a burst instead of discovering the limit by hitting it. Surfaced as
    # _meta.rate_budget.min_interval_ms on success and retry_after_s on a rate_limited error.
    RATE_BUDGET_MIN_INTERVAL_MS: int = 12000
```

- [ ] **Step 4: Add `rate_budget_snapshot` to `errors.py` and use it for the error path**

Add near the top-level helpers:

```python
def rate_budget_snapshot(*, saturated: bool) -> dict[str, Any]:
    """The advertised concurrency budget + soft client-pacing interval.

    The cap is a LOCAL asyncio.Semaphore (MAX_CONCURRENCY), not a tracked time-window
    quota. On success we advertise the soft min spacing for cache-miss calls; on a
    rate_limited failure we add remaining=0 and a retry_after_s for immediate backoff.
    """
    snap: dict[str, Any] = {
        "limit": settings.MAX_CONCURRENCY,
        "unit": "concurrent_requests",
        "min_interval_ms": settings.RATE_BUDGET_MIN_INTERVAL_MS,
    }
    if saturated:
        snap["remaining"] = 0
        snap["retry_after_s"] = max(1, round(settings.RATE_BUDGET_MIN_INTERVAL_MS / 1000))
    return snap
```

In `mcp_tool_error`, replace the inline `rate_budget` block:

```python
    if error_code == "rate_limited":
        payload["_meta"]["rate_budget"] = rate_budget_snapshot(saturated=True)
```

- [ ] **Step 5: Emit on success in the three scoring tools**

In `spliceai.py`, inside the `meta` dict construction (right after `meta = {"cache": ..., "served_warm": ...}`), add:

```python
            from spliceailookup_link.mcp.errors import rate_budget_snapshot

            meta["rate_budget"] = rate_budget_snapshot(saturated=False)
```

Apply the identical addition in `pangolin.py` (same `meta` block) and in `combined.py` (after `meta["served_warm"] = ...`). Place it unconditionally (not under `if not lean`) so minimal/lean callers still get the pacing signal.

- [ ] **Step 6: Emit once on the batch envelope**

In `_batch_runner.py` `run_batch`, where `meta = {"items_submitted": total, "max_items": max_items}` is built, add:

```python
    from spliceailookup_link.mcp.errors import rate_budget_snapshot

    meta["rate_budget"] = rate_budget_snapshot(saturated=False)
```

- [ ] **Step 7: Run tests + regression**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k "rate_budget or retry_after" -q`
Expected: PASS.
Run: `python -m pytest tests/unit -q` — fix any error-path test asserting the old rate_budget dict (now includes `retry_after_s`).

- [ ] **Step 8: Commit**

```bash
git add spliceailookup_link/config.py spliceailookup_link/mcp/errors.py \
  spliceailookup_link/mcp/tools/spliceai.py spliceailookup_link/mcp/tools/pangolin.py \
  spliceailookup_link/mcp/tools/combined.py spliceailookup_link/mcp/tools/_batch_runner.py \
  tests/unit/test_assessment_v0_8_0.py
git commit -m "feat(P1#2): proactive _meta.rate_budget on success + retry_after_s on rate_limited"
```

---

## Task 8: F4 — gtex `see_also` uses the resolved Ensembl gene id

**Files:**
- Modify: `spliceailookup_link/mcp/tools/_common.py`
- Modify: `spliceailookup_link/mcp/tools/_predict.py`
- Modify: `spliceailookup_link/mcp/tools/spliceai.py`, `pangolin.py`, `combined.py`
- Test: `tests/unit/test_assessment_v0_8_0.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
# ---------------- F4: gtex see_also uses the gencode id ----------------

async def test_gtex_see_also_uses_gene_id_in_full(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_spliceai", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
        )
    )
    gtex = next(h for h in data["_meta"]["see_also"] if h["server"] == "gtex-link")
    assert gtex["example"]["tool"] == "get_median_expression_levels"
    assert gtex["example"]["arguments"]["gencode_id"] == ["ENSG00000167632.19"]


async def test_gtex_see_also_uses_gene_id_combined_full(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "response_mode": "full"}
        )
    )
    gtex = next(h for h in data["_meta"]["see_also"] if h["server"] == "gtex-link")
    assert gtex["example"]["arguments"]["gencode_id"] == ["ENSG00000167632.19"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k gtex -q`
Expected: FAIL (currently passes the symbol `["TRAPPC9"]`).

- [ ] **Step 3: Add `gene_id` param to `see_also_for`/`_see_also_full`**

In `_common.py`:

```python
def see_also_for(
    variant_id: str,
    genome_build: GenomeBuild,
    gene: str | None,
    response_mode: str = "compact",
    gene_id: str | None = None,
) -> list[dict[str, Any]]:
    if response_mode == "minimal":
        return []
    full = _see_also_full(variant_id, genome_build, gene, gene_id)
    if response_mode == "full":
        return full
    return [{"server": h["server"], "hint": h["hint"]} for h in full]


def _see_also_full(
    variant_id: str, genome_build: GenomeBuild, gene: str | None, gene_id: str | None = None
) -> list[dict[str, Any]]:
    dataset = "gnomad_r4" if genome_build == "GRCh38" else "gnomad_r2_1"
    hints: list[dict[str, Any]] = [
        {
            "server": "gnomad-link",
            "hint": "allele frequency and ClinVar classification for this variant",
            "example": {
                "tool": "get_variant_frequencies",
                "arguments": {"variant_id": variant_id, "dataset": dataset},
            },
        }
    ]
    if gene:
        hints.append(
            {
                "server": "genereviews-link",
                "hint": f"gene-disease context for {gene}",
                "example": {"tool": "search_passages", "arguments": {"q": gene}},
            }
        )
        if gene_id:
            gtex_example = {
                "tool": "get_median_expression_levels",
                "arguments": {"gencode_id": [gene_id]},
            }
        else:
            gtex_example = {"tool": "search_gtex_genes", "arguments": {"query": gene}}
        hints.append(
            {"server": "gtex-link", "hint": f"tissue expression for {gene}", "example": gtex_example}
        )
        hints.append(
            {
                "server": "uniprot-link",
                "hint": f"protein domains, features, and disease variants for {gene}",
                "example": {
                    "tool": "find_proteins",
                    "arguments": {"gene": gene, "organism_taxon": 9606, "reviewed": True},
                },
            }
        )
    return hints
```

- [ ] **Step 4: Thread `gene_id` through telemetry (combined) and the single-model caller**

In `_predict.py`, capture the top gene_id. After `gene = sai_max = pang_max = consequence = None` add `gene_id = None`; in the SpliceAI block after `gene = sai_top.get("gene")` add `gene_id = sai_top.get("gene_id")`; in the Pangolin block, inside `if gene is None:` also set `if gene_id is None: gene_id = pang_top.get("gene_id")`. Then add to the `telemetry` dict: `"gene_id": gene_id,`.

In `combined.py`, change the see_also call:

```python
                    meta["see_also"] = see_also_for(
                        result["variant_id"], genome_build, tel["gene"], response_mode,
                        gene_id=tel.get("gene_id"),
                    )
```

In `spliceai.py`, compute `gene_id` next to `gene` and pass it:

```python
            gene = shaped.get("gene") or (shaped.get("transcripts") or [{}])[0].get("gene")
            gene_id = (shaped.get("transcripts") or [{}])[0].get("gene_id")
            ...
                    meta["see_also"] = see_also_for(
                        prepared.variant_id, genome_build, gene, response_mode, gene_id=gene_id
                    )
```

Apply the same `gene_id` extraction + pass-through in `pangolin.py`.

- [ ] **Step 5: Run tests + regression**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k gtex -q`
Expected: PASS.
Run: `python -m pytest tests/unit -q` — fix any see_also test asserting `gencode_id:[symbol]`.

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/tools/_common.py spliceailookup_link/mcp/tools/_predict.py \
  spliceailookup_link/mcp/tools/spliceai.py spliceailookup_link/mcp/tools/pangolin.py \
  spliceailookup_link/mcp/tools/combined.py tests/unit/
git commit -m "fix(F4): gtex see_also passes the resolved gencode id, not the gene symbol"
```

---

## Task 9: F5a — symbol-less lncRNA headline

**Files:**
- Modify: `spliceailookup_link/mcp/shaping.py`
- Modify: `spliceailookup_link/mcp/tools/_predict_shape.py`
- Test: `tests/unit/test_assessment_v0_8_0.py`

- [ ] **Step 1: Write failing test (unit-level on the headline helpers)**

Append:

```python
# ---------------- F5a: symbol-less lncRNA headline ----------------

def test_gene_label_marks_ensembl_only_genes() -> None:
    from spliceailookup_link.mcp.shaping import _gene_label

    assert _gene_label("TRAPPC9") == "TRAPPC9"
    assert _gene_label("ENSG00000241860") == "ENSG00000241860 (no gene symbol)"
    assert _gene_label(None) == "unknown gene"


def test_spliceai_headline_uses_gene_label() -> None:
    from spliceailookup_link.mcp.shaping import spliceai_headline

    shaped = {
        "genome_build": "GRCh38",
        "variant_id": "1-100000-C-G",
        "transcripts": [
            {"gene": "ENSG00000241860", "delta_scores": {"acceptor_gain": {"score": 0.0, "position": 0}}}
        ],
    }
    assert "ENSG00000241860 (no gene symbol)" in spliceai_headline(shaped)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k gene_label -q`
Expected: FAIL (`_gene_label` undefined).

- [ ] **Step 3: Add `_gene_label` and use it in headlines**

In `shaping.py`, add near `_strength`:

```python
_ENSG_ONLY_RE = re.compile(r"^ENSG\d+")


def _gene_label(gene: str | None) -> str:
    """Human-facing gene label; flags symbol-less Ensembl-only genes (e.g. some lncRNAs)."""
    if not gene:
        return "unknown gene"
    return f"{gene} (no gene symbol)" if _ENSG_ONLY_RE.match(gene) else gene
```

In `spliceai_headline` and `pangolin_headline`, replace `gene = top.get("gene") or "unknown gene"` with `gene = _gene_label(top.get("gene"))`.

In `_predict_shape.py` `combined_headline`, replace `gene_label = gene or "variant"` with a label that flags ENSG-only too. Import and reuse:

```python
from spliceailookup_link.mcp.shaping import THRESHOLD_BASIS, _gene_label, band
...
    gene_label = _gene_label(gene) if gene else "variant"
```

(`_gene_label(None)` returns "unknown gene"; combined prefers "variant" for the no-gene case, so keep the `if gene else "variant"` guard.)

- [ ] **Step 4: Run tests + regression**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k "gene_label or headline" -q`
Expected: PASS.
Run: `python -m pytest tests/unit/test_shaping.py tests/unit/test_predict_shape.py -q`.

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/shaping.py spliceailookup_link/mcp/tools/_predict_shape.py \
  tests/unit/test_assessment_v0_8_0.py
git commit -m "fix(F5a): flag symbol-less Ensembl-only genes in the headline"
```

---

## Task 10: F5b — per-item `request_id` in batch results

**Files:**
- Modify: `spliceailookup_link/mcp/tools/_batch_runner.py`
- Test: `tests/unit/test_assessment_v0_8_0.py`

- [ ] **Step 1: Write failing test**

Append:

```python
# ---------------- F5b: batch per-item request_id ----------------

async def test_batch_items_have_request_id(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_splicing_batch",
            {"variants": ["chr8-140300616-T-G", "chr8-140300616-T-X"]},
        )
    )
    ids = [r["_meta"]["request_id"] for r in data["results"] if "_meta" in r]
    # success items carry request_id in _meta
    assert all(isinstance(i, str) and len(i) == 12 for i in ids)
    # error items carry request_id at top level
    err = next(r for r in data["results"] if r.get("error_code"))
    assert isinstance(err["request_id"], str) and len(err["request_id"]) == 12
    # request_ids are unique across items
    all_ids = ids + [err["request_id"]]
    assert len(set(all_ids)) == len(all_ids)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k batch_items_have_request_id -q`
Expected: FAIL.

- [ ] **Step 3: Generate a per-item id**

In `_batch_runner.py`, add `import uuid` at the top. In `_run_item`, generate the id once and pass it to the item builders:

```python
    retried = False
    request_id = uuid.uuid4().hex[:12]
    while True:
        try:
            one = await predict_fn(service, variant=variant, genome_build=genome_build, **params)
            return _success_item(one, variant, request_id), "ok", retried
        except Exception as exc:
            item, code = _error_item(exc, variant, genome_build, request_id)
            ...
```

Update `_success_item` signature to `(one, variant, request_id)` and set `item_meta["request_id"] = request_id` (place it first in the dict). Update `_error_item` signature to `(exc, variant, genome_build, request_id)` and add `item["request_id"] = request_id` (top-level, since error items have no `_meta`).

- [ ] **Step 4: Run tests + regression**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k batch_items_have_request_id -q`
Expected: PASS.
Run: `python -m pytest tests/unit/test_batch.py -q`.

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/tools/_batch_runner.py tests/unit/test_assessment_v0_8_0.py
git commit -m "feat(F5b): per-item request_id in batch results for log correlation"
```

---

## Task 11: Docs — capabilities/reference, hint lifecycle, version bump, resolution doc

**Files:**
- Modify: `spliceailookup_link/mcp/resources.py`
- Modify: `spliceailookup_link/__init__.py`, `pyproject.toml`
- Create: `docs/mcp-assessment-v0.8.0-2026-06-12-resolution.md`
- Modify: `README.md`, `docs/API.md`
- Test: `tests/unit/test_assessment_v0_8_0.py`

- [ ] **Step 1: Write failing capabilities tests**

Append:

```python
# ---------------- docs: capabilities reflect the new contract ----------------

def test_capabilities_document_v0_9_contract() -> None:
    from spliceailookup_link.mcp.resources import get_capabilities_resource

    full = get_capabilities_resource(detail="full")
    blob = str(full).lower()
    assert "hint_lifecycle" in full["response_fields"]
    assert "min_interval_ms" in blob
    assert "retry_after_s" in blob
    # F1 doc correction: out-of-range is invalid_input, not build_mismatch
    inv = full  # error taxonomy lives in the reference resource; check both
    from spliceailookup_link.mcp.resources import get_reference_resource

    ref = get_reference_resource()
    assert "out of range" in str(ref).lower() or "exceeds" in str(ref).lower()


def test_version_is_0_9_0() -> None:
    from spliceailookup_link import __version__

    assert __version__ == "0.9.0"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k "v0_9_contract or version_is" -q`
Expected: FAIL.

- [ ] **Step 3: Bump version**

`spliceailookup_link/__init__.py`: `__version__ = "0.9.0"`.
`pyproject.toml`: `version = "0.9.0"`.

- [ ] **Step 4: Update `resources.py`**

In `get_capabilities_resource`:
- Add `hint_lifecycle` under `response_fields`:
  ```python
            "hint_lifecycle": (
                "next_commands and see_also are designed to be read once. After your first "
                "successful predict_* call in a session, set include_hints=false (and "
                "include_see_also=false) for the remaining calls to cut per-call tokens -- the "
                "workflow does not change within a session. The server is stateless, so the flag "
                "must be re-passed each call."
            ),
  ```
- Extend the `include_hints` text to reference `hint_lifecycle`.
- In `concurrency.rate_budget`, document the success-path signal and `retry_after_s`:
  ```python
            "rate_budget": (
                "_meta.rate_budget appears on every prediction success as "
                "{limit, unit:'concurrent_requests', min_interval_ms} -- the cap is a LOCAL "
                "concurrency semaphore (not a time-windowed quota), and min_interval_ms is the "
                "recommended soft spacing between cache-miss scoring calls so you can pace a "
                "burst. On a rate_limited error it adds remaining:0 and retry_after_s for "
                "immediate backoff. Cached responses do not consume the budget."
            ),
  ```
- Rename the `v0_8_0_shape` key to `v0_9_0_shape` and rewrite it:
  ```python
            "v0_9_0_shape": (
                "Every prediction mode exposes the headline number consistently: single-model "
                "results carry top:{class,score,position} + max_delta_score in minimal, compact, "
                "and full; predict_splicing carries agreement:{verdict, spliceai_max_delta, "
                "pangolin_max_delta} in every mode (the older minimal-only spliceai_max/"
                "pangolin_max names are removed). interpretation.threshold_basis appears only in "
                "response_mode='full' (the band is always present; the glossary is in "
                "spliceailookup://reference). A coordinate whose position exceeds the chromosome "
                "length in all builds is invalid_input (not build_mismatch -- no build can score "
                "it), rejected locally before any upstream call. ref_mismatch fallbacks are now "
                "actionable: the matching build, a REF/ALT swap, or get_server_capabilities -- "
                "never the same wrong coordinate back into resolve_variant."
            ),
  ```

In `get_reference_resource`, update the `invalid_input` and `build_mismatch` `when` text:
- `invalid_input` → add: "...; also when a coordinate's position is out of range (exceeds the chromosome length in all supported builds), rejected locally before any upstream call."
- `build_mismatch` → tighten: "the coordinate is valid only on the OTHER build (in range there / scores there); set genome_build correctly. A position out of range in EVERY build is invalid_input, not this; a wrong REF at an in-range position is ref_mismatch."

- [ ] **Step 5: Run capabilities tests**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -k "v0_9_contract or version_is" -q`
Expected: PASS.

- [ ] **Step 6: Write the resolution doc**

Create `docs/mcp-assessment-v0.8.0-2026-06-12-resolution.md` mapping each finding (F1–F6, P1#1–#3, F5a/b) to its commit/fix, the version bump, and the intended score deltas. Mirror the table style of `docs/mcp-ux-9.5-resolution.md`.

- [ ] **Step 7: Update README + API.md**

`README.md` / `docs/API.md`: bump any version references to 0.9.0; update the response-shape description (single-model `top` in all modes; combined `agreement.*_max_delta`; `threshold_basis` full-only; `_meta.rate_budget`). Keep edits minimal and factual.

- [ ] **Step 8: Commit**

```bash
git add spliceailookup_link/mcp/resources.py spliceailookup_link/__init__.py pyproject.toml \
  docs/mcp-assessment-v0.8.0-2026-06-12-resolution.md README.md docs/API.md \
  tests/unit/test_assessment_v0_8_0.py
git commit -m "docs: v0.9.0 capabilities/reference, hint_lifecycle, resolution doc, version bump"
```

---

## Task 12: Final verification

- [ ] **Step 1: Full CI**

Run: `make ci-local`
Expected: format clean, ruff clean, `lint-loc` ≤600 LOC/file (verify `errors.py` did not cross 600 — if it did, extract `rate_budget_snapshot` + the interval into a new `spliceailookup_link/mcp/rate_budget.py` and re-import), mypy clean, all unit tests PASS.

- [ ] **Step 2: Confirm capabilities_version changed**

Run:
```bash
python -c "from spliceailookup_link.mcp.resources import get_capabilities_version as v; print(v())"
```
Expected: a 12-char hash DIFFERENT from `68685f20483a` (the v0.8.0 hash) — proves warm clients will re-discover.

- [ ] **Step 3: Coverage spot-check**

Run: `python -m pytest tests/unit/test_assessment_v0_8_0.py -q`
Expected: all new tests PASS.

- [ ] **Step 4: Final commit (only if `make ci-local` produced formatting changes)**

```bash
git add -A
git commit -m "chore: ci-local formatting for v0.9.0 assessment fixes"
```

---

## Self-review notes

- **Spec coverage:** F1 (T1), F2 (T2), F3a (T3), F3b (T4), F6 (T5), P1#1 (T6), P1#2 (T7), F4 (T8), F5a (T9), F5b (T10), P1#3 + docs + version (T11), verification (T12). All spec sections mapped.
- **LOC risk:** `errors.py` is the only near-cap file (476 → +~40); T12 Step 1 has the extraction fallback. `build_check.py` (102→~130), `shaping.py` (486→~520) stay under 600 — watch `shaping.py`; if it crosses, the `_top_delta`/`_gene_label` helpers can move to a `shaping_summary.py`, but ~520 is safe.
- **Type/name consistency:** `out_of_range` returns `tuple[int,int]|None` everywhere; `rate_budget_snapshot(*, saturated: bool)` used in T7 (error) + T7 (success callers); `_gene_label` defined in `shaping.py`, imported by `_predict_shape.py`; `see_also_for(..., gene_id=None)` keyword consistent across all three callers.
- **Breaking-change containment:** only F3 changes existing keys; the `capabilities_version` hash change (T12 Step 2) is the documented signal; standalone vs combined echo behavior preserved.
