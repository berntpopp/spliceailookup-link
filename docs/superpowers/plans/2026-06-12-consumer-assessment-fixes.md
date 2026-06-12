# Consumer-Assessment Fixes (F18–F24) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every finding in `docs/mcp-consumer-assessment-2026-06-12.md` so the server lands cleanly above 9.5/10, without breaking any existing tool name, schema field, or test.

**Architecture:** Six additive changes to the hand-authored MCP facade: (F18) a new resilient batch runner module that retries retryable items once and splits terminal vs retryable failures; (F19) a new non-retryable `unsupported_contig` error code that fast-fails non-nuclear contigs before any upstream call; (F20) Ensembl GENCODE id normalization in shaping; (F21) tool-aware recovery prose; (F22) an `include_hints` opt-out on standalone tools; (F24) capability-doc + version updates. Each is independently testable and committed separately.

**Tech Stack:** Python 3.12, FastMCP, httpx, pydantic, async_lru, pytest (respx-mocked unit tests). Conventions: `uv`, Ruff, mypy, `make ci-local`, 600-LOC/module cap.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `spliceailookup_link/variant.py` | modify | `SCORING_CONTIGS`, `unsupported_contig_reason`, `UnsupportedContigError` |
| `spliceailookup_link/mcp/tools/_common.py` | modify | `prepare_variant` raises `UnsupportedContigError` for non-nuclear contigs |
| `spliceailookup_link/mcp/errors.py` | modify | classify `unsupported_contig`; tool-aware `_recovery_text` |
| `spliceailookup_link/config.py` | modify | `BATCH_RETRY_BACKOFF_SECONDS` |
| `spliceailookup_link/mcp/tools/_batch_runner.py` | create | resilient per-item loop: retry-once, summary split, `retry_variants`, `rate_budget` |
| `spliceailookup_link/mcp/tools/batch.py` | modify | thin wrapper delegating to `run_batch` |
| `spliceailookup_link/mcp/shaping.py` | modify | `_normalize_ensembl_id` for `gene_id`/`transcript_id` |
| `spliceailookup_link/mcp/tools/resolve.py` | modify | `include_hints`; `scoring_supported` note for non-nuclear contigs |
| `spliceailookup_link/mcp/tools/combined.py` | modify | `include_hints` |
| `spliceailookup_link/mcp/tools/spliceai.py` | modify | `include_hints` |
| `spliceailookup_link/mcp/tools/pangolin.py` | modify | `include_hints` |
| `spliceailookup_link/mcp/resources.py` | modify | document new code/fields |
| `spliceailookup_link/__init__.py`, `pyproject.toml` | modify | version → 0.7.0 |
| `tests/unit/test_eval_fixes_4.py` | create | F18–F24 regression tests |
| `tests/unit/test_variant_parse.py`, `test_shaping.py`, `test_errors.py`, `test_batch.py` | modify | targeted additions |

---

## Task 1: F19 — `unsupported_contig` fast-fail

**Files:**
- Modify: `spliceailookup_link/variant.py`
- Modify: `spliceailookup_link/mcp/tools/_common.py`
- Modify: `spliceailookup_link/mcp/errors.py`
- Test: `tests/unit/test_variant_parse.py`, `tests/unit/test_eval_fixes_4.py`

- [ ] **Step 1: Write the failing test (variant helper)**

Add to `tests/unit/test_variant_parse.py`:

```python
from spliceailookup_link.variant import (
    UnsupportedContigError,
    unsupported_contig_reason,
)


def test_unsupported_contig_reason_flags_mt():
    assert unsupported_contig_reason("MT-3243-A-G") is not None
    assert "Mitochondrial" in unsupported_contig_reason("MT-3243-A-G")
    assert unsupported_contig_reason("chrM-100-A-G") is not None


def test_unsupported_contig_reason_allows_nuclear():
    assert unsupported_contig_reason("1-169549811-C-A") is None
    assert unsupported_contig_reason("chrX-100-A-G") is None
    assert unsupported_contig_reason("Y-100-A-G") is None


def test_unsupported_contig_error_is_parse_error_subclass():
    assert issubclass(UnsupportedContigError, Exception)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_variant_parse.py -k unsupported_contig -v`
Expected: FAIL with `ImportError` / `cannot import name 'unsupported_contig_reason'`.

- [ ] **Step 3: Implement in `variant.py`**

After the `VariantParseError` class (around line 32), add:

```python
class UnsupportedContigError(VariantParseError):
    """Raised when a variant's contig is outside the splice models' nuclear scope.

    SpliceAI and Pangolin are trained on the nuclear chromosomes (1-22, X, Y);
    mitochondrial (M/MT) and non-standard contigs are out of model scope and would
    otherwise burn a slow upstream slot before a 503. Subclasses VariantParseError
    so the error layer maps it deterministically (a distinct code, checked first).
    """


# Contigs the SpliceAI / Pangolin models actually score (nuclear genome only).
SCORING_CONTIGS = {str(i) for i in range(1, 23)} | {"X", "Y"}


def unsupported_contig_reason(variant_id: str) -> str | None:
    """Return a reason string if variant_id's contig is not scorable, else None."""
    chrom = variant_id.split("-", 1)[0]
    c = chrom.removeprefix("chr").removeprefix("CHR").upper()
    if c in SCORING_CONTIGS:
        return None
    if c in ("M", "MT"):
        return (
            "Mitochondrial contig (MT) is not supported by the SpliceAI/Pangolin "
            "splice models, which score only the nuclear chromosomes (1-22, X, Y)."
        )
    return (
        f"Contig '{chrom}' is not supported by the SpliceAI/Pangolin splice models, "
        "which score only the nuclear chromosomes (1-22, X, Y)."
    )
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/unit/test_variant_parse.py -k unsupported_contig -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Write the failing test (fast-fail through the tool, no scoring call)**

Create `tests/unit/test_eval_fixes_4.py` with this header and first test:

```python
"""Regression tests for docs/mcp-consumer-assessment-2026-06-12.md (F18-F24)."""

