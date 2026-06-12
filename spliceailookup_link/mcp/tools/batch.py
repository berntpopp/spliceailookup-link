"""predict_splicing_batch: score many variants in one envelope (server-side fan-out)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from pydantic import Field

from spliceailookup_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from spliceailookup_link.mcp.errors import run_mcp_tool
from spliceailookup_link.mcp.provenance import prediction_provenance
from spliceailookup_link.mcp.tools._batch_runner import run_batch
from spliceailookup_link.mcp.tools._common import running_as_task
from spliceailookup_link.services import SpliceService

_MAX_BATCH = 25


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
        """Score a list of variants in ONE call. The server fans out under its concurrency cap and returns a single envelope with per-variant results (+ per-item errors that do not fail the batch) and a summary. Use this for gene panels instead of N predict_splicing calls. Accepts 1-25 variants (more than max_items=25 returns validation_failed, not a truncated result); each item returns about one compact predict_splicing result, so a full batch is ~25x a single compact response, and _meta echoes items_submitted and max_items. Supports MCP background tasks (execution.taskSupport=optional): augment the call with a task to fire-and-continue instead of blocking 15-40s."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            out = await run_batch(
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
                    # A foreground batch enforces the per-item soft deadline; a
                    # background-task batch bypasses it (large/comprehensive panels).
                    "enforce_deadline": not running_as_task(ctx),
                },
                ctx=ctx,
                max_items=_MAX_BATCH,
            )
            out["_meta"]["provenance"] = prediction_provenance(genome_build)
            return out

        return await run_mcp_tool("predict_splicing_batch", call)
