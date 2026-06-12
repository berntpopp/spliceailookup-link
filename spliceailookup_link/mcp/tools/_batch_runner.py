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
import copy
import random
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from spliceailookup_link.config import GenomeBuild, settings
from spliceailookup_link.mcp.errors import McpErrorContext, mcp_tool_error
from spliceailookup_link.mcp.tools._batch_dedup import build_dedup_plan
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
    agreement = r.get("agreement") or {}
    candidates = [
        (r.get("spliceai") or {}).get("max_delta_score"),
        (r.get("pangolin") or {}).get("max_delta_score"),
        agreement.get("spliceai_max_delta"),
        agreement.get("pangolin_max_delta"),
    ]
    vals = [c for c in candidates if isinstance(c, (int, float))]
    return max(vals) if vals else None


def _success_item(one: dict[str, Any], variant: str, request_id: str) -> dict[str, Any]:
    from spliceailookup_link.services.telemetry import is_served_warm

    tele = one.pop("_telemetry")
    one["variant"] = variant
    item_meta: dict[str, Any] = {
        "request_id": request_id,
        "cache": tele.get("cache"),
        "served_warm": is_served_warm(tele.get("cache"), tele.get("upstream_elapsed_ms")),
    }
    if tele.get("upstream_elapsed_ms") is not None:
        item_meta["upstream_elapsed_ms"] = tele["upstream_elapsed_ms"]
    if tele.get("cache_age_s") is not None:
        item_meta["cache_age_s"] = tele["cache_age_s"]
    one["_meta"] = item_meta
    return one


def _error_item(
    exc: BaseException, variant: str, genome_build: str, request_id: str
) -> tuple[dict[str, Any], str]:
    """Return (per-item error dict, error_code). Mirrors the single-call envelope."""
    env = mcp_tool_error(
        exc,
        McpErrorContext(tool_name="predict_splicing", variant=variant, genome_build=genome_build),
    ).payload
    item: dict[str, Any] = {
        "variant": variant,
        "request_id": request_id,
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
    request_id = uuid.uuid4().hex[:12]
    while True:
        try:
            one = await predict_fn(service, variant=variant, genome_build=genome_build, **params)
            return _success_item(one, variant, request_id), "ok", retried
        except Exception as exc:  # boundary: classify every per-item fault into an envelope
            item, code = _error_item(exc, variant, genome_build, request_id)
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
    genome_build: GenomeBuild,
    params: dict[str, Any],
    ctx: Any = None,
    predict_fn: PredictFn = predict_one,
    retry_backoff_s: float | None = None,
    max_items: int = 25,
) -> dict[str, Any]:
    """Score a panel resiliently; never let one item fail its siblings."""
    if retry_backoff_s is None:
        retry_backoff_s = settings.BATCH_RETRY_BACKOFF_SECONDS
    results: list[dict[str, Any]] = []
    ok = terminal = retryable = retried_count = 0
    retry_variants: list[str] = []
    total = len(variants)

    # W2: resolve all inputs and group by canonical variant_id so a variant
    # submitted twice (e.g. coordinate + its HGVS) is scored once upstream.
    plan = await build_dedup_plan(service, variants, genome_build)
    owner_result: dict[str, dict[str, Any]] = {}
    upstream_calls_saved = 0

    for idx, variant in enumerate(variants):
        canonical = plan.canonical.get(idx)
        if canonical is not None and not plan.is_owner(idx) and canonical in owner_result:
            # Duplicate of an already-scored variant: copy, never re-score.
            base = copy.deepcopy(owner_result[canonical])
            base["variant"] = variant
            item_meta = dict(base.get("_meta") or {})
            item_meta["request_id"] = uuid.uuid4().hex[:12]  # own id for log correlation
            item_meta["cache"] = "deduped"
            item_meta["served_from"] = canonical
            item_meta["served_warm"] = True  # served instantly from a sibling's result
            # No upstream call for this copy -> drop the owner's timing fields.
            item_meta.pop("upstream_elapsed_ms", None)
            item_meta.pop("cache_age_s", None)
            base["_meta"] = item_meta
            results.append(base)
            ok += 1
            upstream_calls_saved += 2  # the two model calls this copy avoided
            if ctx is not None:
                await ctx.report_progress(
                    progress=idx + 1, total=total, message=f"{idx + 1}/{total}"
                )
            continue

        item, kind, retried = await _run_item(
            predict_fn,
            service,
            variant=variant,
            genome_build=genome_build,
            params=params,
            retry_backoff_s=retry_backoff_s,
        )
        results.append(item)
        if canonical is not None and plan.is_owner(idx) and kind == "ok":
            owner_result[canonical] = item
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

    verdict_counts = dict.fromkeys(_VERDICTS, 0)
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
        "unique_variants": plan.unique_count,
        "upstream_calls_saved": upstream_calls_saved,
        **verdict_counts,
    }
    from spliceailookup_link.mcp.errors import rate_budget_snapshot

    meta: dict[str, Any] = {
        "items_submitted": total,
        "max_items": max_items,
        "deduped": {"unique": plan.unique_count, "duplicates": plan.duplicate_count},
        "rate_budget": rate_budget_snapshot(saturated=False),
    }
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
