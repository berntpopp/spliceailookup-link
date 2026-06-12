# Tester-Report Fixes (v0.6.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 8 findings in `docs/mcp-tester-report-2026-06-12.md` to lift the server above 9.5/10, primarily via correctness-adjacent fixes: a cheap Ensembl reference-base diagnostic (`ref_mismatch` + fast build disambiguation), resolver-ambiguity propagation (`ambiguous`), an echoed `capabilities_version`, a server-side soft deadline, a lean capabilities mode, and a `discordant_subthreshold` verdict.

**Architecture:** All changes are additive and stay within the existing hand-authored MCP facade. The one new mechanism is a reference-base lookup on the Ensembl REST `sequence/region` endpoint (same vendor already used for resolution), invoked only on the prediction *failure* path so happy-path latency is unchanged. Two new error codes (`ref_mismatch`, `ambiguous`) and one new agreement verdict (`discordant_subthreshold`) extend documented contracts without renaming any tool.

**Tech Stack:** Python 3.12, FastMCP 3.4.2, httpx, async-lru, pydantic, pytest + respx. Spec: `docs/superpowers/specs/2026-06-12-tester-report-fixes-design.md`.

**Conventions (read once):**
- Run a single test: `uv run pytest tests/unit/test_x.py::test_y -v`
- Full gate before any "done" claim: `make ci-local` (format, lint, lint-loc ≤600, mypy, tests).
- Tests live under `tests/unit/`; live upstream tests are `integration`-marked and out of default CI.
- Async tests need no decorator (the repo enables `asyncio_mode=auto`); write `async def test_...`.
- Hard cap: every `spliceailookup_link/` module < 600 LOC.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `spliceailookup_link/api/ensembl_client.py` | Ensembl REST client | + `reference_base()` (sequence/region) |
| `spliceailookup_link/services/splice_service.py` | caching facade | + cached `reference_base()` wrapper |
| `spliceailookup_link/mcp/tools/_diagnose.py` | **new** | `diagnose_coordinate_failure()` (F1+F8) |
| `spliceailookup_link/mcp/errors.py` | error envelopes | + `RefMismatchError`, `AmbiguousVariantError`, classify/recovery branches, `capabilities_version` provenance, ambiguous next_commands |
| `spliceailookup_link/mcp/tools/_common.py` | variant prep | `prepare_variant` raises `AmbiguousVariantError` |
| `spliceailookup_link/mcp/tools/_predict.py` | combined core | soft deadline (F4) + diagnostic wire-in (F1/F8) |
| `spliceailookup_link/mcp/tools/spliceai.py` / `pangolin.py` | single-model | diagnostic on not_found (F1/F8) |
| `spliceailookup_link/mcp/tools/_predict_shape.py` | agreement/headline | `discordant_subthreshold` (F7) |
| `spliceailookup_link/mcp/tools/batch.py` | batch fan-out | new verdict counter (F7) |
| `spliceailookup_link/mcp/tools/metadata.py` | capabilities/warmup tools | `detail` arg (F6); `warmup(mask=…)` + coverage (F5) |
| `spliceailookup_link/mcp/resources.py` | capabilities doc | cached version accessor, lean mode, new codes/verdict, warmth+deadline docs |
| `spliceailookup_link/config.py` | settings | + `PREDICT_SOFT_DEADLINE_SECONDS` |
| `spliceailookup_link/__init__.py`, `pyproject.toml` | version | → 0.6.0 |
| `tests/conftest.py` | test stub | extend `StubService` for `reference_base`, ambiguity, slow score |

Task order builds the shared F1+F8 mechanism first (bottom-up: client → service → diagnostic → wiring), then the independent findings.

---

## Task 1: Ensembl `reference_base` client method + cached service wrapper

**Files:**
- Modify: `spliceailookup_link/api/ensembl_client.py`
- Modify: `spliceailookup_link/services/splice_service.py`
- Test: `tests/unit/test_service.py`

- [ ] **Step 1: Write the failing test (client, respx-mocked)**

Add to `tests/unit/test_service.py` (match existing respx style in that file; if it has no respx import yet, add `import respx` and `import httpx`):

```python
import httpx
import respx

from spliceailookup_link.api.ensembl_client import EnsemblVepClient


@respx.mock
async def test_reference_base_returns_uppercase_seq() -> None:
    respx.get("https://rest.ensembl.org/sequence/region/human/8:140300616..140300616").mock(
        return_value=httpx.Response(200, json={"seq": "t", "id": "chromosome:GRCh38:8:..."})
    )
    client = EnsemblVepClient()
    base = await client.reference_base("8", 140300616, 1, "GRCh38")
    await client.close()
    assert base == "T"


@respx.mock
async def test_reference_base_uses_grch37_host_for_grch37() -> None:
    route = respx.get(
        "https://grch37.rest.ensembl.org/sequence/region/human/8:140300616..140300616"
    ).mock(return_value=httpx.Response(200, json={"seq": "a"}))
    client = EnsemblVepClient()
    base = await client.reference_base("8", 140300616, 1, "GRCh37")
    await client.close()
    assert base == "A"
    assert route.called


@respx.mock
async def test_reference_base_returns_none_on_upstream_error() -> None:
    respx.get(
        "https://rest.ensembl.org/sequence/region/human/8:999999999..999999999"
    ).mock(return_value=httpx.Response(400, json={"error": "out of range"}))
    client = EnsemblVepClient()
    base = await client.reference_base("8", 999999999, 1, "GRCh38")
    await client.close()
    assert base is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_service.py -k reference_base -v`
Expected: FAIL — `EnsemblVepClient` has no attribute `reference_base`.

- [ ] **Step 3: Implement `reference_base` on the client**

In `spliceailookup_link/api/ensembl_client.py`, update the import to add `SpliceApiError`:

```python
from spliceailookup_link.api.base_client import (
    BaseHTTPClient,
    DataNotFoundError,
    SpliceApiError,
    UpstreamInputError,
)
```

Then append this method to `EnsemblVepClient`:

```python
    async def reference_base(
        self, chrom: str, pos: int, length: int, build: GenomeBuild
    ) -> str | None:
        """Return the uppercase reference base(s) at chrom:pos..pos+length-1, or None.

        Uses Ensembl REST sequence/region on the build-specific host. Returns None
        on any upstream fault or empty sequence so callers can treat the check as
        inconclusive and fall back, never regressing behavior.
        """
        c = chrom.removeprefix("chr").removeprefix("CHR").upper()
        end = pos + max(1, length) - 1
        url = f"{settings.ensembl_url(build)}/sequence/region/human/{c}:{pos}..{end}"
        try:
            payload = await self.get_json(url, {"content-type": "application/json"})
        except SpliceApiError:
            return None
        seq = payload.get("seq") if isinstance(payload, dict) else None
        return seq.upper() if isinstance(seq, str) and seq else None
```

(`DataNotFoundError` and `UpstreamInputError` both subclass `SpliceApiError`, so the single `except SpliceApiError` covers 4xx/5xx/timeouts.)

- [ ] **Step 4: Run to verify client tests pass**

Run: `uv run pytest tests/unit/test_service.py -k reference_base -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the failing test (service caches the lookup)**

Add to `tests/unit/test_service.py`:

```python
from spliceailookup_link.services import SpliceService


class _RefStubEnsembl:
    def __init__(self) -> None:
        self.calls = 0

    async def reference_base(self, chrom, pos, length, build):
        self.calls += 1
        return "T"

    async def close(self):  # pragma: no cover
        return None