from __future__ import annotations

from spliceailookup_link.api import RateLimitedError, SpliceApiError
from tests.conftest import StubService, structured


async def test_f19_mt_fast_fails_unsupported_contig_no_scoring(mcp, stub_service: StubService) -> None:
    res = await mcp.call_tool("predict_splicing", {"variant": "MT-3243-A-G"})
    data = structured(res)
    assert data["success"] is False
    assert data["error_code"] == "unsupported_contig"
    assert data["retryable"] is False
    # The whole point: no upstream scoring slot was ever consumed.
    assert stub_service.score_calls == []
    # Recovery points the caller at the right cross-server tool.
    assert "gnomad-link" in data["recovery"]


async def test_f19_mt_in_batch_is_per_item_unsupported_contig(mcp, stub_service: StubService) -> None:
    data = structured(
        await mcp.call_tool(
            "predict_splicing_batch",
            {"variants": ["chr8-140300616-T-G", "MT-3243-A-G"]},
        )
    )
    by_variant = {r["variant"]: r for r in data["results"]}
    assert by_variant["MT-3243-A-G"]["error_code"] == "unsupported_contig"
    assert "error_code" not in by_variant["chr8-140300616-T-G"]
    # MT consumed no scoring slot; only the valid item scored (spliceai + pangolin).
    assert all(c["variant_id"] != "MT-3243-A-G" for c in stub_service.score_calls)
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k f19 -v`
Expected: FAIL — currently MT reaches scoring (`error_code` is not `unsupported_contig`).

- [ ] **Step 7: Guard in `prepare_variant` (`_common.py`)**

Add the import at the top of `_common.py`:

```python
from spliceailookup_link.variant import (
    UnsupportedContigError,
    parse_variant_input,
    unsupported_contig_reason,
)
```

(Replace the existing `from spliceailookup_link.variant import parse_variant_input` line.)

In `prepare_variant`, in the coordinate branch, immediately after `parsed = parse_variant_input(raw_variant)` and the `if parsed.kind == "coordinate":` guard, before `detect_build_mismatch`, add the contig check; and in the resolved branch add it after computing the resolved id. Concretely, replace the coordinate branch body:

```python
    if parsed.kind == "coordinate":
        _reject_unsupported_contig(parsed.value)
        inferred = detect_build_mismatch(parsed.value, genome_build)
        ...
```

and after `resolution = await service.resolve(...)` + ambiguity check, before building the resolved `PreparedVariant`, add:

```python
    _reject_unsupported_contig(resolution["variant_id"])
```

Then add the helper near the top of `_common.py` (after `mask_to_int`):

```python
def _reject_unsupported_contig(variant_id: str) -> None:
    reason = unsupported_contig_reason(variant_id)
    if reason is not None:
        raise UnsupportedContigError(reason)
```

- [ ] **Step 8: Classify `unsupported_contig` in `errors.py`**

Add the import (extend the existing `from spliceailookup_link.variant import VariantParseError`):

```python
from spliceailookup_link.variant import UnsupportedContigError, VariantParseError
```

In `_classify`, add this branch **before** the `if isinstance(exc, (UpstreamInputError, VariantParseError)):` branch (subclass-first ordering):

```python
    if isinstance(exc, UnsupportedContigError):
        return "unsupported_contig", False, _FALLBACK_TOOL, None
```

In `_envelope_message`, add `"unsupported_contig"` to the safe-message set so the precise reason surfaces:

```python
    if error_code in {
        "build_mismatch",
        "invalid_input",
        "not_found",
        "ref_mismatch",
        "ambiguous",
        "unsupported_contig",
    }:
        return _safe_message(exc)
```

In `_recovery_text`, add (before the final `return`):

```python
    if error_code == "unsupported_contig":
        return (
            "This contig is outside the SpliceAI/Pangolin nuclear scope (chr1-22, X, Y). "
            "Do not retry unchanged. For mitochondrial variants, use gnomad-link "
            "get_mitochondrial_variant; otherwise confirm the variant is on a nuclear "
            "chromosome and re-submit."
        )
```

- [ ] **Step 9: Run the F19 tests**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k f19 tests/unit/test_variant_parse.py -k unsupported_contig -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add spliceailookup_link/variant.py spliceailookup_link/mcp/tools/_common.py spliceailookup_link/mcp/errors.py tests/unit/test_variant_parse.py tests/unit/test_eval_fixes_4.py
git commit -m "feat(F19): fast-fail unsupported (MT/non-nuclear) contigs with unsupported_contig code"
```

---

## Task 2: F18 + F23 — resilient batch runner

**Files:**
- Modify: `spliceailookup_link/config.py`
- Create: `spliceailookup_link/mcp/tools/_batch_runner.py`
- Modify: `spliceailookup_link/mcp/tools/batch.py`
- Test: `tests/unit/test_eval_fixes_4.py`, `tests/unit/test_batch.py`

- [ ] **Step 1: Add the backoff setting (`config.py`)**

In `class Settings`, after `MAX_RETRIES: int = 3`, add:

```python
    # predict_splicing_batch retries a per-item retryable failure (rate_limited /
    # upstream_unavailable) once within the batch; this caps the jittered backoff.
    # Tests set 0 for determinism.
    BATCH_RETRY_BACKOFF_SECONDS: float = 1.0
```

- [ ] **Step 2: Write the failing runner unit test**

Add to `tests/unit/test_eval_fixes_4.py`:

