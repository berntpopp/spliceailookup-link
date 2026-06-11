"""predict_splicing_batch: score many variants in one envelope (server-side fan-out)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from pydantic import Field

from spliceailookup_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from spliceailookup_link.mcp.errors import McpErrorContext, mcp_tool_error, run_mcp_tool
from spliceailookup_link.mcp.tools._common import see_also_for
from spliceailookup_link.mcp.tools._predict import predict_one
from spliceailookup_link.services import SpliceService

_MAX_BATCH = 25


def register_batch_tools(mcp: FastMCP, *, service_factory: Callable[[], SpliceService]) -> None:
    @mcp.tool(
        name="predict_splicing_batch",
        title="Predict Splicing for Many Variants",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"prediction"},
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
        """Score a list of variants in ONE call. The server fans out under its concurrency cap and returns a single envelope with per-variant results (+ per-item errors that do not fail the batch) and a summary. Use this for gene panels instead of N predict_splicing calls. Returns up to ~25x a single compact result."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            results: list[dict[str, Any]] = []
            ok = failed = 0
            genes: set[str] = set()
            total = len(variants)
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
                    )
                    tel = one.pop("_telemetry")
                    if tel.get("gene"):
                        genes.add(tel["gene"])
                    one["variant"] = variant
                    results.append(one)
                    ok += 1
                except Exception as exc:  # capture per-item, never fail the batch
                    env = mcp_tool_error(
                        exc, McpErrorContext(tool_name="predict_splicing_batch", variant=variant)
                    ).payload
                    results.append(
                        {
                            "variant": variant,
                            "error_code": env["error_code"],
                            "message": env["message"],
                            "retryable": env["retryable"],
                        }
                    )
                    failed += 1
                if ctx is not None:
                    await ctx.report_progress(progress=idx + 1, total=total, message=f"{idx + 1}/{total}")
            concordant_high = sum(
                1 for r in results if r.get("agreement", {}).get("verdict") == "concordant_high"
            )
            first_gene = next(iter(genes), None)
            return {
                "count": total,
                "results": results,
                "summary": {"ok": ok, "failed": failed, "concordant_high": concordant_high},
                "_meta": {"see_also": see_also_for("", genome_build, first_gene, response_mode)},
            }

        return await run_mcp_tool("predict_splicing_batch", call)