async def test_service_reference_base_is_cached() -> None:
    ens = _RefStubEnsembl()
    svc = SpliceService(ensembl_client=ens)
    a = await svc.reference_base("8", 140300616, 1, "GRCh38")
    b = await svc.reference_base("8", 140300616, 1, "GRCh38")
    assert a == b == "T"
    assert ens.calls == 1  # second call served from cache
```

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/unit/test_service.py -k reference_base_is_cached -v`
Expected: FAIL — `SpliceService` has no `reference_base`.

- [ ] **Step 7: Implement the cached service wrapper**

In `spliceailookup_link/services/splice_service.py`, inside `SpliceService.__init__`, after the existing `self._resolve_cached = ...` block, add:

```python
        self._refbase_cached = alru_cache(maxsize=cache_size, ttl=ttl_seconds)(
            self._refbase_uncached
        )
```

Then add these two methods (place them after `resolve`, before `close`):

```python
    async def _refbase_uncached(
        self, chrom: str, pos: int, length: int, build: GenomeBuild
    ) -> str | None:
        return await self._ensembl.reference_base(chrom, pos, length, build)

    async def reference_base(
        self, chrom: str, pos: int, length: int, build: GenomeBuild
    ) -> str | None:
        """Cached reference-base lookup (used by the failure-path diagnostic)."""
        return await self._refbase_cached(chrom, pos, length, build)
```

- [ ] **Step 8: Run to verify service test passes**

Run: `uv run pytest tests/unit/test_service.py -k reference_base -v`
Expected: PASS (4 tests total).

- [ ] **Step 9: Commit**

```bash
git add spliceailookup_link/api/ensembl_client.py spliceailookup_link/services/splice_service.py tests/unit/test_service.py
git commit -m "feat(F1): Ensembl reference_base lookup + cached service wrapper"
```

---

## Task 2: `RefMismatchError` + `ref_mismatch` classification

**Files:**
- Modify: `spliceailookup_link/mcp/errors.py`
- Test: `tests/unit/test_errors.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_errors.py`:

```python
from spliceailookup_link.mcp.errors import (
    McpErrorContext,
    RefMismatchError,
    mcp_tool_error,
)


def test_ref_mismatch_classifies_and_routes_to_resolve() -> None:
    exc = RefMismatchError(
        variant_id="8-140300616-A-G",
        observed_ref="A",
        reference_base="T",
        build="GRCh38",
        chrom="8",
        pos=140300616,
    )
    env = mcp_tool_error(
        exc, McpErrorContext(tool_name="predict_splicing", variant="8-140300616-A-G")
    ).payload
    assert env["error_code"] == "ref_mismatch"
    assert env["retryable"] is False
    assert env["recovery_action"] == "reformulate_input"
    assert env["fallback_tool"] == "resolve_variant"
    assert "does not match" in env["message"]
    assert "T" in env["message"]  # the actual reference base is surfaced
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_errors.py -k ref_mismatch -v`
Expected: FAIL — cannot import `RefMismatchError`.

- [ ] **Step 3: Add the exception class**

In `spliceailookup_link/mcp/errors.py`, after the `BuildMismatchError` class, add:

```python
class RefMismatchError(ValueError):
    """Raised when a coordinate's REF allele does not match the genome reference."""

    def __init__(
        self,
        *,
        variant_id: str,
        observed_ref: str,
        reference_base: str,
        build: str,
        chrom: str,
        pos: int,
    ):
        self.variant_id = variant_id
        self.observed_ref = observed_ref
        self.reference_base = reference_base
        self.build = build
        super().__init__(
            f"REF allele '{observed_ref}' does not match the {build} reference base "
            f"'{reference_base}' at {chrom}:{pos}."
        )
```

- [ ] **Step 4: Add the classification branch**

In `_classify`, add the `RefMismatchError` branch immediately after the `BuildMismatchError` branch (it must precede the generic `ValueError` branch):

```python
    if isinstance(exc, RefMismatchError):
        tool, args = _fallback_for(context)
        return "ref_mismatch", False, tool, args
```

- [ ] **Step 5: Add recovery action + text + safe-message branches**

In `_recovery_action`, extend the reformulate set:

```python
    if error_code in {"invalid_input", "validation_failed", "ref_mismatch", "ambiguous"}:
        return "reformulate_input"
```

In `_recovery_text`, add (before the final `return`):

```python
    if error_code == "ref_mismatch":
        return (
            "The REF allele does not match the genome reference at this position "
            "(likely a swapped REF/ALT, the opposite strand, or the wrong build). "
            "Fix the REF allele, or pass an HGVS/rsID to resolve_variant to get "
            "canonical CHROM-POS-REF-ALT, then retry."
        )
```

In `_envelope_message`, add `ref_mismatch` to the surfaced-verbatim set:

```python
    if error_code in {"build_mismatch", "invalid_input", "not_found", "ref_mismatch"}:
        return _safe_message(exc)
```

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/unit/test_errors.py -k ref_mismatch -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add spliceailookup_link/mcp/errors.py tests/unit/test_errors.py
git commit -m "feat(F1): ref_mismatch error code routed to resolve_variant"
```

---

## Task 3: `diagnose_coordinate_failure` (F1 + F8)

**Files:**
- Create: `spliceailookup_link/mcp/tools/_diagnose.py`
- Test: `tests/unit/test_diagnose.py` (new)
- Modify: `tests/conftest.py` (extend `StubService` with reference-base support)

- [ ] **Step 1: Extend `StubService` for reference bases**

In `tests/conftest.py`, inside `StubService.__init__`, add:

```python
        self.ref_bases: dict[str, str] = {}  # build -> base at the test locus
        self.refbase_calls: list[tuple[str, int, int, str]] = []
```

And add this method to `StubService` (after `resolve`):

```python
    async def reference_base(self, chrom: str, pos: int, length: int, build: str):
        self.refbase_calls.append((chrom, pos, length, build))
        return self.ref_bases.get(build)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_diagnose.py`:

```python
"""F1+F8: coordinate-failure diagnostic (ref_mismatch vs cheap build_mismatch)."""

from __future__ import annotations

import pytest

from spliceailookup_link.mcp.errors import BuildMismatchError, RefMismatchError
from spliceailookup_link.mcp.tools._diagnose import diagnose_coordinate_failure
from tests.conftest import StubService


async def _run(svc: StubService, variant_id: str, build: str = "GRCh38") -> None:
    await diagnose_coordinate_failure(
        svc,
        variant_id=variant_id,
        requested_build=build,
        distance=500,
        mask=0,
        gene_set="basic",
    )


async def test_ref_mismatch_when_ref_matches_neither_build() -> None:
    svc = StubService()
    svc.ref_bases = {"GRCh38": "T", "GRCh37": "C"}  # REF 'A' matches neither
    with pytest.raises(RefMismatchError) as ei:
        await _run(svc, "8-140300616-A-G")
    assert ei.value.reference_base == "T"
    # No slow scoring cross-build probe was used.
    assert svc.score_calls == []


async def test_build_mismatch_when_ref_matches_other_build() -> None:
    svc = StubService()
    svc.ref_bases = {"GRCh38": "T", "GRCh37": "A"}  # REF 'A' matches GRCh37 only
    with pytest.raises(BuildMismatchError) as ei:
        await _run(svc, "8-140300616-A-G", build="GRCh38")
    assert ei.value.inferred_build == "GRCh37"
    assert svc.score_calls == []  # cheap path, no scoring probe


async def test_genuine_not_found_when_ref_matches_requested_build() -> None:
    svc = StubService()
    svc.ref_bases = {"GRCh38": "A", "GRCh37": "A"}  # REF matches -> real no-overlap
    await _run(svc, "8-140300616-A-G")  # returns (no raise)