```python
from spliceailookup_link.mcp.tools._batch_runner import run_batch


def _ok_result(variant: str) -> dict:
    return {
        "variant_id": variant,
        "agreement": {"verdict": "concordant_low"},
        "spliceai": {"max_delta_score": 0.1},
        "_telemetry": {"cache": "miss", "upstream_elapsed_ms": 5, "cache_age_s": None},
    }


def _make_predict_fn():
    """Fake predict_fn keyed on the variant string; deterministic, no sleeps."""
    attempts: dict[str, int] = {}

    async def predict_fn(service, *, variant, genome_build, **params):
        attempts[variant] = attempts.get(variant, 0) + 1
        if variant == "OK":
            return _ok_result(variant)
        if variant == "RETRY_OK":
            if attempts[variant] == 1:
                raise RateLimitedError("saturated")
            return _ok_result(variant)
        if variant == "ALWAYS_429":
            raise RateLimitedError("saturated")
        if variant == "ALWAYS_503":
            raise SpliceApiError("upstream 503")
        if variant == "BAD":
            from spliceailookup_link.variant import VariantParseError

            raise VariantParseError("nope")
        raise AssertionError(variant)

    return predict_fn, attempts


async def test_f18_runner_splits_terminal_retryable_and_emits_retry_variants(stub_service) -> None:
    predict_fn, attempts = _make_predict_fn()
    out = await run_batch(
        stub_service,
        variants=["OK", "RETRY_OK", "ALWAYS_503", "ALWAYS_429", "BAD"],
        genome_build="GRCh38",
        params={
            "max_distance": 500,
            "mask": "raw",
            "gene_set": "basic",
            "transcripts": "mane",
            "response_mode": "compact",
            "cross_build_check": True,
            "enforce_deadline": True,
        },
        predict_fn=predict_fn,
        retry_backoff_s=0,
    )
    s = out["summary"]
    assert s["ok"] == 2  # OK + RETRY_OK
    assert s["terminal_failed"] == 1  # BAD
    assert s["retryable_failed"] == 2  # ALWAYS_503 + ALWAYS_429
    assert s["failed"] == s["terminal_failed"] + s["retryable_failed"]
    assert s["retried"] == 3  # RETRY_OK + ALWAYS_503 + ALWAYS_429 each retried once
    assert attempts["RETRY_OK"] == 2 and attempts["ALWAYS_503"] == 2 and attempts["BAD"] == 1
    assert set(out["retry_variants"]) == {"ALWAYS_503", "ALWAYS_429"}


async def test_f23_runner_attaches_rate_budget_to_per_item_rate_limited(stub_service) -> None:
    predict_fn, _ = _make_predict_fn()
    out = await run_batch(
        stub_service,
        variants=["ALWAYS_429"],
        genome_build="GRCh38",
        params={
            "max_distance": 500,
            "mask": "raw",
            "gene_set": "basic",
            "transcripts": "mane",
            "response_mode": "compact",
            "cross_build_check": True,
            "enforce_deadline": True,
        },
        predict_fn=predict_fn,
        retry_backoff_s=0,
    )
    item = out["results"][0]
    assert item["error_code"] == "rate_limited"
    assert item["rate_budget"]["unit"] == "concurrent_requests"
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k "f18 or f23" -v`
Expected: FAIL with `ModuleNotFoundError: ..._batch_runner`.

- [ ] **Step 4: Create `_batch_runner.py`**

