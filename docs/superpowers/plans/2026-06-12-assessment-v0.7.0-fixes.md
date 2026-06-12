# v0.7.0 Assessment Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every defect (D1–D5) and consumer recommendation (C3 batch contract, C4 `served_warm`, C5 lean resources, Rec-#5 coverage) from `docs/mcp-assessment-v0.7.0-2026-06-12.md` so a re-run scores > 9.5 / 10.

**Architecture:** D1 and D2 are unified by moving the existing cheap, cached Ensembl reference-base check *before* the scoring dispatch (`prepare_variant`) and deleting the unreliable "other-build base matches REF → build_mismatch" mapping. The remaining items are localized edits to shaping, `_meta` assembly, the resolver, the batch envelope, and the capabilities document, each behind a deterministic respx/stub-mocked test.

**Tech Stack:** Python 3.12, FastMCP, httpx, pydantic-settings, pytest (async, respx), Ruff, mypy. `uv` for deps. `make ci-local` is the gate.

**Conventions for every task:** modern typing (`str | None`), ASCII, keep each file < 600 LOC (`make lint-loc`), tests under `tests/unit/`. Run the named test after each implementation step.

---

### Task 1: Config + telemetry foundation (`served_warm`, thresholds)

**Files:**
- Modify: `spliceailookup_link/config.py`
- Modify: `spliceailookup_link/services/telemetry.py`
- Test: `tests/unit/test_telemetry.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_telemetry.py`:

```python
from spliceailookup_link.services.telemetry import CallTelemetry, is_served_warm


def test_is_served_warm_cache_hit() -> None:
    assert is_served_warm("hit", None, 5000) is True


def test_is_served_warm_fast_miss() -> None:
    assert is_served_warm("miss", 800, 5000) is True


def test_is_served_warm_cold_miss() -> None:
    assert is_served_warm("miss", 20000, 5000) is False


def test_is_served_warm_unknown_miss() -> None:
    # No upstream timing recorded and not a hit -> conservatively not warm.
    assert is_served_warm("partial", None, 5000) is False


def test_call_telemetry_served_warm_uses_default_threshold() -> None:
    assert CallTelemetry(cache="hit").served_warm() is True
    assert CallTelemetry(cache="miss", upstream_elapsed_ms=20000).served_warm() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_telemetry.py -q`
Expected: FAIL (`ImportError: cannot import name 'is_served_warm'`).

- [ ] **Step 3: Add the config settings**

In `spliceailookup_link/config.py`, after the `PREDICT_SOFT_DEADLINE_SECONDS` block (around line 65), add:

```python
    # A response is "warm" if it was a cache hit or the upstream answered faster
    # than this (cold Cloud Run starts are ~13s+; warm calls are sub-second).
    # Surfaced as _meta.served_warm so a client can choose blocking vs background.
    WARM_THRESHOLD_MS: int = 5000

    # Validate the coordinate REF against the Ensembl reference base BEFORE the
    # slow scoring dispatch (fast ref_mismatch instead of a ~17s not_found).
    # Disable only if Ensembl sequence lookups are unavailable in an environment.
    PREFLIGHT_REF_CHECK_ENABLED: bool = True
```

- [ ] **Step 4: Add the telemetry helper**

Replace the body of `spliceailookup_link/services/telemetry.py` with:

```python
"""Per-call telemetry for scoring (cache hit/miss + upstream timing + warmth).

Hit/miss is decided at the score() boundary by membership in the service's
set of already-computed keys, checked BEFORE the await. This is concurrency
safe (distinct keys are independent; async_lru runs leaves in their own tasks,
so a ContextVar set inside a leaf would not propagate back) and best-effort
after TTL expiry / LRU eviction, which is acceptable for advisory telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass

from spliceailookup_link.config import settings


def is_served_warm(
    cache: str | None, upstream_elapsed_ms: int | None, threshold_ms: int | None = None
) -> bool:
    """True when the response avoided a cold start: a cache hit, or an upstream
    answer faster than threshold_ms. Unknown timing on a non-hit is not warm."""
    if threshold_ms is None:
        threshold_ms = settings.WARM_THRESHOLD_MS
    if cache == "hit":
        return True
    if upstream_elapsed_ms is not None:
        return upstream_elapsed_ms < threshold_ms
    return False


@dataclass(slots=True)
class CallTelemetry:
    cache: str  # "hit" | "miss"
    upstream_elapsed_ms: int | None = None
    cache_age_s: int | None = None
    cache_ttl_s: int | None = None

    def served_warm(self, threshold_ms: int | None = None) -> bool:
        return is_served_warm(self.cache, self.upstream_elapsed_ms, threshold_ms)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_telemetry.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/config.py spliceailookup_link/services/telemetry.py tests/unit/test_telemetry.py
git commit -m "feat(config): WARM_THRESHOLD_MS + PREFLIGHT_REF_CHECK_ENABLED; telemetry served_warm helper"
```

---

### Task 2: D1+D2 — pre-flight reference-base check + correct build/ref split

**Files:**
- Modify: `spliceailookup_link/mcp/errors.py` (`RefMismatchError`, `mcp_tool_error`)
- Modify: `spliceailookup_link/mcp/tools/_diagnose.py` (refactor; new `preflight_ref_mismatch`)
- Modify: `spliceailookup_link/mcp/tools/_common.py` (`prepare_variant`)
- Modify: `spliceailookup_link/mcp/tools/spliceai.py`, `pangolin.py`, `_predict.py` (pass `cross_build_check`)
- Test: `tests/unit/test_diagnose.py`, `tests/unit/test_eval_v07.py` (new)

- [ ] **Step 1: Write failing tests for the new diagnose behavior**