async def test_falls_back_to_scoring_probe_when_ensembl_unavailable() -> None:
    svc = StubService()
    svc.ref_bases = {}  # reference_base returns None -> inconclusive
    svc.only_build = "GRCh37"  # variant only scores in the other build
    with pytest.raises(BuildMismatchError):
        await _run(svc, "8-140300616-A-G", build="GRCh38")
    # The fallback DID use the scoring probe (against the other build).
    assert any(c["build"] == "GRCh37" for c in svc.score_calls)


async def test_skips_non_acgt_ref() -> None:
    svc = StubService()
    svc.ref_bases = {"GRCh38": "T", "GRCh37": "C"}
    # Symbolic / N ref -> diagnostic is a no-op (no raise, no ensembl call).
    await _run(svc, "8-140300616-N-G")
    assert svc.refbase_calls == []
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/unit/test_diagnose.py -v`
Expected: FAIL — cannot import `_diagnose`.

- [ ] **Step 4: Implement the diagnostic module**

Create `spliceailookup_link/mcp/tools/_diagnose.py`:

```python
"""Distinguish wrong-REF from wrong-build cheaply on a coordinate prediction failure.

Called by the prediction tools only on the both-models not_found path for a
coordinate input. Two cached Ensembl reference-base lookups replace a ~17s scoring
cross-build probe and turn a misleading not_found into an accurate ref_mismatch or
build_mismatch. Falls back to the scoring probe when the reference check is
inconclusive or Ensembl is unavailable, so behavior never regresses.
"""

from __future__ import annotations

from spliceailookup_link.config import GenomeBuild
from spliceailookup_link.mcp.errors import BuildMismatchError, RefMismatchError
from spliceailookup_link.mcp.tools._common import cross_build_probe
from spliceailookup_link.services import SpliceService
from spliceailookup_link.variant import VariantParseError, split_variant_id

_ACGT = set("ACGT")