```python
"""Resilient server-side scheduler for predict_splicing_batch.

Runs panel items through the upstream concurrency cap so a slow or failing item
never spuriously fails its siblings, retries genuinely-retryable items once within
the batch, and reports terminal vs retryable failures separately so the caller
knows exactly which variants to resubmit (retry_variants). At MAX_CONCURRENCY=2
each item already saturates the cap with its two model calls, so items run one at
a time; the loop scales to ceil(cap/2) concurrent items only if the cap is raised.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

from spliceailookup_link.config import settings
from spliceailookup_link.mcp.errors import McpErrorContext, mcp_tool_error
from spliceailookup_link.mcp.tools._predict import predict_one
from spliceailookup_link.services import SpliceService

PredictFn = Callable[..., Awaitable[dict[str, Any]]]

# Codes worth one in-batch retry (transient contention / upstream blip). Terminal
# codes (bad input, no overlap, wrong build, unsupported contig) never improve.
_RETRYABLE_CODES = {"rate_limited", "upstream_unavailable"}

_VERDICTS = (
    "concordant_high",
    "concordant_moderate",
    "concordant_low",
    "discordant",
    "discordant_subthreshold",
    "incomplete",
)


def _result_max_delta(r: dict[str, Any]) -> float | None:
    candidates = [
        (r.get("spliceai") or {}).get("max_delta_score"),
        (r.get("pangolin") or {}).get("max_delta_score"),
        r.get("spliceai_max"),
        r.get("pangolin_max"),
    ]
    vals = [c for c in candidates if isinstance(c, (int, float))]
    return max(vals) if vals else None


def _success_item(one: dict[str, Any], variant: str) -> dict[str, Any]:
    tele = one.pop("_telemetry")
    one["variant"] = variant
    item_meta: dict[str, Any] = {"cache": tele.get("cache")}
    if tele.get("upstream_elapsed_ms") is not None:
        item_meta["upstream_elapsed_ms"] = tele["upstream_elapsed_ms"]
    if tele.get("cache_age_s") is not None:
        item_meta["cache_age_s"] = tele["cache_age_s"]
    one["_meta"] = item_meta
    return one


def _error_item(
    exc: BaseException, variant: str, genome_build: str
) -> tuple[dict[str, Any], str]:
    """Return (per-item error dict, error_code). Mirrors the single-call envelope."""
    env = mcp_tool_error(
        exc,
        McpErrorContext(
            tool_name="predict_splicing", variant=variant, genome_build=genome_build
        ),
    ).payload
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
    # F23: surface the advertised rate_budget on per-item rate_limited failures.
    if env["_meta"].get("rate_budget"):
        item["rate_budget"] = env["_meta"]["rate_budget"]
    return item, env["error_code"]


async def _run_item(
    predict_fn: PredictFn,
    service: SpliceService,
    *,
    variant: str,
    genome_build: str,
    params: dict[str, Any],
    retry_backoff_s: float,
) -> tuple[dict[str, Any], str, bool]:
    """Score one variant, retrying a retryable failure once.

    Returns (item, kind, retried) where kind is 'ok' | 'terminal' | 'retryable'.
    """
    retried = False
    while True:
        try:
            one = await predict_fn(
                service, variant=variant, genome_build=genome_build, **params
            )
            return _success_item(one, variant), "ok", retried
        except Exception as exc:  # noqa: BLE001 - boundary: classify every per-item fault
            item, code = _error_item(exc, variant, genome_build)
            if code in _RETRYABLE_CODES and not retried:
                retried = True
                if retry_backoff_s:
                    await asyncio.sleep(random.uniform(0, retry_backoff_s))  # noqa: S311
                continue
            kind = "retryable" if code in _RETRYABLE_CODES else "terminal"
            return item, kind, retried


async def run_batch(
    service: SpliceService,
    *,
    variants: list[str],
    genome_build: str,
    params: dict[str, Any],
    ctx: Any = None,
    predict_fn: PredictFn = predict_one,
    retry_backoff_s: float | None = None,
) -> dict[str, Any]:
    """Score a panel resiliently; never let one item fail its siblings."""
    if retry_backoff_s is None:
        retry_backoff_s = settings.BATCH_RETRY_BACKOFF_SECONDS
    results: list[dict[str, Any]] = []
    ok = terminal = retryable = retried_count = 0
    retry_variants: list[str] = []
    total = len(variants)

    for idx, variant in enumerate(variants):
        item, kind, retried = await _run_item(
            predict_fn,
            service,
            variant=variant,
            genome_build=genome_build,
            params=params,
            retry_backoff_s=retry_backoff_s,
        )
        results.append(item)
        if retried:
            retried_count += 1
        if kind == "ok":
            ok += 1
        elif kind == "terminal":
            terminal += 1
        else:
            retryable += 1
            retry_variants.append(variant)
        if ctx is not None:
            await ctx.report_progress(progress=idx + 1, total=total, message=f"{idx + 1}/{total}")

    verdict_counts = {v: 0 for v in _VERDICTS}
    top: dict[str, Any] | None = None
    for r in results:
        verdict = (r.get("agreement") or {}).get("verdict")
        if verdict in verdict_counts:
            verdict_counts[verdict] += 1
        max_delta = _result_max_delta(r)
        if max_delta is not None and (top is None or max_delta > top["max_delta_score"]):
            top = {"variant": r.get("variant"), "max_delta_score": max_delta}

    summary = {
        "ok": ok,
        "failed": terminal + retryable,
        "terminal_failed": terminal,
        "retryable_failed": retryable,
        "retried": retried_count,
        **verdict_counts,
    }
    meta: dict[str, Any] = {}
    if top is not None:
        meta["next_commands"] = [
            {
                "tool": "predict_splicing",
                "arguments": {
                    "variant": top["variant"],
                    "genome_build": genome_build,
                    "response_mode": "full",
                },
            }
        ]
    out: dict[str, Any] = {
        "count": total,
        "results": results,
        "summary": summary,
        "summary_top_variant": top,
        "_meta": meta,
    }
    if retry_variants:
        out["retry_variants"] = retry_variants
    return out
```

- [ ] **Step 5: Run the runner unit tests**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k "f18 or f23" -v`
Expected: PASS.

- [ ] **Step 6: Thin `batch.py` to delegate**

Replace the body of `predict_splicing_batch`'s inner `call()` (everything from `async def call()` through `return await run_mcp_tool(...)`) with a delegation, and delete the now-unused `_result_max_delta` helper at the top of the file. The new tail of `batch.py`:

```python
from spliceailookup_link.mcp.tools._batch_runner import run_batch
from spliceailookup_link.mcp.tools._common import running_as_task
```

(remove the `from spliceailookup_link.mcp.tools._predict import predict_one` import and the `_result_max_delta` function; keep the `_MAX_BATCH` constant and the tool decorator/signature unchanged.)

```python
        async def call() -> dict[str, Any]:
            service = service_factory()
            return await run_batch(
                service,
                variants=variants,
                genome_build=genome_build,
                params={
                    "max_distance": max_distance,
                    "mask": mask,
                    "gene_set": gene_set,
                    "transcripts": transcripts,
                    "response_mode": response_mode,
                    "cross_build_check": cross_build_check,
                    "enforce_deadline": not running_as_task(ctx),
                },
                ctx=ctx,
            )

        return await run_mcp_tool("predict_splicing_batch", call)