Replace `test_build_mismatch_when_ref_matches_other_build` in `tests/unit/test_diagnose.py` (the old test asserts the buggy behavior D1 removes) and add a hint test:

```python
async def test_ref_mismatch_with_secondary_hint_when_ref_matches_other_build() -> None:
    # D1: a wrong REF that coincidentally matches the OTHER build's base is a
    # ref_mismatch (the requested-build coordinate is valid), NOT a build_mismatch.
    svc = StubService()
    svc.ref_bases = {"GRCh38": "T", "GRCh37": "C"}  # REF 'C' matches GRCh37 only
    with pytest.raises(RefMismatchError) as ei:
        await _run(svc, "8-140300616-C-A", build="GRCh38")
    assert ei.value.reference_base == "T"
    assert ei.value.other_build_hint is not None
    assert ei.value.other_build_hint["build"] == "GRCh37"
    assert svc.score_calls == []  # no slow scoring probe
```

- [ ] **Step 2: Write failing facade tests (pre-flight, before scoring)**

Create `tests/unit/test_eval_v07.py`:

```python
"""Regression tests for the v0.7.0 assessment defects (D1-D5, C3-C5)."""

from __future__ import annotations

from tests.conftest import StubService, structured


# --- D1 + D2: pre-flight reference-base check ---------------------------------

async def test_preflight_ref_mismatch_skips_scoring(mcp, stub_service: StubService) -> None:
    # D2: a wrong REF is rejected as ref_mismatch BEFORE any scoring call.
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "C"}
    res = await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-A-G"})
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "ref_mismatch"
    assert stub_service.score_calls == []  # never dispatched to the scoring backend


async def test_preflight_ref_typo_matching_other_build_is_ref_mismatch(
    mcp, stub_service: StubService
) -> None:
    # D1: the exact assessment case chr8-140300616-C-A. REF matches GRCh37 base,
    # but it is reported as ref_mismatch (with a secondary hint), NOT build_mismatch.
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "C"}
    res = await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-C-A"})
    data = structured(res)
    assert data["error_code"] == "ref_mismatch"
    assert data["other_build_hint"]["build"] == "GRCh37"
    assert stub_service.score_calls == []


async def test_preflight_proceeds_when_ref_matches(mcp, stub_service: StubService) -> None:
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "T"}  # REF 'T' matches
    res = await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"})
    data = structured(res)
    assert data["success"] is True
    assert stub_service.score_calls  # scoring proceeded


async def test_preflight_proceeds_when_ensembl_unavailable(
    mcp, stub_service: StubService
) -> None:
    stub_service.ref_bases = {}  # reference_base returns None -> inconclusive
    res = await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-A-G"})
    data = structured(res)
    assert data["success"] is True  # never regress; scoring proceeds
    assert stub_service.score_calls
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_diagnose.py tests/unit/test_eval_v07.py -q`
Expected: FAIL (`RefMismatchError.other_build_hint` attribute missing; pre-flight not implemented so `score_calls` is non-empty / build_mismatch).

- [ ] **Step 4: Add `other_build_hint` to `RefMismatchError`**

In `spliceailookup_link/mcp/errors.py`, replace the `RefMismatchError.__init__` (lines ~82-99) signature/body to accept and store the hint:

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
        other_build_hint: dict[str, str] | None = None,
    ):
        self.variant_id = variant_id
        self.observed_ref = observed_ref
        self.reference_base = reference_base
        self.build = build
        self.other_build_hint = other_build_hint
        super().__init__(
            f"REF allele '{observed_ref}' does not match the {build} reference base "
            f"'{reference_base}' at {chrom}:{pos}."
        )
```

- [ ] **Step 5: Surface the hint in the error envelope**

In `mcp_tool_error` (errors.py), after the `AmbiguousVariantError` special-case block (after line ~378) add:

```python
    if isinstance(exc, RefMismatchError) and exc.other_build_hint:
        payload["other_build_hint"] = exc.other_build_hint
        payload["recovery"] = f"{payload['recovery']} {exc.other_build_hint['note']}"
```

- [ ] **Step 6: Refactor `_diagnose.py` (delete the ref-base→build_mismatch branch; add pre-flight)**

Replace `spliceailookup_link/mcp/tools/_diagnose.py` with:

```python
"""Distinguish wrong-REF from wrong-build on a coordinate prediction.

A cached Ensembl reference-base lookup classifies a coordinate failure:
- preflight_ref_mismatch runs BEFORE scoring (in prepare_variant): a REF that
  does not match the requested-build reference is a fast ref_mismatch, never a
  ~17s not_found, and never a misleading build_mismatch.
- diagnose_coordinate_failure runs on the post-scoring not_found path as a
  safety net (e.g. when Ensembl was unavailable at preflight time). It only
  asserts build_mismatch via the scoring cross_build_probe, which confirms the
  variant actually scores on the other build, so the redirect is productive.
"""

from __future__ import annotations

from spliceailookup_link.config import GenomeBuild
from spliceailookup_link.mcp.errors import BuildMismatchError, RefMismatchError
from spliceailookup_link.mcp.tools._common import cross_build_probe
from spliceailookup_link.services import SpliceService
from spliceailookup_link.variant import VariantParseError, split_variant_id

_ACGT = set("ACGT")


def _is_simple_ref(ref: str) -> bool:
    return bool(ref) and all(b in _ACGT for b in ref.upper())