async def diagnose_coordinate_failure(
    service: SpliceService,
    *,
    variant_id: str,
    requested_build: GenomeBuild,
    distance: int,
    mask: int,
    gene_set: str,
) -> None:
    """Raise RefMismatchError / BuildMismatchError when applicable; else return.

    Returning without raising means "genuine not_found" (well-formed variant with
    no overlapping transcript) — the caller re-raises the original not_found.
    """
    try:
        chrom, pos, ref, _alt = split_variant_id(variant_id)
    except VariantParseError:
        return
    if not ref or any(b not in _ACGT for b in ref.upper()):
        return  # only simple ACGT refs; skip N / symbolic alleles

    requested_base = await service.reference_base(chrom, pos, len(ref), requested_build)
    if requested_base is None:
        await _probe_fallback(service, variant_id, requested_build, distance, mask, gene_set)
        return
    if requested_base == ref.upper():
        return  # REF matches the requested-build reference: real no-overlap not_found

    other: GenomeBuild = "GRCh37" if requested_build == "GRCh38" else "GRCh38"
    other_base = await service.reference_base(chrom, pos, len(ref), other)
    if other_base == ref.upper():
        raise BuildMismatchError(
            variant_id=variant_id,
            inferred_build=other,
            requested_build=requested_build,
        )
    raise RefMismatchError(
        variant_id=variant_id,
        observed_ref=ref.upper(),
        reference_base=requested_base,
        build=requested_build,
        chrom=chrom,
        pos=pos,
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

- [ ] **Step 5: Run to verify the diagnostic tests pass**

Run: `uv run pytest tests/unit/test_diagnose.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/tools/_diagnose.py tests/unit/test_diagnose.py tests/conftest.py
git commit -m "feat(F1,F8): coordinate-failure diagnostic — ref_mismatch + cheap build disambiguation"
```

---

## Task 4: Wire the diagnostic into the prediction tools (F1 + F8)

**Files:**
- Modify: `spliceailookup_link/mcp/tools/_predict.py` (combined path)
- Modify: `spliceailookup_link/mcp/tools/spliceai.py`, `pangolin.py` (single-model not_found path)
- Test: `tests/unit/test_eval_fixes.py` (add an end-to-end case via `mcp` fixture)

- [ ] **Step 1: Write the failing end-to-end test**

Add to `tests/unit/test_eval_fixes.py` (uses the `mcp` + `stub_service` fixtures and `structured`):

```python
async def test_wrong_ref_reports_ref_mismatch(mcp, stub_service) -> None:
    stub_service.score_error = DataNotFoundError("did not return any scores")
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "C"}  # REF 'A' matches neither
    data = structured(
        await mcp.call_tool("predict_splicing", {"variant": "8-140300616-A-G"})
    )
    assert data["error_code"] == "ref_mismatch"
    assert data["fallback_tool"] == "resolve_variant"
```

(Imports `DataNotFoundError` and `structured` already come from `tests.conftest`; add them to the file's imports if absent.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes.py -k wrong_ref -v`
Expected: FAIL — code is `not_found`, not `ref_mismatch`.

- [ ] **Step 3: Replace the cross-build probe with the diagnostic in the combined path**

In `spliceailookup_link/mcp/tools/_predict.py`, update imports:

```python
from spliceailookup_link.mcp.tools._common import (
    mask_to_int,
    prepare_variant,
)
from spliceailookup_link.mcp.tools._diagnose import diagnose_coordinate_failure
```

(Remove `cross_build_probe` from the `_common` import list here — it now lives behind the diagnostic. `_diagnose` still imports it.)

Replace the both-failed branch body (currently the `if cross_build_check and prepared.resolution is None and isinstance(sai_res, DataNotFoundError):` block that calls `cross_build_probe` and raises `BuildMismatchError`) with:

```python
    if isinstance(sai_res, BaseException) and isinstance(pang_res, BaseException):
        if (
            cross_build_check
            and prepared.resolution is None
            and isinstance(sai_res, DataNotFoundError)
        ):
            # Cheap Ensembl reference check: raises BuildMismatchError or
            # RefMismatchError when applicable, else returns (genuine not_found).
            await diagnose_coordinate_failure(
                service,
                variant_id=prepared.variant_id,
                requested_build=genome_build,
                distance=max_distance,
                mask=mask_to_int(mask),
                gene_set=gene_set,
            )
        raise sai_res
```

- [ ] **Step 4: Run to verify the combined case passes**

Run: `uv run pytest tests/unit/test_eval_fixes.py -k wrong_ref -v`
Expected: PASS.

- [ ] **Step 5: Add the diagnostic to the single-model not_found path**

Open `spliceailookup_link/mcp/tools/spliceai.py` and `pangolin.py`. Each has a point where a single `service.score(...)` raises `DataNotFoundError` for a coordinate input. Wrap that scoring call so a coordinate `not_found` runs the diagnostic before re-raising. Add the import to both files:

```python
from spliceailookup_link.mcp.tools._diagnose import diagnose_coordinate_failure
```

In each tool's `call()` body, locate the `await service.score(...)` (or its wrapper) and replace it with a try/except that diagnoses coordinate inputs. Concretely, in `spliceai.py` (and the analogous spot in `pangolin.py`), where the prepared variant is scored:

```python
        try:
            payload, tele = await service.score(model="spliceai", **score_kwargs)
        except DataNotFoundError:
            if cross_build_check and prepared.resolution is None:
                await diagnose_coordinate_failure(
                    service,
                    variant_id=prepared.variant_id,
                    requested_build=genome_build,
                    distance=max_distance,
                    mask=mask_to_int(mask),
                    gene_set=gene_set,
                )
            raise
```

Use `model="pangolin"` in `pangolin.py`. If a file does not already import `DataNotFoundError` / `mask_to_int` / `cross_build_check` param, add the imports and confirm the tool already exposes a `cross_build_check` parameter (the combined tool does; mirror its `Annotated[bool, Field(...)] = True` parameter and `prepared` handling — match the existing single-model structure in the file).

> Implementer note: read `spliceai.py`/`pangolin.py` first to match their exact local variable names (`prepared`, `score_kwargs`/inline kwargs, `genome_build`, `max_distance`, `mask`). The behavior to preserve: a coordinate `not_found` runs the diagnostic; an HGVS/rsID-resolved `not_found` does not (resolution is not None).

- [ ] **Step 6: Write + run single-model regression tests**

Add to `tests/unit/test_eval_fixes.py`:

```python
async def test_spliceai_wrong_ref_reports_ref_mismatch(mcp, stub_service) -> None:
    stub_service.score_error = DataNotFoundError("did not return any scores")
    stub_service.ref_bases = {"GRCh38": "T", "GRCh37": "C"}
    data = structured(
        await mcp.call_tool("predict_spliceai", {"variant": "8-140300616-A-G"})
    )
    assert data["error_code"] == "ref_mismatch"
```

Run: `uv run pytest tests/unit/test_eval_fixes.py -k "wrong_ref or ref_mismatch" -v`
Expected: PASS.

- [ ] **Step 7: Run the full prediction test modules (regression)**

Run: `uv run pytest tests/unit/test_tools.py tests/unit/test_eval_fixes.py tests/unit/test_eval_fixes_2.py tests/unit/test_eval_fixes_3.py -v`
Expected: PASS (the existing `build_mismatch` tests that drive `only_build` still pass — the diagnostic's `_probe_fallback` preserves them when `ref_bases` is empty).

> If an existing build_mismatch test set `only_build` but no `ref_bases`, the new diagnostic falls back to the scoring probe and behavior is unchanged. If any such test now also wants the fast path, it can set `ref_bases` to exercise it — not required for green.

- [ ] **Step 8: Commit**

```bash
git add spliceailookup_link/mcp/tools/_predict.py spliceailookup_link/mcp/tools/spliceai.py spliceailookup_link/mcp/tools/pangolin.py tests/unit/test_eval_fixes.py
git commit -m "feat(F1,F8): prediction tools diagnose coordinate not_found (ref vs build)"
```

---

## Task 5: Resolver-ambiguity propagation — `ambiguous` error (F2)

**Files:**
- Modify: `spliceailookup_link/mcp/errors.py` (`AmbiguousVariantError` + classify + per-allele next_commands)
- Modify: `spliceailookup_link/mcp/tools/_common.py` (`prepare_variant` raises it)
- Modify: `spliceailookup_link/mcp/tools/batch.py` (pass `genome_build` into the per-item error context)
- Test: `tests/unit/test_errors.py`, `tests/unit/test_batch.py`

- [ ] **Step 1: Write the failing classification test**

Add to `tests/unit/test_errors.py`:

```python
from spliceailookup_link.mcp.errors import AmbiguousVariantError


def test_ambiguous_lists_alleles_and_per_allele_next_commands() -> None:
    exc = AmbiguousVariantError(
        variant="rs6025",
        candidates=["1-169549811-C-A", "1-169549811-C-T"],
        note="rs6025 maps to 2 alleles at this locus; pick one variant_id.",
    )
    env = mcp_tool_error(
        exc,
        McpErrorContext(
            tool_name="predict_splicing", variant="rs6025", genome_build="GRCh38"
        ),
    ).payload
    assert env["error_code"] == "ambiguous"
    assert env["retryable"] is False
    assert env["variant_ids"] == ["1-169549811-C-A", "1-169549811-C-T"]
    cmds = env["_meta"]["next_commands"]
    # One ready-to-call predict_splicing per allele, first.
    assert cmds[0] == {
        "tool": "predict_splicing",
        "arguments": {"variant": "1-169549811-C-A", "genome_build": "GRCh38"},
    }
    assert cmds[1]["arguments"]["variant"] == "1-169549811-C-T"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_errors.py -k ambiguous -v`
Expected: FAIL — cannot import `AmbiguousVariantError`.

- [ ] **Step 3: Add the exception, classification, and enrichment**

In `spliceailookup_link/mcp/errors.py`, after `RefMismatchError`, add:

```python
class AmbiguousVariantError(ValueError):
    """Raised when an input resolves to more than one ALT allele at the locus."""

    def __init__(self, *, variant: str, candidates: list[str], note: str | None = None):
        self.variant = variant
        self.candidates = candidates
        self.note = note
        super().__init__(
            note or f"{variant} resolves to {len(candidates)} alleles; pick one variant_id."
        )
```

In `_classify`, add after the `RefMismatchError` branch (before generic `ValueError`):

```python
    if isinstance(exc, AmbiguousVariantError):
        return "ambiguous", False, "resolve_variant", {"variant": exc.variant}
```

In `_recovery_text`, add (before the final return):

```python
    if error_code == "ambiguous":
        return (
            "This input maps to more than one ALT allele at the locus. Pick one "
            "variant_id (see variant_ids / next_commands, one prediction per allele) "
            "and retry, or call resolve_variant to review the candidates."
        )
```

In `_envelope_message`, add `ambiguous` to the surfaced-verbatim set:

```python
    if error_code in {"build_mismatch", "invalid_input", "not_found", "ref_mismatch", "ambiguous"}:
        return _safe_message(exc)
```

In `mcp_tool_error`, after `payload` is built and before the `rate_limited` block, enrich for ambiguity:

```python
    if isinstance(exc, AmbiguousVariantError):
        build = context.genome_build or "GRCh38"
        payload["variant_ids"] = exc.candidates
        payload["_meta"]["next_commands"] = [
            {"tool": "predict_splicing", "arguments": {"variant": c, "genome_build": build}}
            for c in exc.candidates
        ] + payload["_meta"]["next_commands"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_errors.py -k ambiguous -v`
Expected: PASS.

- [ ] **Step 5: Make `prepare_variant` raise on ambiguity**

In `spliceailookup_link/mcp/tools/_common.py`, add the import:

```python
from spliceailookup_link.mcp.errors import AmbiguousVariantError, BuildMismatchError
```

In `prepare_variant`, replace the resolution tail:

```python
    resolution = await service.resolve(raw_variant, genome_build)
    if resolution.get("ambiguous"):
        raise AmbiguousVariantError(
            variant=raw_variant,
            candidates=resolution.get("variant_ids") or [resolution["variant_id"]],
            note=resolution.get("note"),
        )
    return PreparedVariant(
        variant_id=resolution["variant_id"],
        genome_build=genome_build,
        consequence=resolution.get("consequence"),
        resolution=resolution,
    )
```

- [ ] **Step 6: Pass `genome_build` into batch per-item error context**

In `spliceailookup_link/mcp/tools/batch.py`, the per-item except block builds `McpErrorContext(tool_name="predict_splicing", variant=variant)`. Add the build so per-allele next_commands carry the right build:

```python
                    env = mcp_tool_error(
                        exc,
                        McpErrorContext(
                            tool_name="predict_splicing",
                            variant=variant,
                            genome_build=genome_build,
                        ),
                    ).payload
```

Also carry `variant_ids` onto the per-item dict when present (so a batch item shows the alleles). After the existing `"next_commands": env["_meta"]["next_commands"],` line inside the appended dict, the dict is closed; instead build it to include the optional field. Replace the per-item error append with:

```python
                    item: dict[str, Any] = {
                        "variant": variant,
                        "error_code": env["error_code"],
                        "message": env["message"],
                        "retryable": env["retryable"],
                        "recovery_action": env["recovery_action"],
                        "fallback_tool": env["fallback_tool"],
                        "fallback_args": env["fallback_args"],
                        "recovery": env["recovery"],
                        "next_commands": env["_meta"]["next_commands"],
                    }
                    if env.get("variant_ids"):
                        item["variant_ids"] = env["variant_ids"]
                    results.append(item)
```

- [ ] **Step 7: Write the failing batch + single end-to-end tests**

Add to `tests/unit/test_batch.py`:

```python
async def test_batch_flags_ambiguous_rsid_not_silently_scored(mcp) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_splicing_batch",
            {"variants": ["chr8-140300616-T-G", "rs6025"]},
        )
    )
    by_variant = {r["variant"]: r for r in data["results"]}
    amb = by_variant["rs6025"]
    assert amb["error_code"] == "ambiguous"
    assert amb["variant_ids"] == ["1-169549811-C-A", "1-169549811-C-T"]
    # The other variant still succeeded; the batch is not sunk.
    assert "error_code" not in by_variant["chr8-140300616-T-G"]
    assert data["summary"]["ok"] == 1
    assert data["summary"]["failed"] == 1
```

Add to `tests/unit/test_eval_fixes.py`:

```python
async def test_single_predict_ambiguous_rsid_does_not_silently_score(mcp, stub_service) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "rs6025"}))
    assert data["error_code"] == "ambiguous"
    # No allele was scored.
    assert stub_service.score_calls == []
```

- [ ] **Step 8: Run to verify they pass**

Run: `uv run pytest tests/unit/test_batch.py tests/unit/test_errors.py tests/unit/test_eval_fixes.py -k "ambiguous" -v`
Expected: PASS.

- [ ] **Step 9: Run the full batch module (regression)**

Run: `uv run pytest tests/unit/test_batch.py -v`
Expected: PASS (existing batch tests that used `rs6025` as an "ok" item, if any, are updated by this task — search the file and adjust any assertion that expected `rs6025` to score; it now fails as `ambiguous`).

- [ ] **Step 10: Commit**

```bash
git add spliceailookup_link/mcp/errors.py spliceailookup_link/mcp/tools/_common.py spliceailookup_link/mcp/tools/batch.py tests/unit/test_errors.py tests/unit/test_batch.py tests/unit/test_eval_fixes.py
git commit -m "feat(F2): propagate resolver ambiguity as a distinct ambiguous error with per-allele next_commands"
```

---

## Task 6: Echo `capabilities_version` in every `_meta` (F3)

**Files:**
- Modify: `spliceailookup_link/mcp/resources.py` (cached accessor)
- Modify: `spliceailookup_link/mcp/errors.py` (`_provenance_meta`)
- Test: `tests/unit/test_eval_fixes.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_eval_fixes.py`:

```python
from spliceailookup_link.mcp.resources import get_capabilities_version


async def test_capabilities_version_echoed_on_success(mcp) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert data["_meta"]["capabilities_version"] == get_capabilities_version()


async def test_capabilities_version_echoed_on_error(mcp, stub_service) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "not a variant!!"}))
    assert data["error_code"] == "invalid_input"
    assert data["_meta"]["capabilities_version"] == get_capabilities_version()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes.py -k capabilities_version_echoed -v`
Expected: FAIL — `_meta` has no `capabilities_version` (and/or import missing).

- [ ] **Step 3: Add the cached version accessor**

In `spliceailookup_link/mcp/resources.py`, after `_capabilities_version`, add a module-level cache + accessor:

```python
_CAPABILITIES_VERSION: str | None = None


def get_capabilities_version() -> str:
    """The full capabilities doc's content hash, computed once and cached.

    Echoed into every response `_meta` so a warm client compares the hash and
    skips re-fetching the capabilities document until it actually changes.
    """
    global _CAPABILITIES_VERSION
    if _CAPABILITIES_VERSION is None:
        _CAPABILITIES_VERSION = get_capabilities_resource()["capabilities_version"]
    return _CAPABILITIES_VERSION
```

(Place `get_capabilities_resource` definition before this accessor is *called*; it already exists later in the module — Python resolves the name at call time, so module order is fine, but keep the accessor function defined after `_capabilities_version`.)

- [ ] **Step 4: Stamp it into provenance**

In `spliceailookup_link/mcp/errors.py`, add the import near the top:

```python
from spliceailookup_link.mcp.resources import get_capabilities_version
```

Replace `_provenance_meta`:

```python
def _provenance_meta() -> dict[str, Any]:
    return {**_BASE_META, "capabilities_version": get_capabilities_version()}
```

> Import-cycle check: `resources.py` imports only `config` + `mcp.types`; it does **not** import `errors`. So `errors` importing `resources` introduces no cycle. `get_capabilities_version()` is lazy, so no doc is built at import time.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/unit/test_eval_fixes.py -k capabilities_version_echoed -v`
Expected: PASS (both success and error envelopes carry the hash).

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/resources.py spliceailookup_link/mcp/errors.py tests/unit/test_eval_fixes.py
git commit -m "feat(F3): echo capabilities_version in every response _meta"
```

---

## Task 7: Server-side soft deadline → `upstream_unavailable` (F4)

**Files:**
- Modify: `spliceailookup_link/config.py`
- Modify: `spliceailookup_link/mcp/tools/_predict.py`
- Test: `tests/unit/test_eval_fixes.py`, `tests/unit/test_config_cli.py`

- [ ] **Step 1: Add the setting**

In `spliceailookup_link/config.py`, inside `Settings`, after `MAX_RETRIES`, add:

```python
    # Foreground prediction soft deadline (seconds). A comprehensive gene_set with a
    # large max_distance can exceed the client's MCP timeout; this returns a
    # structured upstream_unavailable before the client gives up. Set 0 to disable.
    # Background Tasks (ctx.is_background_task) bypass this deadline.
    PREDICT_SOFT_DEADLINE_SECONDS: int = 55
```

- [ ] **Step 2: Write the failing test**

Add to `tests/unit/test_eval_fixes.py`:

```python
import asyncio

from spliceailookup_link.config import settings


async def test_soft_deadline_returns_upstream_unavailable(mcp, stub_service, monkeypatch) -> None:
    monkeypatch.setattr(settings, "PREDICT_SOFT_DEADLINE_SECONDS", 1)

    async def _slow_score(*args, **kwargs):
        await asyncio.sleep(5)

    # Make scoring exceed the 1s deadline.
    monkeypatch.setattr(stub_service, "score", _slow_score)
    data = structured(
        await mcp.call_tool(
            "predict_splicing",
            {"variant": "chr8-140300616-T-G", "gene_set": "comprehensive"},
        )
    )
    assert data["error_code"] == "upstream_unavailable"
    assert data["retryable"] is True
    assert "task" in data["recovery"].lower()
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes.py -k soft_deadline -v`
Expected: FAIL — the call hangs ~5s then returns scores or errors differently (no deadline yet).

- [ ] **Step 4: Implement the deadline wrap**

In `spliceailookup_link/mcp/tools/_predict.py`, ensure imports include `asyncio` (already there) and `SpliceApiError`:

```python
from spliceailookup_link.api import DataNotFoundError, SpliceApiError
```

Add a small helper near the top of the module (after imports):

```python
def _running_as_task(ctx: Any) -> bool:
    return bool(ctx is not None and getattr(ctx, "is_background_task", False))
```

Replace the scoring `gather` block:

```python
    if ctx is not None:
        await ctx.report_progress(progress=1, total=3, message="scoring SpliceAI + Pangolin")
    deadline = settings.PREDICT_SOFT_DEADLINE_SECONDS
    gather_coro = asyncio.gather(
        service.score(model="spliceai", **common),
        service.score(model="pangolin", **common),
        return_exceptions=True,
    )
    if deadline and not _running_as_task(ctx):
        try:
            gathered: list[Any] = list(await asyncio.wait_for(gather_coro, timeout=deadline))
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise SpliceApiError(
                f"Scoring exceeded the server's {deadline}s deadline "
                "(comprehensive gene_set and/or a large max_distance are slow)."
            ) from exc
    else:
        gathered = list(await gather_coro)
```

Add the `settings` import to `_predict.py` if not present:

```python
from spliceailookup_link.config import GenomeBuild, settings
```

- [ ] **Step 5: Sharpen the `upstream_unavailable` recovery text**

In `spliceailookup_link/mcp/errors.py` `_recovery_text`, replace the `upstream_unavailable` branch with:

```python
    if error_code == "upstream_unavailable":
        return (
            "The scoring service failed transiently, or the call exceeded the server's "
            "soft deadline (comprehensive gene_set and/or a large max_distance are slow "
            "and may 503 upstream). Retry with backoff using gene_set='basic' or a smaller "
            "max_distance, or resubmit as a background task (task=…), which is not bound by "
            "the deadline."
        )
```

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/unit/test_eval_fixes.py -k soft_deadline -v`
Expected: PASS (returns within ~1s with `upstream_unavailable`).

- [ ] **Step 7: Add a config-default regression test**

Add to `tests/unit/test_config_cli.py`:

```python
def test_predict_soft_deadline_default() -> None:
    from spliceailookup_link.config import Settings

    assert Settings().PREDICT_SOFT_DEADLINE_SECONDS == 55
```

Run: `uv run pytest tests/unit/test_config_cli.py -k soft_deadline -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add spliceailookup_link/config.py spliceailookup_link/mcp/tools/_predict.py spliceailookup_link/mcp/errors.py tests/unit/test_eval_fixes.py tests/unit/test_config_cli.py
git commit -m "feat(F4): server-side soft deadline returns structured upstream_unavailable (tasks bypass)"
```

---

## Task 8: `discordant_subthreshold` verdict (F7)

**Files:**
- Modify: `spliceailookup_link/mcp/tools/_predict_shape.py`
- Modify: `spliceailookup_link/mcp/tools/batch.py` (histogram)
- Test: `tests/unit/test_predict_shape.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_predict_shape.py`:

```python
from spliceailookup_link.mcp.tools._predict_shape import assess_agreement


def test_subthreshold_split_is_not_discordant() -> None:
    # 0.31 (moderate) vs 0.09 (low): neither crosses high -> not a strong conflict.
    a = assess_agreement(0.31, 0.09)
    assert a["verdict"] == "discordant_subthreshold"
    b = assess_agreement(0.21, 0.05)
    assert b["verdict"] == "discordant_subthreshold"


def test_high_vs_low_is_still_discordant() -> None:
    # One model high-confidence, the other not -> genuine discordance.
    assert assess_agreement(0.85, 0.10)["verdict"] == "discordant"


def test_existing_concordant_bands_unchanged() -> None:
    assert assess_agreement(0.7, 0.8)["verdict"] == "concordant_high"
    assert assess_agreement(0.3, 0.4)["verdict"] == "concordant_moderate"
    assert assess_agreement(0.05, 0.1)["verdict"] == "concordant_low"
    assert assess_agreement(0.9, None)["verdict"] == "incomplete"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_predict_shape.py -k "subthreshold or discordant" -v`
Expected: FAIL — 0.31/0.09 currently returns `discordant`.

- [ ] **Step 3: Refine `assess_agreement`**

In `spliceailookup_link/mcp/tools/_predict_shape.py`, update `_VERDICT_CLAUSE` to add the new clause:

```python
_VERDICT_CLAUSE = {
    "concordant_high": "models agree (both strong)",
    "concordant_moderate": "models agree (both moderate)",
    "concordant_low": "models agree (both low/none)",
    "discordant": "models disagree",
    "discordant_subthreshold": "models differ on a weak signal (neither >=0.5)",
}
```

Replace the `else` tail of `assess_agreement` (the current `discordant` assignment) with a split on whether a high-confidence call is involved:

```python
    both_high = sai_max >= _HIGH and pang_max >= _HIGH
    both_low = sai_max < _LOW and pang_max < _LOW
    both_moderate = (_LOW <= sai_max < _HIGH) and (_LOW <= pang_max < _HIGH)
    either_high = sai_max >= _HIGH or pang_max >= _HIGH
    if both_high:
        verdict, detail = "concordant_high", "both models predict a strong splicing effect"
    elif both_low:
        verdict, detail = "concordant_low", "both models predict little or no splicing effect"
    elif both_moderate:
        verdict, detail = "concordant_moderate", "both models predict a moderate splicing effect"
    elif either_high:
        verdict, detail = (
            "discordant",
            "one model predicts a high-confidence effect and the other does not; "
            "interpret with caution",
        )
    else:
        verdict, detail = (
            "discordant_subthreshold",
            "the models differ in magnitude but neither crosses the high-confidence "
            "threshold (delta>=0.5); treat as a weak/uncertain signal, not a strong conflict",
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_predict_shape.py -k "subthreshold or discordant or concordant" -v`
Expected: PASS.

- [ ] **Step 5: Add the histogram counter in batch**

In `spliceailookup_link/mcp/tools/batch.py`, update `verdict_counts`:

```python
            verdict_counts = {
                "concordant_high": 0,
                "concordant_moderate": 0,
                "concordant_low": 0,
                "discordant": 0,
                "discordant_subthreshold": 0,
                "incomplete": 0,
            }
```

- [ ] **Step 6: Run the shape + batch modules (regression)**

Run: `uv run pytest tests/unit/test_predict_shape.py tests/unit/test_batch.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add spliceailookup_link/mcp/tools/_predict_shape.py spliceailookup_link/mcp/tools/batch.py tests/unit/test_predict_shape.py
git commit -m "feat(F7): discordant_subthreshold verdict for both-below-high splits"
```

---

## Task 9: `warmup` coverage + optional `mask` (F5)

**Files:**
- Modify: `spliceailookup_link/services/splice_service.py` (`warmup` accepts mask, returns coverage)
- Modify: `spliceailookup_link/mcp/tools/metadata.py` (warmup tool arg + coverage passthrough)
- Modify: `tests/conftest.py` (`StubService.warmup` signature)
- Test: `tests/unit/test_tools.py`

- [ ] **Step 1: Update the service `warmup` signature**

In `spliceailookup_link/services/splice_service.py`, change `warmup`:

```python
    async def warmup(self, build: GenomeBuild, mask: int = 0) -> dict[str, Any]:
        """Wake the upstream Cloud Run containers with a known-good sentinel call.

        Warms only the (basic gene_set, given mask) path per model; Cloud Run scales
        per-instance, so other param combinations or concurrent calls may still
        cold-start, and warmth decays after minutes of idle.
        """
        sentinel = "8-140300616-T-G"
        detail: dict[str, Any] = {}
        for model in ("spliceai", "pangolin"):
            start = perf_counter()
            status = "ok"
            try:
                await self._scoring.score(
                    model=model,  # type: ignore[arg-type]
                    build=build,
                    variant=sentinel,
                    distance=50,
                    mask=mask,
                    gene_set="basic",
                    raw=None,
                    variant_consequence=None,
                )
            except DataNotFoundError:
                status = "ok"
            except SpliceApiError:
                status = "unavailable"
            detail[model] = {"status": status, "elapsed_ms": int((perf_counter() - start) * 1000)}
        return detail
```

- [ ] **Step 2: Update the warmup tool**

In `spliceailookup_link/mcp/tools/metadata.py`, add a `mask` parameter and emit `coverage` + `note`:

```python
    async def warmup(
        genome_build: Annotated[
            Literal["GRCh37", "GRCh38"],
            Field(description="Build whose scoring containers to warm. GRCh38 default."),
        ] = "GRCh38",
        mask: Annotated[
            Literal["raw", "masked"],
            Field(description="Which mask path to warm (raw default; warm masked if you'll use it)."),
        ] = "raw",
    ) -> dict[str, Any]:
        """Pre-warm the SpliceAI + Pangolin Cloud Run containers before a burst so the first real call does not eat the 10-40s cold start. Warms the (basic gene_set, chosen mask) path per model; Cloud Run scales per-instance, so other param combos or concurrent calls may still cold-start and warmth decays after minutes idle. Returns per-model elapsed_ms + coverage. Returns <1kB."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            mask_int = 1 if mask == "masked" else 0
            detail = await service.warmup(genome_build, mask_int)
            warmed = all(d["status"] == "ok" for d in detail.values())
            return {
                "warmed": warmed,
                "genome_build": genome_build,
                "detail": detail,
                "coverage": {
                    "models": ["spliceai", "pangolin"],
                    "mask": mask,
                    "gene_set": "basic",
                },
                "note": (
                    "Warms only this (mask, basic gene_set) path per model. Cloud Run "
                    "autoscales per-instance: subsequent calls with other params or under "
                    "concurrency may still cold-start, and warmth decays after minutes idle. "
                    "For a guaranteed-cold first call, prefer a background task."
                ),
            }

        return await run_mcp_tool("warmup", call)
```

- [ ] **Step 3: Update the test stub**

In `tests/conftest.py`, change `StubService.warmup`:

```python
    async def warmup(self, build: str, mask: int = 0) -> dict[str, Any]:
        return {
            "spliceai": {"status": "ok", "elapsed_ms": 3},
            "pangolin": {"status": "ok", "elapsed_ms": 4},
        }
```

- [ ] **Step 4: Write + run the test**

Add to `tests/unit/test_tools.py`:

```python
async def test_warmup_reports_coverage_and_accepts_mask(mcp) -> None:
    data = structured(await mcp.call_tool("warmup", {"genome_build": "GRCh38", "mask": "masked"}))
    assert data["warmed"] is True
    assert data["coverage"]["mask"] == "masked"
    assert data["coverage"]["gene_set"] == "basic"
    assert "cold-start" in data["note"]
```

Run: `uv run pytest tests/unit/test_tools.py -k warmup -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/services/splice_service.py spliceailookup_link/mcp/tools/metadata.py tests/conftest.py tests/unit/test_tools.py
git commit -m "feat(F5): warmup reports coverage + accepts mask; document warmth scope/TTL"
```

---

## Task 10: Lean capabilities mode (F6)

**Files:**
- Modify: `spliceailookup_link/mcp/resources.py` (`get_capabilities_resource(detail=...)`)
- Modify: `spliceailookup_link/mcp/tools/metadata.py` (`detail` param on the tool)
- Test: `tests/unit/test_tools.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_tools.py`:

```python
from spliceailookup_link.mcp.resources import get_capabilities_resource, get_capabilities_version


def test_lean_capabilities_omits_param_prose_keeps_hash() -> None:
    lean = get_capabilities_resource(detail="lean")
    full = get_capabilities_resource(detail="full")
    assert "parameters" not in lean
    assert "params_by_reference" in lean
    assert lean["tools"] == full["tools"]
    # The version hash is the FULL doc's hash in both.
    assert lean["capabilities_version"] == get_capabilities_version()
    assert len(str(lean)) < len(str(full))


async def test_capabilities_tool_detail_lean(mcp) -> None:
    data = structured(await mcp.call_tool("get_server_capabilities", {"detail": "lean"}))
    assert "parameters" not in data
    assert data["error_codes"]  # still present for branching
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_tools.py -k "lean or detail" -v`
Expected: FAIL — `get_capabilities_resource` takes no `detail` arg.

- [ ] **Step 3: Implement `detail` in the doc builder**

In `spliceailookup_link/mcp/resources.py`, change `get_capabilities_resource` to accept `detail` and trim for lean. Keep the full doc construction exactly as-is up to the version stamping; then branch:

```python
def get_capabilities_resource(detail: str = "full") -> dict[str, Any]:
    doc: dict[str, Any] = {
        # ... unchanged full doc body ...
    }
    version_hash, chars = _capabilities_version(doc)
    doc["capabilities_version"] = version_hash
    doc["descriptor_chars"] = chars
    if detail == "lean":
        return _lean_capabilities(doc)
    return doc
```

Add the lean projection helper after the function:

```python
def _lean_capabilities(full: dict[str, Any]) -> dict[str, Any]:
    """SEP-1576-aligned lean view: tool list + verdicts + codes + hash, params by reference."""
    return {
        "server": full["server"],
        "server_version": full["server_version"],
        "mcp_protocol_version": full["mcp_protocol_version"],
        "research_use_only": True,
        "tools": full["tools"],
        "recommended_workflows": full["recommended_workflows"],
        "agreement_verdicts": full["agreement_verdicts"],
        "interpretation_bands": full["interpretation_bands"],
        "error_codes": full["error_codes"],
        "params_by_reference": (
            "Per-parameter docs live in each tool's input schema and "
            "spliceailookup://reference; omitted here to avoid duplication (SEP-1576). "
            "Call get_server_capabilities(detail='full') for the complete document."
        ),
        "capabilities_version": full["capabilities_version"],
        "descriptor_chars": full["descriptor_chars"],
    }
```

> `get_capabilities_version()` (Task 6) calls `get_capabilities_resource()` with the default `detail="full"`, so the cached hash stays the full doc's hash. Confirm that accessor is unchanged.

- [ ] **Step 4: Add the `detail` param to the tool**

In `spliceailookup_link/mcp/tools/metadata.py`, update `get_server_capabilities`:

```python
    async def get_server_capabilities(
        detail: Annotated[
            Literal["full", "lean"],
            Field(description="full (default, complete doc) or lean (tool list + hash + glossary; params by reference)."),
        ] = "full",
    ) -> dict[str, Any]:
        """Use this first in a cold session to learn the tools, parameters, score glossary, recommended workflows, error codes, and limitations. detail='lean' returns a trimmed doc (tool list + verdicts + error codes + capabilities_version) that omits per-parameter prose already in the tool schemas. Full ~4kB, lean ~1-2kB."""

        async def call() -> dict[str, Any]:
            return get_capabilities_resource(detail=detail)

        return await run_mcp_tool("get_server_capabilities", call)
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/unit/test_tools.py -k "lean or detail" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/resources.py spliceailookup_link/mcp/tools/metadata.py tests/unit/test_tools.py
git commit -m "feat(F6): lean capabilities mode (params by reference, SEP-1576)"
```

---

## Task 11: Capabilities/glossary docs + version bump to 0.6.0

**Files:**
- Modify: `spliceailookup_link/mcp/resources.py` (error codes, verdicts, warmth, deadline, resolve_caveat, version-echo doc)
- Modify: `spliceailookup_link/__init__.py`, `pyproject.toml` (version)
- Test: `tests/unit/test_eval_fixes.py` (capabilities content assertions)

- [ ] **Step 1: Write the failing capabilities-content test**

Add to `tests/unit/test_eval_fixes.py`:

```python
def test_capabilities_documents_new_codes_and_verdict() -> None:
    doc = get_capabilities_resource()
    assert "ref_mismatch" in doc["error_codes"]
    assert "ambiguous" in doc["error_codes"]
    assert "discordant_subthreshold" in doc["agreement_verdicts"]
    ref = get_reference_resource()
    assert "ref_mismatch" in ref["error_taxonomy"]["codes"]
    assert "ambiguous" in ref["error_taxonomy"]["codes"]
```

Add the imports at the top of the file if missing:

```python
from spliceailookup_link.mcp.resources import get_capabilities_resource, get_reference_resource
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes.py -k documents_new_codes -v`
Expected: FAIL — codes/verdict not in the doc yet.

- [ ] **Step 3: Update the capabilities doc**

In `spliceailookup_link/mcp/resources.py` `get_capabilities_resource`, in the `error_codes` list add `"ref_mismatch"` and `"ambiguous"`:

```python
        "error_codes": [
            "invalid_input",
            "not_found",
            "ref_mismatch",
            "ambiguous",
            "build_mismatch",
            "rate_limited",
            "validation_failed",
            "upstream_unavailable",
            "internal_error",
        ],
```

In `agreement_verdicts`, add `"discordant_subthreshold"`:

```python
        "agreement_verdicts": [
            "concordant_high",
            "concordant_moderate",
            "concordant_low",
            "discordant",
            "discordant_subthreshold",
            "incomplete",
        ],
```

Update the `resolve_caveat` glossary string to note detection now happens:

```python
            "resolve_caveat": (
                "Coordinate inputs are normalized, not deeply validated up front, but a "
                "wrong REF allele is now detected at prediction time via an Ensembl "
                "reference-base check and returned as ref_mismatch (not a misleading "
                "not_found)."
            ),
```

Update `response_fields.capabilities_version` to state it is echoed:

```python
            "capabilities_version": (
                "stable content hash of this document (+ descriptor_chars), ALSO echoed in "
                "every response's _meta so a warm client compares it and skips re-fetching "
                "the full capabilities until it changes. detail='lean' returns a trimmed doc."
            ),
```

Add a `warmth` section (after `background_execution`):

```python
        "warmth": {
            "scope": "warmup warms the (basic gene_set, chosen mask) path per model.",
            "ttl": "upstream-controlled (Cloud Run idle scale-down, ~minutes); not guaranteed.",
            "caveat": (
                "Cloud Run autoscales per-instance, so a subsequent call with other params or "
                "under concurrency may still cold-start. For a guaranteed-cold first call, "
                "prefer a background task over relying on warmup."
            ),
        },
```

Add a `prediction_deadline` note inside `concurrency` (or as a sibling key):

```python
        "prediction_deadline": (
            "Foreground predict_* calls have a server soft deadline "
            f"({settings.PREDICT_SOFT_DEADLINE_SECONDS}s); exceeding it returns a retryable "
            "upstream_unavailable. Background tasks bypass the deadline — use them for "
            "comprehensive gene_set / large max_distance."
        ),
```

- [ ] **Step 4: Update the reference taxonomy**

In `get_reference_resource`, in `error_taxonomy.codes`, add (after `not_found`):

```python
                "ref_mismatch": {
                    "retryable": False,
                    "when": "coordinate REF allele does not match the genome reference at that "
                    "position/build (swapped REF/ALT, wrong strand, or wrong build)",
                },
                "ambiguous": {
                    "retryable": False,
                    "when": "input (e.g. an rsID) maps to >1 ALT allele; pick one variant_id "
                    "(see variant_ids / next_commands) and retry",
                },
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/unit/test_eval_fixes.py -k documents_new_codes -v`
Expected: PASS.

- [ ] **Step 6: Bump the version to 0.6.0**

In `spliceailookup_link/__init__.py`, set `__version__ = "0.6.0"` (match the existing assignment form in that file). In `pyproject.toml`, change `version = "0.5.0"` to `version = "0.6.0"`.

- [ ] **Step 7: Reinstall so the package version resolves, then verify**

Run: `uv pip install -e . --quiet && uv run python -c "from spliceailookup_link.mcp.resources import get_capabilities_resource as g; print(g()['server_version'])"`
Expected: prints `0.6.0`.

- [ ] **Step 8: Commit**

```bash
git add spliceailookup_link/mcp/resources.py spliceailookup_link/__init__.py pyproject.toml tests/unit/test_eval_fixes.py
git commit -m "docs(F1-F7): capabilities documents ref_mismatch/ambiguous/discordant_subthreshold, warmth, deadline; bump 0.6.0"
```

---

## Task 12: Full gate + capabilities-version drift check

**Files:** none (verification only)

- [ ] **Step 1: Run the full local CI gate**

Run: `make ci-local`
Expected: format clean, ruff clean, `lint-loc` reports every module < 600, mypy clean, all unit tests PASS, coverage ≥ 80%.

- [ ] **Step 2: Fix any LOC overflow if reported**

If `lint-loc` flags a module ≥ 600 (most likely `resources.py` or `errors.py`), split the offender by responsibility — e.g. move the capabilities doc body to `resources.py` and the lean projection + reference/usage/citations into a sibling `resources_reference.py`, or move the new error classes into `mcp/error_types.py` imported by `errors.py`. Re-run `make ci-local`. (Do not add a `.loc-allowlist` entry — keep modules under 600.)

- [ ] **Step 3: Confirm the capabilities_version changed from v0.5.0**

Run: `uv run python -c "from spliceailookup_link.mcp.resources import get_capabilities_version as v; print(v())"`
Expected: prints a 12-char hash. Sanity: it differs from the pre-change value because error_codes/verdicts/version changed. The value is stable across repeated calls in one process (cached).

- [ ] **Step 4: Optional live smoke (integration, network)**

Run: `make test-integration` (may rate-limit/cold-start; not part of default CI). Manually confirm against a rebuilt server: `predict_splicing("8-140300616-A-G")` → `ref_mismatch`; `predict_splicing("rs6025")` → `ambiguous` with two `variant_ids`; a compact prediction `_meta` carries `capabilities_version`; `get_server_capabilities(detail="lean")` is materially smaller than full.

- [ ] **Step 5: Final commit (only if Step 2 made changes)**

```bash
git add -A
git commit -m "chore: keep modules <600 LOC after v0.6.0 tester-report fixes"
```

---

## Self-Review (completed by plan author)

**Spec coverage:** F1 → Tasks 1–4; F2 → Task 5; F3 → Task 6; F4 → Task 7; F5 → Task 9; F6 → Task 10; F7 → Task 8; F8 → Tasks 1/3/4 (same Ensembl mechanism, fast build disambiguation + `_probe_fallback`); capabilities/version → Task 11; gate → Task 12. All 8 findings + the version bump are covered.

**Placeholder scan:** No `TBD`/`TODO`/"add error handling" steps. Two "implementer note" callouts (Task 4 Step 5, Task 12 Step 2) direct reading existing code to match local names / split on overflow — these are concrete instructions, not placeholders, because the surrounding code is already shown.

**Type consistency:** `reference_base(chrom, pos, length, build)` signature identical across client, service, stub, and `_diagnose`. `RefMismatchError`/`AmbiguousVariantError` keyword fields match between definition (Tasks 2/5) and construction (`_diagnose`, `_common`). `diagnose_coordinate_failure(service, *, variant_id, requested_build, distance, mask, gene_set)` identical between definition (Task 3) and all call sites (Task 4). `get_capabilities_resource(detail=...)` and `get_capabilities_version()` consistent across Tasks 6/10/11. Verdict string `discordant_subthreshold` identical in `_predict_shape.py`, `batch.py`, and capabilities.

**F4 task-mode:** resolved against the installed FastMCP 3.4.2 — `Context.is_background_task` is a real property; `_running_as_task` reads it defensively (`getattr(..., False)`).