```

- [ ] **Step 7: Run the existing batch suite (no regressions) + new summary keys**

Run: `uv run pytest tests/unit/test_batch.py tests/unit/test_eval_fixes_4.py -v`
Expected: PASS. The existing `test_f10_batch_summary_full_histogram` still passes (the new keys are additive; `ok`/`failed`/verdicts unchanged).

- [ ] **Step 8: Add a batch envelope regression test (terminal vs retryable through the facade)**

Add to `tests/unit/test_eval_fixes_4.py`:

```python
async def test_f18_batch_retryable_item_goes_to_retry_variants(mcp, stub_service: StubService) -> None:
    # An always-503 upstream makes every item retryable; with retry exhausted they
    # land in retryable_failed + retry_variants, never miscounted as terminal.
    from spliceailookup_link.config import settings

    stub_service.score_error = SpliceApiError("upstream 503")
    old = settings.BATCH_RETRY_BACKOFF_SECONDS
    settings.BATCH_RETRY_BACKOFF_SECONDS = 0
    try:
        data = structured(
            await mcp.call_tool("predict_splicing_batch", {"variants": ["1-100-A-T"]})
        )
    finally:
        settings.BATCH_RETRY_BACKOFF_SECONDS = old
    assert data["summary"]["retryable_failed"] == 1
    assert data["summary"]["terminal_failed"] == 0
    assert data["retry_variants"] == ["1-100-A-T"]
```

- [ ] **Step 9: Run it**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k f18 -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add spliceailookup_link/config.py spliceailookup_link/mcp/tools/_batch_runner.py spliceailookup_link/mcp/tools/batch.py tests/unit/test_eval_fixes_4.py
git commit -m "feat(F18,F23): resilient batch runner (retry-once, terminal/retryable split, retry_variants, rate_budget)"
```

---

## Task 3: F20 — normalize GENCODE `_NN` IDs

**Files:**
- Modify: `spliceailookup_link/mcp/shaping.py`
- Test: `tests/unit/test_shaping.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_shaping.py`:

```python
from spliceailookup_link.mcp.shaping import shape_spliceai


def _grch37_payload() -> dict:
    return {
        "variant": "1-169519049-T-C",
        "hg": "37",
        "distance": 500,
        "mask": 0,
        "bc": "basic",
        "scores": [
            {
                "g_name": "F5",
                "g_id": "ENSG00000198734.13_12",
                "t_id": "ENST00000367797.9_9",
                "t_priority": "MS",
                "DS_AG": 0.1, "DP_AG": -5,
                "DS_AL": 0.0, "DP_AL": 1,
                "DS_DG": 0.0, "DP_DG": 2,
                "DS_DL": 0.0, "DP_DL": 3,
            }
        ],
    }


def test_f20_grch37_gencode_ids_are_normalized():
    out = shape_spliceai(_grch37_payload(), transcripts="all", response_mode="compact")
    t = out["transcripts"][0]
    assert t["gene_id"] == "ENSG00000198734.13"
    assert t["transcript_id"] == "ENST00000367797.9"


def test_f20_full_mode_preserves_raw_gencode_id():
    out = shape_spliceai(_grch37_payload(), transcripts="all", response_mode="full")
    t = out["transcripts"][0]
    assert t["gene_id"] == "ENSG00000198734.13"
    assert t["gencode_id"] == {
        "gene_id": "ENSG00000198734.13_12",
        "transcript_id": "ENST00000367797.9_9",
    }


def test_f20_grch38_clean_ids_untouched():
    payload = _grch37_payload()
    payload["hg"] = "38"
    payload["scores"][0]["g_id"] = "ENSG00000198734.13"
    payload["scores"][0]["t_id"] = "ENST00000367797.9"
    out = shape_spliceai(payload, transcripts="all", response_mode="full")
    t = out["transcripts"][0]
    assert t["gene_id"] == "ENSG00000198734.13"
    assert "gencode_id" not in t
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_shaping.py -k f20 -v`
Expected: FAIL (ids still carry `_12`/`_9`).

- [ ] **Step 3: Implement in `shaping.py`**

Add `import re` to the imports, and after `_to_int` (around line 82) add:

```python
_ENSEMBL_VERSIONED_RE = re.compile(r"^(ENS[A-Z]+\d+\.\d+)_\d+$")


def _normalize_ensembl_id(value: Any) -> Any:
    """Strip the GRCh37 GENCODE re-version suffix (ENSG...13_12 -> ENSG...13).

    Leaves clean GRCh38 ids and any non-matching value untouched so cross-build
    joins line up. Returns the input unchanged for non-string / non-matching values.
    """
    if not isinstance(value, str):
        return value
    m = _ENSEMBL_VERSIONED_RE.match(value)
    return m.group(1) if m else value
```

In `_shape_spliceai_transcript`, replace the `gene_id` / `transcript_id` lines:

```python
    raw_gid = raw.get("g_id")
    raw_tid = raw.get("t_id")
    gene_id = _normalize_ensembl_id(raw_gid)
    transcript_id = _normalize_ensembl_id(raw_tid)
    out: dict[str, Any] = {
        "gene": raw.get("g_name"),
        "gene_id": gene_id,
        "transcript_id": transcript_id,
        "transcript_priority": _priority_label(raw.get("t_priority")),
        "refseq_ids": raw.get("t_refseq_ids"),
        "strand": raw.get("t_strand"),
        "transcript_type": raw.get("t_type"),
        "delta_scores": deltas,
        "max_delta_score": max_score,
    }
    if mode == "full" and (gene_id != raw_gid or transcript_id != raw_tid):
        out["gencode_id"] = {"gene_id": raw_gid, "transcript_id": raw_tid}
```

In `_shape_pangolin_transcript`, apply the same normalization to its `gene_id` / `transcript_id` keys (it has no `transcript_type`):

```python
    raw_gid = raw.get("g_id")
    raw_tid = raw.get("t_id")
    gene_id = _normalize_ensembl_id(raw_gid)
    transcript_id = _normalize_ensembl_id(raw_tid)
    out: dict[str, Any] = {
        "gene": raw.get("g_name"),
        "gene_id": gene_id,
        "transcript_id": transcript_id,
        "transcript_priority": _priority_label(raw.get("t_priority")),
        "refseq_ids": raw.get("t_refseq_ids"),
        "strand": raw.get("t_strand"),
        "delta_scores": deltas,
        "max_delta_score": max(abs_scores) if abs_scores else None,
    }
    if mode == "full" and (gene_id != raw_gid or transcript_id != raw_tid):
        out["gencode_id"] = {"gene_id": raw_gid, "transcript_id": raw_tid}
```