async def _build_ref_mismatch(
    service: SpliceService,
    *,
    variant_id: str,
    chrom: str,
    pos: int,
    ref: str,
    requested_base: str,
    requested_build: GenomeBuild,
) -> RefMismatchError:
    """Construct a RefMismatchError, enriched with a secondary other-build hint
    when the typed REF happens to match the other build's base (D1)."""
    other: GenomeBuild = "GRCh37" if requested_build == "GRCh38" else "GRCh38"
    other_base = await service.reference_base(chrom, pos, len(ref), other)
    hint: dict[str, str] | None = None
    if other_base == ref.upper():
        hint = {
            "build": other,
            "note": (
                f"REF '{ref.upper()}' matches the {other} reference base at "
                f"{chrom}:{pos}; if you intended {other}, re-run with "
                f"genome_build={other}, or call resolve_variant for canonical "
                "CHROM-POS-REF-ALT."
            ),
        }
    return RefMismatchError(
        variant_id=variant_id,
        observed_ref=ref.upper(),
        reference_base=requested_base,
        build=requested_build,
        chrom=chrom,
        pos=pos,
        other_build_hint=hint,
    )


async def preflight_ref_mismatch(
    service: SpliceService, *, variant_id: str, requested_build: GenomeBuild
) -> None:
    """Raise RefMismatchError when the coordinate's REF does not match the
    requested-build reference base. No-op (return) when inconclusive (Ensembl
    unavailable), when the REF matches, or for non-ACGT/symbolic REFs."""
    try:
        chrom, pos, ref, _alt = split_variant_id(variant_id)
    except VariantParseError:
        return
    if not _is_simple_ref(ref):
        return
    requested_base = await service.reference_base(chrom, pos, len(ref), requested_build)
    if requested_base is None or requested_base == ref.upper():
        return
    raise await _build_ref_mismatch(
        service,
        variant_id=variant_id,
        chrom=chrom,
        pos=pos,
        ref=ref,
        requested_base=requested_base,
        requested_build=requested_build,
    )


async def diagnose_coordinate_failure(
    service: SpliceService,
    *,
    variant_id: str,
    requested_build: GenomeBuild,
    distance: int,
    mask: int,
    gene_set: str,
) -> None:
    """Post-scoring not_found safety net. Returning without raising means a
    genuine not_found (well-formed variant, no overlapping transcript)."""
    try:
        chrom, pos, ref, _alt = split_variant_id(variant_id)
    except VariantParseError:
        return
    if not _is_simple_ref(ref):
        return
    requested_base = await service.reference_base(chrom, pos, len(ref), requested_build)
    if requested_base is None:
        # Ensembl inconclusive: fall back to a scoring probe of the other build,
        # which raises BuildMismatchError only if the variant actually scores there.
        await _probe_fallback(service, variant_id, requested_build, distance, mask, gene_set)
        return
    if requested_base == ref.upper():
        return  # REF matches the requested-build reference: genuine not_found.
    # Position is in-range (prepare_variant already ruled out build_mismatch) and
    # the REF is wrong -> ref_mismatch (with optional hint). Never build_mismatch.
    raise await _build_ref_mismatch(
        service,
        variant_id=variant_id,
        chrom=chrom,
        pos=pos,
        ref=ref,
        requested_base=requested_base,
        requested_build=requested_build,
    )


async def _probe_fallback(
    service: SpliceService,
    variant_id: str,
    requested_build: GenomeBuild,
    distance: int,
    mask: int,
    gene_set: str,
) -> None:
    other = await cross_build_probe(
        service,
        model="spliceai",
        requested_build=requested_build,
        variant_id=variant_id,
        distance=distance,
        mask=mask,
        gene_set=gene_set,
    )
    if other:
        raise BuildMismatchError(
            variant_id=variant_id,
            inferred_build=other,
            requested_build=requested_build,
        )
```

- [ ] **Step 7: Wire the pre-flight into `prepare_variant`**

In `spliceailookup_link/mcp/tools/_common.py`, change the `prepare_variant` signature and the coordinate branch:

```python
async def prepare_variant(
    service: SpliceService,
    raw_variant: str,
    genome_build: GenomeBuild,
    *,
    cross_build_check: bool = True,
) -> PreparedVariant:
    """Normalize any input to a CHROM-POS-REF-ALT id, resolving HGVS/rsID via VEP.

    Raises VariantParseError (-> invalid_input) for uninterpretable input,
    BuildMismatchError (-> build_mismatch) when a coordinate's position cannot
    belong to the requested build, and RefMismatchError (-> ref_mismatch) when a
    coordinate's REF does not match the requested-build reference base -- both
    before any slow scoring call. The pre-flight ref check is gated by
    cross_build_check and settings.PREFLIGHT_REF_CHECK_ENABLED.
    """
    parsed = parse_variant_input(raw_variant)
    if parsed.kind == "coordinate":
        _reject_unsupported_contig(parsed.value)
        inferred = detect_build_mismatch(parsed.value, genome_build)
        if inferred is not None:
            raise BuildMismatchError(
                variant_id=parsed.value,
                inferred_build=inferred,
                requested_build=genome_build,
            )
        if cross_build_check and settings.PREFLIGHT_REF_CHECK_ENABLED:
            # Local import avoids a module cycle (_diagnose imports _common).
            from spliceailookup_link.mcp.tools._diagnose import preflight_ref_mismatch

            await preflight_ref_mismatch(
                service, variant_id=parsed.value, requested_build=genome_build
            )
        return PreparedVariant(
            variant_id=parsed.value,
            genome_build=genome_build,
            consequence=None,
            resolution=None,
        )
    resolution = await service.resolve(raw_variant, genome_build)
    if resolution.get("ambiguous"):
        raise AmbiguousVariantError(
            variant=raw_variant,
            candidates=resolution.get("variant_ids") or [resolution["variant_id"]],
            note=resolution.get("note"),
        )
    _reject_unsupported_contig(resolution["variant_id"])
    return PreparedVariant(
        variant_id=resolution["variant_id"],
        genome_build=genome_build,
        consequence=resolution.get("consequence"),
        resolution=resolution,
    )
