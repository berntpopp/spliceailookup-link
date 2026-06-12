"""predict_splicing_batch: score many variants in one envelope (server-side fan-out)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from pydantic import Field

from spliceailookup_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from spliceailookup_link.mcp.errors import McpErrorContext, mcp_tool_error, run_mcp_tool
from spliceailookup_link.mcp.tools._common import running_as_task
from spliceailookup_link.mcp.tools._predict import predict_one
from spliceailookup_link.services import SpliceService

_MAX_BATCH = 25


def _result_max_delta(r: dict[str, Any]) -> float | None:
    candidates = [
        (r.get("spliceai") or {}).get("max_delta_score"),
        (r.get("pangolin") or {}).get("max_delta_score"),
        r.get("spliceai_max"),
        r.get("pangolin_max"),
    ]
    vals = [c for c in candidates if isinstance(c, (int, float))]
    return max(vals) if vals else None


def register_batch_tools(mcp: FastMCP, *, service_factory: Callable[[], SpliceService]) -> None:
    @mcp.tool(
        name="predict_splicing_batch",
        title="Predict Splicing for Many Variants",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"prediction"},
        task=True,
    )
    async def predict_splicing_batch(
        variants: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=_MAX_BATCH,
                description=f"1-{_MAX_BATCH} variants (CHROM-POS-REF-ALT / HGVS / rsID).",
            ),
        ],
        genome_build: Annotated[
            Literal["GRCh37", "GRCh38"],
            Field(description="Build. GRCh38 default."),
        ] = "GRCh38",
        max_distance: Annotated[int, Field(ge=1, le=10000)] = 500,
        mask: Annotated[Literal["raw", "masked"], Field()] = "raw",
        gene_set: Annotated[Literal["basic", "comprehensive"], Field()] = "basic",
        transcripts: Annotated[Literal["mane", "all"], Field()] = "mane",
        response_mode: Annotated[Literal["compact", "full", "minimal"], Field()] = "compact",
        cross_build_check: Annotated[bool, Field()] = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Score a list of variants in ONE call. The server fans out under its concurrency cap and returns a single envelope with per-variant results (+ per-item errors that do not fail the batch) and a summary. Use this for gene panels instead of N predict_splicing calls. Returns up to ~25x a single compact result. Supports MCP background tasks (execution.taskSupport=optional): augment the call with a task to fire-and-continue instead of blocking 15-40s."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            results: list[dict[str, Any]] = []
            ok = failed = 0
            total = len(variants)
            enforce_item_deadline = not running_as_task(ctx)
            for idx, variant in enumerate(variants):
                try:
                    one = await predict_one(
                        service,
                        variant=variant,
                        genome_build=genome_build,
                        max_distance=max_distance,
                        mask=mask,
                        gene_set=gene_set,
                        transcripts=transcripts,
                        response_mode=response_mode,
                        cross_build_check=cross_build_check,
                        enforce_deadline=enforce_item_deadline,
                    )
                    tele = one.pop("_telemetry")
                    one["variant"] = variant
                    item_meta: dict[str, Any] = {"cache": tele.get("cache")}
                    if tele.get("upstream_elapsed_ms") is not None:
                        item_meta["upstream_elapsed_ms"] = tele["upstream_elapsed_ms"]
                    if tele.get("cache_age_s") is not None:
                        item_meta["cache_age_s"] = tele["cache_age_s"]
                    one["_meta"] = item_meta
                    results.append(one)
                    ok += 1
                except Exception as exc:  # capture per-item, never fail the batch
                    # Build the per-item error as a standalone predict_splicing on
                    # this variant so _fallback_for routes recovery to
                    # resolve_variant{variant} (parity with the single-call error),
                    # not the batch-context get_server_capabilities fallback.
                    env = mcp_tool_error(
                        exc,
                        McpErrorContext(
                            tool_name="predict_splicing",
                            variant=variant,
                            genome_build=genome_build,
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
                    results.append(item)
                    failed += 1
                if ctx is not None:
                    await ctx.report_progress(
                        progress=idx + 1, total=total, message=f"{idx + 1}/{total}"
                    )
            verdict_counts = {
                "concordant_high": 0,
                "concordant_moderate": 0,
                "concordant_low": 0,
                "discordant": 0,
                "discordant_subthreshold": 0,
                "incomplete": 0,
            }
            top: dict[str, Any] | None = None
            for r in results:
                verdict = (r.get("agreement") or {}).get("verdict")
                if verdict in verdict_counts:
                    verdict_counts[verdict] += 1
                max_delta = _result_max_delta(r)
                if max_delta is not None and (top is None or max_delta > top["max_delta_score"]):
                    top = {"variant": r.get("variant"), "max_delta_score": max_delta}
            summary = {"ok": ok, "failed": failed, **verdict_counts}
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
            return {
                "count": total,
                "results": results,
                "summary": summary,
                "summary_top_variant": top,
                "_meta": meta,
            }

        return await run_mcp_tool("predict_splicing_batch", call)