Note: `_collapse_identical_transcripts` keys on score signature (not id), and `_lift_identity` in `_predict.py` compares `transcript_id` equality — normalizing both models' ids identically keeps that comparison correct (a GRCh37 cross-model match now compares normalized-to-normalized).

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/unit/test_shaping.py -k f20 -v`
Expected: PASS.

- [ ] **Step 5: Run the full shaping suite for regressions**

Run: `uv run pytest tests/unit/test_shaping.py tests/unit/test_predict_shape.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add spliceailookup_link/mcp/shaping.py tests/unit/test_shaping.py
git commit -m "feat(F20): normalize GRCh37 GENCODE _NN-suffixed Ensembl ids (raw kept in full mode)"
```

---

## Task 4: F21 — de-circularize resolve_variant recovery

**Files:**
- Modify: `spliceailookup_link/mcp/errors.py`
- Test: `tests/unit/test_errors.py`, `tests/unit/test_eval_fixes_4.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_eval_fixes_4.py`:

```python
async def test_f21_resolve_invalid_input_recovery_is_not_circular(mcp) -> None:
    data = structured(await mcp.call_tool("resolve_variant", {"variant": "totally not a variant"}))
    assert data["success"] is False
    assert data["error_code"] == "invalid_input"
    # The bug: prose told you to "call resolve_variant" from inside resolve_variant.
    assert "resolve_variant" not in data["recovery"]
    assert "get_server_capabilities" in data["recovery"]


async def test_f21_prediction_invalid_input_still_points_to_resolve(mcp, stub_service: StubService) -> None:
    data = structured(await mcp.call_tool("predict_splicing", {"variant": "totally not a variant"}))
    assert data["error_code"] == "invalid_input"
    assert "resolve_variant" in data["recovery"]  # unchanged for prediction tools
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k f21 -v`
Expected: FAIL (first test: `recovery` contains "resolve_variant").

- [ ] **Step 3: Make `_recovery_text` tool-aware (`errors.py`)**

Change the signature and the `invalid_input` branch of `_recovery_text`:

```python
def _recovery_text(error_code: str, fallback_tool: str | None, *, tool_name: str) -> str:
```

Replace the `invalid_input` branch with a tool-aware version:

```python
    if error_code == "invalid_input":
        if tool_name == "resolve_variant":
            return (
                "The input could not be parsed into any supported variant form. Do not "
                "retry unchanged. Provide CHROM-POS-REF-ALT (chr optional), transcript or "
                "genomic HGVS (e.g. NM_000123.4:c.10A>T), or an rsID (e.g. rs6025); call "
                "get_server_capabilities for accepted formats and examples."
            )
        return (
            "The variant could not be parsed or the upstream rejected it. Do not retry "
            "unchanged. Call resolve_variant to normalize HGVS / rsIDs / loose coordinates "
            "into CHROM-POS-REF-ALT, then retry the prediction."
        )
```

Update the single call site in `mcp_tool_error`:

```python
        "recovery": _recovery_text(error_code, fallback_tool, tool_name=context.tool_name),
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k f21 tests/unit/test_errors.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/errors.py tests/unit/test_eval_fixes_4.py
git commit -m "fix(F21): de-circularize resolve_variant invalid_input recovery prose"
```

---

## Task 5: F22 — `include_hints` opt-out

**Files:**
- Modify: `spliceailookup_link/mcp/tools/combined.py`, `spliceai.py`, `pangolin.py`, `resolve.py`
- Test: `tests/unit/test_eval_fixes_4.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_eval_fixes_4.py`:

```python
async def test_f22_include_hints_false_drops_next_commands_and_see_also(mcp) -> None:
    full = structured(await mcp.call_tool("predict_splicing", {"variant": "chr8-140300616-T-G"}))
    assert "next_commands" in full["_meta"] and "see_also" in full["_meta"]

    lean = structured(
        await mcp.call_tool(
            "predict_splicing", {"variant": "chr8-140300616-T-G", "include_hints": False}
        )
    )
    assert "next_commands" not in lean["_meta"]
    assert "see_also" not in lean["_meta"]
    # Observability + provenance are retained (safety + drift detection).
    assert "request_id" in lean["_meta"]
    assert lean["_meta"]["unsafe_for_clinical_use"] is True