```

- [ ] **Step 8: Pass `cross_build_check` from the three call sites**

In `spliceailookup_link/mcp/tools/spliceai.py` (line ~91) and `pangolin.py` (line ~87):

```python
            prepared = await prepare_variant(
                service, variant, genome_build, cross_build_check=cross_build_check
            )
```

In `spliceailookup_link/mcp/tools/_predict.py` (line ~97):

```python
    prepared = await prepare_variant(
        service, variant, genome_build, cross_build_check=cross_build_check
    )
```

- [ ] **Step 9: Run the tests**

Run: `uv run pytest tests/unit/test_diagnose.py tests/unit/test_eval_v07.py tests/unit/test_tools.py -q`
Expected: PASS (and existing tool tests still green).

- [ ] **Step 10: Commit**

```bash
git add spliceailookup_link/mcp/errors.py spliceailookup_link/mcp/tools/_diagnose.py \
  spliceailookup_link/mcp/tools/_common.py spliceailookup_link/mcp/tools/spliceai.py \
  spliceailookup_link/mcp/tools/pangolin.py spliceailookup_link/mcp/tools/_predict.py \
  tests/unit/test_diagnose.py tests/unit/test_eval_v07.py
git commit -m "fix(D1,D2): pre-flight ref-base check; ref_mismatch never misclassified as build_mismatch"
```

---

### Task 3: D3 — ambiguous `resolve_variant` returns `variant_id=null`

**Files:**
- Modify: `spliceailookup_link/mcp/tools/resolve.py`
- Test: `tests/unit/test_eval_v07.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_eval_v07.py`:

```python
# --- D3: ambiguous resolve consistency ---------------------------------------

async def test_resolve_ambiguous_nulls_singular_id(mcp) -> None:
    res = await mcp.call_tool("resolve_variant", {"variant": "rs6025"})
    data = structured(res)
    assert data["ambiguous"] is True
    assert data["variant_id"] is None  # cannot silently pick one allele
    assert data["variant_ids"] == ["1-169549811-C-A", "1-169549811-C-T"]
    # The per-allele next_commands still guide the choice.
    tools = [c["tool"] for c in data["_meta"]["next_commands"]]
    assert tools and all(t == "predict_splicing" for t in tools)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_eval_v07.py::test_resolve_ambiguous_nulls_singular_id -q`
Expected: FAIL (`variant_id` is `"1-169549811-C-A"`, not `None`).

- [ ] **Step 3: Null the singular id when ambiguous**

In `spliceailookup_link/mcp/tools/resolve.py`, inside `call()` after `result = await service.resolve(...)` and before the `ids = ...` line (around line 77), add:

```python
            if result.get("ambiguous"):
                # D3: force the caller to pick from variant_ids[] rather than
                # silently inheriting the first allele.
                result["variant_id"] = None
```

And update the output schema so a null id validates: change the `variant_id` property in `_OUTPUT_SCHEMA` (line ~23) from `{"type": "string"}` to `{"type": ["string", "null"]}` (keep it in `required`).

Note: the existing `ids = result.get("variant_ids") or [result["variant_id"]]` line stays correct — ambiguous results always carry `variant_ids`, so the `None` singular id is never dereferenced.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_eval_v07.py tests/unit/test_tools.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/tools/resolve.py tests/unit/test_eval_v07.py
git commit -m "fix(D3): resolve_variant returns variant_id=null when ambiguous"
```

---

### Task 4: D4 + C4 — lean `_meta` trim + `served_warm`

**Files:**
- Modify: `spliceailookup_link/mcp/errors.py` (`run_mcp_tool` lean gating)
- Modify: `spliceailookup_link/mcp/tools/spliceai.py`, `pangolin.py`, `combined.py`
- Test: `tests/unit/test_eval_v07.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_eval_v07.py`:

```python
# --- D4 + C4: lean _meta and served_warm -------------------------------------

async def test_meta_full_provenance_in_compact(mcp) -> None:
    res = await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"})
    meta = structured(res)["_meta"]
    assert "capabilities_version" in meta
    assert meta["unsafe_for_clinical_use"] is True
    assert "served_warm" in meta


async def test_meta_trimmed_when_hints_off(mcp) -> None:
    res = await mcp.call_tool(
        "predict_spliceai", {"variant": "8-140300616-T-G", "include_hints": False}
    )
    meta = structured(res)["_meta"]
    # Bulky/redundant provenance dropped on the lean path...
    assert "capabilities_version" not in meta
    assert "cache_ttl_s" not in meta
    assert "cache_age_s" not in meta
    assert "next_commands" not in meta
    # ...but request_id, timing, cache, served_warm, and the safety flag stay.
    assert "request_id" in meta
    assert "elapsed_ms" in meta["timing"]
    assert "cache" in meta
    assert "served_warm" in meta
    assert meta["unsafe_for_clinical_use"] is True


async def test_meta_trimmed_in_minimal_mode(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing", {"variant": "8-140300616-T-G", "response_mode": "minimal"}
    )
    meta = structured(res)["_meta"]
    assert "capabilities_version" not in meta
    assert "served_warm" in meta


async def test_served_warm_true_on_cache_hit(mcp, stub_service: StubService) -> None:
    await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"})  # warms cache
    res = await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-T-G"})
    assert structured(res)["_meta"]["served_warm"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_eval_v07.py -k "meta or served_warm" -q`
Expected: FAIL (`served_warm` missing; `capabilities_version` still present on lean path).

- [ ] **Step 3: Add lean gating to `run_mcp_tool`**

In `spliceailookup_link/mcp/errors.py`, change `run_mcp_tool` to accept `lean_meta` and gate provenance in `_stamp`:

```python
async def run_mcp_tool(
    tool_name: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    context: McpErrorContext | None = None,
    lean_meta: bool = False,
) -> dict[str, Any]:
    """Execute an MCP tool body, converting any exception to an envelope dict.

    lean_meta=True (response_mode='minimal' or include_hints=False) drops the
    repetitive capabilities_version from _meta to save tokens on high-volume
    calls; the research-use disclaimer (unsafe_for_clinical_use) is always kept.
    """
    ctx = context or McpErrorContext(tool_name=tool_name)
    request_id = uuid.uuid4().hex[:12]
    start = time.perf_counter()

    def _stamp(envelope: dict[str, Any]) -> dict[str, Any]:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        existing: dict[str, Any] = envelope.get("_meta") or {}
        meta: dict[str, Any] = {
            "request_id": request_id,
            "timing": {"elapsed_ms": elapsed_ms},
            **existing,
            **_BASE_META,  # unsafe_for_clinical_use -- always present
        }
        if not lean_meta:
            meta["capabilities_version"] = get_capabilities_version()
        envelope["_meta"] = meta
        return envelope
```

(The rest of `run_mcp_tool` — the try/except and `_stamp` call sites — is unchanged.)

- [ ] **Step 4: Update `predict_spliceai` `_meta` assembly**

In `spliceailookup_link/mcp/tools/spliceai.py`: add the import near the top with the other `_common` imports:

```python
from spliceailookup_link.config import settings
from spliceailookup_link.services.telemetry import is_served_warm
```

Compute `lean` at the top of the tool function body (right after the docstring, before `async def call`):

```python
        lean = response_mode == "minimal" or not include_hints
```

Replace the `_meta` assembly block (lines ~128-145) with:

```python
            meta: dict[str, Any] = {
                "cache": tele.cache,
                "served_warm": is_served_warm(
                    tele.cache, tele.upstream_elapsed_ms, settings.WARM_THRESHOLD_MS
                ),
            }
            if include_hints:
                meta["next_commands"] = [
                    cmd("predict_pangolin", variant=prepared.variant_id, genome_build=genome_build)
                ]
                if response_mode != "minimal":
                    meta["see_also"] = see_also_for(
                        prepared.variant_id, genome_build, gene, response_mode
                    )
            if not lean:
                if tele.upstream_elapsed_ms is not None:
                    meta["upstream_elapsed_ms"] = tele.upstream_elapsed_ms
                if tele.cache_ttl_s is not None:
                    meta["cache_ttl_s"] = tele.cache_ttl_s
                if tele.cache_age_s is not None:
                    meta["cache_age_s"] = tele.cache_age_s
                if prepared.resolution is not None:
                    meta["resolved_from"] = prepared.resolution.get("raw_input")
            shaped["_meta"] = meta
            return shaped
```

Pass `lean_meta=lean` to `run_mcp_tool` at the bottom:

```python
        return await run_mcp_tool(
            "predict_spliceai",
            call,
            context=McpErrorContext(
                tool_name="predict_spliceai", variant=variant, genome_build=genome_build
            ),
            lean_meta=lean,
        )
```

- [ ] **Step 5: Apply the same pattern to `predict_pangolin`**

In `spliceailookup_link/mcp/tools/pangolin.py`: add the same two imports, compute `lean = response_mode == "minimal" or not include_hints`, replace the `_meta` block (lines ~119-136) with the analogous version (the only difference is `cmd("predict_spliceai", ...)` as the single next_command), and pass `lean_meta=lean` to `run_mcp_tool`.

```python
            meta: dict[str, Any] = {
                "cache": tele.cache,
                "served_warm": is_served_warm(
                    tele.cache, tele.upstream_elapsed_ms, settings.WARM_THRESHOLD_MS
                ),
            }
            if include_hints:
                meta["next_commands"] = [
                    cmd("predict_spliceai", variant=prepared.variant_id, genome_build=genome_build)
                ]
                if response_mode != "minimal":
                    meta["see_also"] = see_also_for(
                        prepared.variant_id, genome_build, gene, response_mode
                    )
            if not lean:
                if tele.upstream_elapsed_ms is not None:
                    meta["upstream_elapsed_ms"] = tele.upstream_elapsed_ms
                if tele.cache_ttl_s is not None:
                    meta["cache_ttl_s"] = tele.cache_ttl_s
                if tele.cache_age_s is not None:
                    meta["cache_age_s"] = tele.cache_age_s
                if prepared.resolution is not None:
                    meta["resolved_from"] = prepared.resolution.get("raw_input")
            shaped["_meta"] = meta
            return shaped
```

- [ ] **Step 6: Apply the pattern to `predict_splicing` (combined)**

In `spliceailookup_link/mcp/tools/combined.py`: add imports:

```python
from spliceailookup_link.config import settings
from spliceailookup_link.services.telemetry import is_served_warm
```

Compute `lean = response_mode == "minimal" or not include_hints` at the top of the function body. Replace the `_meta` assembly (lines ~91-111) with:

```python
            tel = result.pop("_telemetry")
            meta: dict[str, Any] = {}
            if include_hints:
                meta["next_commands"] = for_combined(result["variant_id"], genome_build)
                if response_mode != "minimal":
                    meta["see_also"] = see_also_for(
                        result["variant_id"], genome_build, tel["gene"], response_mode
                    )
            if tel["cache"]:
                meta["cache"] = tel["cache"]
            meta["served_warm"] = is_served_warm(
                tel["cache"], tel["upstream_elapsed_ms"], settings.WARM_THRESHOLD_MS
            )
            if not lean:
                if tel["upstream_elapsed_ms"] is not None:
                    meta["upstream_elapsed_ms"] = tel["upstream_elapsed_ms"]
                if tel.get("cache_ttl_s") is not None:
                    meta["cache_ttl_s"] = tel["cache_ttl_s"]
                if tel.get("cache_age_s") is not None:
                    meta["cache_age_s"] = tel["cache_age_s"]
                if tel["resolution"] is not None:
                    meta["resolved_from"] = tel["resolution"].get("raw_input")
                    meta["resolved_consequence"] = tel["resolved_consequence"]
            if tel["partial"]:
                meta["partial"] = tel["partial"]
            result["_meta"] = meta
            return result
```

Pass `lean_meta=lean` to `run_mcp_tool` at the bottom of `predict_splicing`.

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/unit/test_eval_v07.py tests/unit/test_tools.py -q`
Expected: PASS. If a pre-existing test asserted `capabilities_version`/`cache_ttl_s` presence under `include_hints=false` or minimal mode, update it to match the new lean contract.

- [ ] **Step 8: Commit**

```bash
git add spliceailookup_link/mcp/errors.py spliceailookup_link/mcp/tools/spliceai.py \
  spliceailookup_link/mcp/tools/pangolin.py spliceailookup_link/mcp/tools/combined.py \
  tests/unit/test_eval_v07.py
git commit -m "feat(D4,C4): lean _meta on minimal/hints-off paths + served_warm signal"
```

---

### Task 5: D5 — derive `tx_start`/`tx_end` from the exon model

**Files:**
- Modify: `spliceailookup_link/mcp/shaping.py`
- Test: `tests/unit/test_eval_v07.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_eval_v07.py`:

```python
# --- D5: tx_start / tx_end ----------------------------------------------------

from spliceailookup_link.mcp.shaping import shape_spliceai  # noqa: E402
from tests.fixtures.api_responses import SPLICEAI_TRAPPC9, SPLICEAI_MASKED_EMPTY_ABERR  # noqa: E402


def test_exon_model_carries_tx_bounds() -> None:
    shaped = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="full")
    exon = shaped["transcripts"][0]["exon_model"]
    assert exon["tx_start"] == 139727725  # min(EXON_STARTS)
    assert exon["tx_end"] == 140300614    # max(EXON_ENDS)


def test_transcript_info_tx_bounds_filled_when_null() -> None:
    # SAI-10k transcript_info carries strand/exon_count but null tx bounds; fill
    # them from the exon arrays in the scored transcript.
    shaped = shape_spliceai(SPLICEAI_MASKED_EMPTY_ABERR, response_mode="full")
    ti = shaped["consequence"]["transcript_info"]
    assert ti["tx_start"] == 139727725
    assert ti["tx_end"] == 140300614
    assert ti["strand"] == "-"  # upstream fields preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_eval_v07.py -k "tx_" -q`
Expected: FAIL (`exon_model` has no `tx_start`; `transcript_info` keeps null/absent tx bounds).

- [ ] **Step 3: Add tx bounds to the exon model**

In `spliceailookup_link/mcp/shaping.py`, replace the `out["exon_model"] = {...}` block in `_shape_spliceai_transcript` (lines ~218-223) with:

```python
        starts = raw.get("EXON_STARTS")
        ends = raw.get("EXON_ENDS")
        out["exon_model"] = {
            "tx_start": min(starts) if starts else None,
            "tx_end": max(ends) if ends else None,
            "exon_starts": starts,
            "exon_ends": ends,
            "cds_start": raw.get("CDS_START"),
            "cds_end": raw.get("CDS_END"),
        }
```

- [ ] **Step 4: Add the tx-bounds helper and fill `transcript_info`**

Add this helper above `_shape_consequence` in `shaping.py`:

```python
def _tx_bounds(scores: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    """Genomic transcript bounds from the MANE/top scored transcript's exons."""
    if not scores:
        return None, None
    top = next((s for s in scores if str(s.get("t_priority")) in ("MS", "MP")), scores[0])
    starts = top.get("EXON_STARTS")
    ends = top.get("EXON_ENDS")
    return (min(starts) if starts else None, max(ends) if ends else None)
```

In `_shape_consequence`, replace the `transcript_info` passthrough (lines ~256-257) with:

```python
        if sai.get("transcript_info") is not None:
            ti = dict(sai["transcript_info"])
            if ti.get("tx_start") is None or ti.get("tx_end") is None:
                tx_start, tx_end = _tx_bounds(payload.get("scores") or [])
                if ti.get("tx_start") is None and tx_start is not None:
                    ti["tx_start"] = tx_start
                if ti.get("tx_end") is None and tx_end is not None:
                    ti["tx_end"] = tx_end
            out["transcript_info"] = ti
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_eval_v07.py tests/unit/test_shaping.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/shaping.py tests/unit/test_eval_v07.py
git commit -m "fix(D5): derive tx_start/tx_end from exon model in full mode"
```

---

### Task 6: C3 + C5 — batch size contract + lean resource URIs

**Files:**
- Modify: `spliceailookup_link/mcp/tools/batch.py`, `_batch_runner.py`
- Modify: `spliceailookup_link/mcp/resources.py`
- Test: `tests/unit/test_eval_v07.py`, `tests/unit/test_batch.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_eval_v07.py`:

```python
# --- C3: batch size contract --------------------------------------------------

async def test_batch_envelope_self_describes_size_contract(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing_batch", {"variants": ["8-140300616-T-G", "8-140300616-T-G"]}
    )
    meta = structured(res)["_meta"]
    assert meta["items_submitted"] == 2
    assert meta["max_items"] == 25


async def test_batch_rejects_over_cap(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing_batch", {"variants": ["8-140300616-T-G"] * 26}
    )
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "validation_failed"


async def test_batch_item_meta_has_served_warm(mcp) -> None:
    res = await mcp.call_tool(
        "predict_splicing_batch", {"variants": ["8-140300616-T-G"]}
    )
    item = structured(res)["results"][0]
    assert "served_warm" in item["_meta"]


# --- C5: resources in lean capabilities --------------------------------------

async def test_lean_capabilities_lists_resources(mcp) -> None:
    res = await mcp.call_tool("get_server_capabilities", {"detail": "lean"})
    data = structured(res)
    assert "resources" in data
    assert "spliceailookup://reference" in data["resources"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_eval_v07.py -k "batch or lean_capabilities" -q`