async def test_f22_include_hints_false_on_single_and_resolve(mcp) -> None:
    for tool in ("predict_spliceai", "predict_pangolin"):
        data = structured(await mcp.call_tool(tool, {"variant": "chr8-140300616-T-G", "include_hints": False}))
        assert "next_commands" not in data["_meta"] and "see_also" not in data["_meta"]
    rv = structured(await mcp.call_tool("resolve_variant", {"variant": "chr8-140300616-T-G", "include_hints": False}))
    assert "next_commands" not in rv["_meta"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k f22 -v`
Expected: FAIL (`include_hints` is not an accepted argument → validation_failed, or hints still present).

- [ ] **Step 3: Add the param + gating to `combined.py`**

Add the parameter after `cross_build_check` (before `ctx`):

```python
        include_hints: Annotated[
            bool,
            Field(description="Include _meta.next_commands + see_also chaining hints (default true; set false to trim tokens once you know the workflow)."),
        ] = True,
```

Replace the `meta` construction in `call()`:

```python
            meta: dict[str, Any] = {}
            if include_hints:
                meta["next_commands"] = for_combined(result["variant_id"], genome_build)
                if response_mode != "minimal":
                    meta["see_also"] = see_also_for(
                        result["variant_id"], genome_build, tel["gene"], response_mode
                    )
```

(the rest of the `meta[...]` assignments for cache/upstream/resolution/partial stay as-is, below this block.)

- [ ] **Step 4: Add the param + gating to `spliceai.py`**

Add the same `include_hints` parameter after `cross_build_check` (before `ctx`). Replace the `meta` construction:

```python
            meta: dict[str, Any] = {"cache": tele.cache}
            if include_hints:
                meta["next_commands"] = [
                    cmd("predict_pangolin", variant=prepared.variant_id, genome_build=genome_build)
                ]
                if response_mode != "minimal":
                    meta["see_also"] = see_also_for(
                        prepared.variant_id, genome_build, gene, response_mode
                    )
```

(the subsequent `meta["upstream_elapsed_ms"]` / `cache_ttl_s` / `cache_age_s` / `resolved_from` assignments stay.)

- [ ] **Step 5: Add the param + gating to `pangolin.py`**

Identical pattern; the next_commands target is `predict_spliceai`:

```python
            meta: dict[str, Any] = {"cache": tele.cache}
            if include_hints:
                meta["next_commands"] = [
                    cmd("predict_spliceai", variant=prepared.variant_id, genome_build=genome_build)
                ]
                if response_mode != "minimal":
                    meta["see_also"] = see_also_for(
                        prepared.variant_id, genome_build, gene, response_mode
                    )
```

- [ ] **Step 6: Add the param + gating to `resolve.py`**

Add the parameter after `genome_build`:

```python
        include_hints: Annotated[
            bool,
            Field(description="Include _meta.next_commands (default true; set false to trim tokens)."),
        ] = True,
```

Replace the `_meta` line in `call()`:

```python
            ids = result.get("variant_ids") or [result["variant_id"]]
            result["_meta"] = (
                {"next_commands": after_resolve_many(ids, genome_build)} if include_hints else {}
            )
```

- [ ] **Step 7: Run it to verify it passes**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k f22 -v`
Expected: PASS.

- [ ] **Step 8: Run the tool suite for regressions**

Run: `uv run pytest tests/unit/test_tools.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add spliceailookup_link/mcp/tools/combined.py spliceailookup_link/mcp/tools/spliceai.py spliceailookup_link/mcp/tools/pangolin.py spliceailookup_link/mcp/tools/resolve.py tests/unit/test_eval_fixes_4.py
git commit -m "feat(F22): include_hints opt-out to trim next_commands/see_also on standalone calls"
```

---

## Task 6: F19b — `scoring_supported` flag on resolve_variant

**Files:**
- Modify: `spliceailookup_link/mcp/tools/resolve.py`
- Test: `tests/unit/test_eval_fixes_4.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_eval_fixes_4.py`:

```python
async def test_f19b_resolve_marks_mt_not_scoring_supported(mcp) -> None:
    data = structured(await mcp.call_tool("resolve_variant", {"variant": "MT-3243-A-G"}))
    assert data["success"] is True  # resolve normalizes coordinates; it does not score
    assert data["scoring_supported"] is False
    assert "MT" in data["note"] or "itochondrial" in data["note"]


async def test_f19b_resolve_nuclear_has_no_scoring_supported_flag(mcp) -> None:
    data = structured(await mcp.call_tool("resolve_variant", {"variant": "chr8-140300616-T-G"}))
    assert "scoring_supported" not in data  # additive: only set when NOT supported
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k f19b -v`
Expected: FAIL (`scoring_supported` not present for MT).

- [ ] **Step 3: Implement in `resolve.py`**

Add the import:

```python
from spliceailookup_link.variant import unsupported_contig_reason
```

Add `scoring_supported` to the `_OUTPUT_SCHEMA` properties:

```python
            "scoring_supported": {"type": "boolean"},
```

In `call()`, after `result = await service.resolve(variant, genome_build)` and before building `_meta`, add:

```python
            reason = unsupported_contig_reason(result["variant_id"])
            if reason is not None:
                result["scoring_supported"] = False
                result["note"] = (
                    f"{reason} For mitochondrial variants, use gnomad-link "
                    "get_mitochondrial_variant."
                )
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k f19b -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spliceailookup_link/mcp/tools/resolve.py tests/unit/test_eval_fixes_4.py
git commit -m "feat(F19b): resolve_variant flags non-nuclear contigs as scoring_supported=false"
```

---

## Task 7: F24 — capabilities/reference docs + version bump

**Files:**
- Modify: `spliceailookup_link/mcp/resources.py`
- Modify: `spliceailookup_link/__init__.py`, `pyproject.toml`
- Test: `tests/unit/test_eval_fixes_4.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_eval_fixes_4.py`:

```python
from spliceailookup_link.mcp.resources import get_capabilities_resource, get_reference_resource


def test_f24_capabilities_documents_new_code_and_batch_semantics():
    doc = get_capabilities_resource()
    assert "unsupported_contig" in doc["error_codes"]
    assert "batch_semantics" in doc
    assert "retry_variants" in doc["batch_semantics"]
    assert "include_hints" in doc["response_fields"]
    ref = get_reference_resource()
    assert "unsupported_contig" in ref["error_taxonomy"]["codes"]


def test_f24_capabilities_version_stable_and_12_char():
    a = get_capabilities_resource()
    b = get_capabilities_resource()
    assert a["capabilities_version"] == b["capabilities_version"]
    assert len(a["capabilities_version"]) == 12
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k f24 -v`
Expected: FAIL (`unsupported_contig` / `batch_semantics` not present).

- [ ] **Step 3: Update `resources.py`**

In `get_capabilities_resource`, add `"unsupported_contig"` to the `error_codes` list (after `"build_mismatch"`):

```python
        "error_codes": [
            "invalid_input",
            "not_found",
            "ref_mismatch",
            "ambiguous",
            "build_mismatch",
            "unsupported_contig",
            "rate_limited",
            "validation_failed",
            "upstream_unavailable",
            "internal_error",
        ],
```

Add an `include_hints` entry to `response_fields` (after `see_also`):

```python
            "include_hints": (
                "predict_* and resolve_variant accept include_hints (default true). Set false "
                "to drop _meta.next_commands and see_also once you know the workflow -- trims the "
                "per-call token overhead. predict_splicing_batch already omits per-item hints."
            ),
```

Add a top-level `batch_semantics` block (after the `concurrency` block):

```python
        "batch_semantics": (
            "predict_splicing_batch runs items through the concurrency cap so a slow or failing "
            "item never spuriously rate_limits its siblings, and retries a per-item "
            "rate_limited/upstream_unavailable failure once. summary splits failures into "
            "terminal_failed (invalid_input / not_found / ref_mismatch / build_mismatch / "
            "ambiguous / unsupported_contig -- do not resubmit) and retryable_failed; the "
            "variants in retryable_failed are listed in the top-level retry_variants array for "
            "resubmission (ideally as a background task). summary.retried counts auto-retries."
        ),
```

Add a glossary note for GENCODE normalization in the `score_glossary` (after `resolve_caveat`):

```python
            "ensembl_id_normalization": (
                "gene_id / transcript_id are normalized: the GRCh37 GENCODE re-version suffix "
                "(e.g. ENSG00000198734.13_12 -> ENSG00000198734.13) is stripped so cross-build "
                "joins line up; response_mode='full' preserves the raw value under gencode_id."
            ),
```

In `get_reference_resource`, add `unsupported_contig` to the `codes` taxonomy (after `build_mismatch`):

```python
                "unsupported_contig": {
                    "retryable": False,
                    "when": "variant is on a non-nuclear contig (MT or non-standard) the "
                    "SpliceAI/Pangolin models do not score; use gnomad-link for MT variants",
                },
```

- [ ] **Step 4: Bump the version**

In `spliceailookup_link/__init__.py` line 3: `__version__ = "0.7.0"`.
In `pyproject.toml` line 7: `version = "0.7.0"`.

- [ ] **Step 5: Run it to verify it passes**

Run: `uv run pytest tests/unit/test_eval_fixes_4.py -k f24 -v`
Expected: PASS.

- [ ] **Step 6: Run the capabilities/tools suites for regressions**

Run: `uv run pytest tests/unit/test_tools.py tests/unit/test_eval_fixes.py tests/unit/test_eval_fixes_3.py -v`
Expected: PASS (the `capabilities_version` assertions are stability/length/echo checks, not literal-hash pins, so the doc change does not break them).

- [ ] **Step 7: Commit**

```bash
git add spliceailookup_link/mcp/resources.py spliceailookup_link/__init__.py pyproject.toml tests/unit/test_eval_fixes_4.py
git commit -m "docs(F24): capabilities document unsupported_contig + batch_semantics + include_hints; bump 0.7.0"
```

---

## Task 8: Update server instructions + full verification

**Files:**
- Modify: `spliceailookup_link/mcp/facade.py` (instructions string)
- Verify: whole suite

- [ ] **Step 1: Add a one-line chaining-hint note to `_INSTRUCTIONS` in `facade.py`**

In the `- Chaining:` bullet, append after the existing sentence:

```python
    "steps) and _meta.see_also (cross-server hints for gnomad-link / genereviews-link / "
    "gtex-link). Read the top-level headline first. Set include_hints=false on predict_* / "
    "resolve_variant to drop these once the workflow is known.\n"
```

- [ ] **Step 2: Run the full unit suite**

Run: `uv run pytest tests/unit -q`
Expected: PASS, no failures.

- [ ] **Step 3: Run the line-budget gate**

Run: `make lint-loc`
Expected: PASS — every module < 600 LOC. (Spot-check: `errors.py`, `shaping.py`, `resources.py`, `_batch_runner.py`.)

- [ ] **Step 4: Run the full local CI**

Run: `make ci-local`
Expected: format clean, lint clean, lint-loc clean, mypy clean, tests pass, coverage ≥ 80%.

- [ ] **Step 5: Fix anything CI surfaces, then commit**

If `make format` rewrote files or lint/mypy flagged issues, address them (use the `ci-failure-triage` skill if a check is non-obvious), then:

```bash
git add -A
git commit -m "chore: facade instructions note + ci-local green for consumer-assessment fixes"
```

---

## Self-Review (completed at write time)

**Spec coverage:** F18 → Task 2; F19 → Task 1 (+ F19b resolve flag → Task 6); F20 → Task 3; F21 → Task 4; F22 → Task 5; F23 → Task 2 (folded); F24 → Task 7; instructions/verification → Task 8. All spec sections map to a task.

**Placeholder scan:** No TBD/TODO; every code step shows full code; every run step shows the command + expected outcome.

**Type/name consistency:** `run_batch(service, *, variants, genome_build, params, ctx, predict_fn, retry_backoff_s)` is defined in Task 2 and called identically in `batch.py` and the tests. `unsupported_contig_reason` / `UnsupportedContigError` / `SCORING_CONTIGS` defined in Task 1, used in Tasks 1/6. `_normalize_ensembl_id` defined and used in Task 3. `_recovery_text(..., *, tool_name=...)` signature change and its single call site both updated in Task 4. `include_hints` param name identical across all four tools in Task 5. Error code string `"unsupported_contig"` identical in errors.py, resources.py, and tests.