Expected: FAIL (`items_submitted`/`max_items` absent; item `_meta` has no `served_warm`; lean doc has no `resources`).

- [ ] **Step 3: Self-describe the batch size contract**

In `spliceailookup_link/mcp/tools/_batch_runner.py`:

Add `served_warm` to `_success_item` — replace its body (lines ~50-59) with:

```python
def _success_item(one: dict[str, Any], variant: str) -> dict[str, Any]:
    from spliceailookup_link.services.telemetry import is_served_warm

    tele = one.pop("_telemetry")
    one["variant"] = variant
    item_meta: dict[str, Any] = {
        "cache": tele.get("cache"),
        "served_warm": is_served_warm(tele.get("cache"), tele.get("upstream_elapsed_ms")),
    }
    if tele.get("upstream_elapsed_ms") is not None:
        item_meta["upstream_elapsed_ms"] = tele["upstream_elapsed_ms"]
    if tele.get("cache_age_s") is not None:
        item_meta["cache_age_s"] = tele["cache_age_s"]
    one["_meta"] = item_meta
    return one
```

Add a `max_items` parameter to `run_batch` (default 25) and surface it in the envelope meta. Change the signature (line ~116):

```python
async def run_batch(
    service: SpliceService,
    *,
    variants: list[str],
    genome_build: str,
    params: dict[str, Any],
    ctx: Any = None,
    predict_fn: PredictFn = predict_one,
    retry_backoff_s: float | None = None,
    max_items: int = 25,
) -> dict[str, Any]:
```

In the `meta` dict built near the end (after line ~174), add the contract fields:

```python
    meta: dict[str, Any] = {"items_submitted": total, "max_items": max_items}
    if top is not None:
        meta["next_commands"] = [
```

- [ ] **Step 4: Pass `max_items` from the batch tool and update its docstring**

In `spliceailookup_link/mcp/tools/batch.py`, pass `max_items=_MAX_BATCH` to `run_batch`:

```python
            return await run_batch(
                service,
                variants=variants,
                genome_build=genome_build,
                params={ ... },  # unchanged
                ctx=ctx,
                max_items=_MAX_BATCH,
            )
```

Update the docstring sentence "Returns up to ~25x a single compact result." to:

```
Accepts 1-25 variants (more than max_items=25 returns validation_failed, not a truncated result); each item returns about one compact predict_splicing result, so a full batch is ~25x a single compact response.
```

- [ ] **Step 5: Add `resources` to the lean capabilities doc (C5)**

In `spliceailookup_link/mcp/resources.py`, inside `_lean_capabilities`, add the resources line before `capabilities_version`:

```python
        "error_codes": full["error_codes"],
        "resources": full["resources"],
        "params_by_reference": (
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/unit/test_eval_v07.py tests/unit/test_batch.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add spliceailookup_link/mcp/tools/batch.py spliceailookup_link/mcp/tools/_batch_runner.py \
  spliceailookup_link/mcp/resources.py tests/unit/test_eval_v07.py
git commit -m "feat(C3,C5): batch size contract + per-item served_warm; lean capabilities lists resources"
```

---

### Task 7: Rec #5 — deterministic coverage for comprehensive-503 and rate_limited

**Files:**
- Test: `tests/unit/test_eval_v07.py`

- [ ] **Step 1: Write the tests**

Append to `tests/unit/test_eval_v07.py`:

```python
# --- Rec #5: error-mapping coverage (deterministic, no live calls) -----------

from spliceailookup_link.api import RateLimitedError, SpliceApiError  # noqa: E402


async def test_comprehensive_503_maps_to_upstream_unavailable(
    mcp, stub_service: StubService
) -> None:
    # A 5xx during a comprehensive gene_set call surfaces as retryable upstream_unavailable.
    stub_service.score_error = SpliceApiError("Upstream HTTP 503")
    res = await mcp.call_tool(
        "predict_splicing",
        {"variant": "8-140300616-T-G", "gene_set": "comprehensive"},
    )
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "upstream_unavailable"
    assert data["retryable"] is True


async def test_rate_limited_reports_rate_budget(mcp, stub_service: StubService) -> None:
    stub_service.score_error = RateLimitedError("Local concurrency limit saturated")
    res = await mcp.call_tool("predict_splicing", {"variant": "8-140300616-T-G"})
    data = structured(res)
    assert data["error_code"] == "rate_limited"
    budget = data["_meta"]["rate_budget"]
    assert budget["unit"] == "concurrent_requests"
    assert budget["remaining"] == 0
    assert "limit" in budget


async def test_batch_per_item_rate_budget(mcp, stub_service: StubService) -> None:
    stub_service.score_error = RateLimitedError("saturated")
    res = await mcp.call_tool(
        "predict_splicing_batch",
        {"variants": ["8-140300616-T-G"]},
    )
    item = structured(res)["results"][0]
    assert item["error_code"] == "rate_limited"
    assert item["rate_budget"]["unit"] == "concurrent_requests"
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/unit/test_eval_v07.py -k "503 or rate" -q`
Expected: PASS (these assert already-implemented mappings; if any fails it is a real find — fix the mapping, do not weaken the test).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_eval_v07.py
git commit -m "test(Rec5): cover comprehensive-503 -> upstream_unavailable and rate_limited budget shape"
```

---

### Task 8: Documentation — capabilities, reference, API.md

**Files:**
- Modify: `spliceailookup_link/mcp/resources.py` (`observability`, `batch_semantics`, error `when` clauses)
- Modify: `docs/API.md`
- Test: `tests/unit/test_eval_v07.py` (assert documented fields)

- [ ] **Step 1: Write the failing doc-contract test**

Append to `tests/unit/test_eval_v07.py`:

```python
# --- Documentation contract ---------------------------------------------------

def test_capabilities_documents_served_warm_and_batch_cap() -> None:
    from spliceailookup_link.mcp.resources import get_capabilities_resource

    doc = get_capabilities_resource("full")
    assert "served_warm" in doc["response_fields"]["observability"]
    assert "max_items" in doc["batch_semantics"] or "25" in doc["batch_semantics"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_eval_v07.py -k documents -q`
Expected: FAIL.

- [ ] **Step 3: Update the capabilities document**

In `spliceailookup_link/mcp/resources.py`:

Extend the `observability` response field (line ~179-182) to:

```python
            "observability": (
                "every _meta carries request_id, timing.elapsed_ms, and served_warm "
                "(true on a cache hit or a sub-cold-start upstream answer -- use it to "
                "choose blocking vs a background task); prediction payloads add cache "
                "('hit'|'miss'|'partial') and upstream_elapsed_ms (on a miss). On the "
                "lean path (response_mode='minimal' or include_hints=false) the "
                "repetitive capabilities_version and cache_ttl_s/cache_age_s are dropped "
                "to save tokens (fetch capabilities_version from get_server_capabilities)."
            ),
```

Extend `batch_semantics` (append to the existing string, line ~214-222):

```python
            " predict_splicing_batch accepts max_items=25 variants; submitting more "
            "returns validation_failed (the cap is enforced, not silently truncated). "
            "Each item returns about one compact predict_splicing result, and the "
            "envelope _meta echoes items_submitted and max_items."
```

Refine the `build_mismatch` error `when` clause in `get_reference_resource` (line ~361-364) to make the ref/build split explicit:

```python
                "build_mismatch": {
                    "retryable": False,
                    "when": "the coordinate cannot belong to the requested build -- its "
                    "position is out of range, or the variant only scores on the other "
                    "build. A wrong REF at an in-range position is ref_mismatch, not this.",
                },
```

Refine the `ref_mismatch` `when` clause (line ~351-355) to mention the secondary hint:

```python
                "ref_mismatch": {
                    "retryable": False,
                    "when": "the coordinate REF does not match the genome reference at that "
                    "position/build (swapped REF/ALT, wrong strand, or a typo). Detected "
                    "pre-flight via an Ensembl reference-base check. If the REF matches the "
                    "other build, other_build_hint carries a secondary suggestion.",
                },
```

- [ ] **Step 4: Update `docs/API.md`**

Read `docs/API.md`, then add/adjust the `_meta` and error sections to mention: `served_warm`, the lean-path `_meta` trim, the pre-flight `ref_mismatch` (fast, < 1s), `build_mismatch` now position/scorability-based, `resolve_variant` returning `variant_id=null` when ambiguous, `tx_start`/`tx_end` in full mode, and the batch `max_items=25` contract. Keep edits scoped to these facts; match the file's existing style.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_eval_v07.py -k documents -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/resources.py docs/API.md tests/unit/test_eval_v07.py
git commit -m "docs: document served_warm, lean _meta, ref/build split, batch cap, tx bounds"
```

---

### Task 9: Full gate — `make ci-local`

**Files:** none (verification + any cleanup).

- [ ] **Step 1: Run the full local CI**

Run: `make ci-local`
Expected: PASS for format, lint, lint-loc (every touched module < 600 LOC), mypy, and the full unit suite.

- [ ] **Step 2: Fix any failures at the source**

If `lint-loc` flags `errors.py` (was 460) or `resources.py` (was 437): the additions are small, but if either crosses 600, extract a cohesive helper module (e.g. move `_recovery_text`/`_recovery_action` into `mcp/recovery.py`) rather than trimming behavior. If Ruff/format/mypy complain, fix at the source; do not silence with ignores unless a pattern already exists in the file.

- [ ] **Step 3: Final verification of the assessment reproductions**

Run: `uv run pytest tests/unit/test_eval_v07.py tests/unit/test_diagnose.py -q`
Confirm the two headline assertions hold: (a) wrong REF `8-140300616-C-A` → `ref_mismatch` + `other_build_hint`, not `build_mismatch`; (b) wrong REF dispatches **no** scoring call (`score_calls == []`).

- [ ] **Step 4: Commit any cleanup**

```bash
git add -A
git commit -m "chore: ci-local green for v0.7.0 assessment fixes" --allow-empty
```

---

## Self-review (spec coverage)

- **D1** → Task 2 (delete ref-base→build_mismatch; `other_build_hint`; regression test on `C-A`).
- **D2** → Task 2 (pre-flight in `prepare_variant`; `score_calls == []` assertion).
- **D3** → Task 3 (`variant_id=null`; schema nullable).
- **D4** → Task 4 (`run_mcp_tool` lean gating + per-tool trim).
- **C4** → Tasks 1 + 4 + 6 (`served_warm` helper, single/combined/batch).
- **D5** → Task 5 (`exon_model` + `transcript_info` tx bounds).
- **C3** → Task 6 (`items_submitted`/`max_items`, docstring, docs in Task 8).
- **C5** → Task 6 (lean `resources`).
- **Rec #5** → Task 7 (503 + rate_limited coverage).
- **Docs** → Task 8. **Gate** → Task 9.

No placeholders; types/method names (`is_served_warm`, `preflight_ref_mismatch`,
`_build_ref_mismatch`, `_tx_bounds`, `other_build_hint`, `lean_meta`,
`max_items`) are consistent across tasks.
