"""predict_spliceai: SpliceAI delta scores (+ optional SAI-10k consequence)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from pydantic import Field

from spliceailookup_link.api import DataNotFoundError
from spliceailookup_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from spliceailookup_link.mcp.errors import BuildMismatchError, McpErrorContext, run_mcp_tool
from spliceailookup_link.mcp.next_commands import cmd
from spliceailookup_link.mcp.shaping import shape_spliceai
from spliceailookup_link.mcp.tools._common import (
    cross_build_probe,
    mask_to_int,
    prepare_variant,
    see_also_for,
)
from spliceailookup_link.services import SpliceService


def register_spliceai_tools(mcp: FastMCP, *, service_factory: Callable[[], SpliceService]) -> None:
    @mcp.tool(
        name="predict_spliceai",
        title="Predict Splicing Impact (SpliceAI)",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"prediction", "spliceai"},
        task=True,
    )
    async def predict_spliceai(
        variant: Annotated[
            str,
            Field(
                min_length=1,
                max_length=200,
                description="CHROM-POS-REF-ALT, HGVS, or rsID (HGVS/rsIDs are auto-resolved).",
                examples=["chr8-140300616-T-G", "NM_001089.3(ABCA3):c.875A>T"],
            ),
        ],
        genome_build: Annotated[
            Literal["GRCh37", "GRCh38"],
            Field(description="Reference build. GRCh38 default."),
        ] = "GRCh38",
        max_distance: Annotated[
            int,
            Field(ge=1, le=10000, description="nt window scanned (default 500; larger = slower)."),
        ] = 500,
        mask: Annotated[
            Literal["raw", "masked"],
            Field(description="raw (default; alt-splicing) or masked (variant interpretation)."),
        ] = "raw",
        gene_set: Annotated[
            Literal["basic", "comprehensive"],
            Field(description="basic (default) or comprehensive GENCODE (much slower; may 503)."),
        ] = "basic",
        transcripts: Annotated[
            Literal["mane", "all"],
            Field(description="mane (default, MANE Select) or all overlapping transcripts."),
        ] = "mane",
        include_consequence: Annotated[
            bool,
            Field(description="Include the SAI-10k aberration prediction (exon skipping, etc.)."),
        ] = True,
        response_mode: Annotated[
            Literal["compact", "full", "minimal"],
            Field(description="compact (default), full (adds REF/ALT + exon model), or minimal."),
        ] = "compact",
        cross_build_check: Annotated[
            bool,
            Field(description="On not_found, probe the other build to detect a build_mismatch."),
        ] = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Use this for the SpliceAI delta scores (acceptor/donor gain/loss, each 0-1 with a position) of a single variant, optionally with the SpliceAI-10k consequence prediction (exon skipping / intron retention / frameshift). For a quick raw-vs-masked or single-model question; use predict_splicing to also get Pangolin. Δ>=0.5 is high-confidence. Returns ~1-4kB (full/all larger). Note: cold calls take 10-30s. Supports MCP background tasks (execution.taskSupport=optional): augment the call with a task to fire-and-continue instead of blocking 15-40s."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            if ctx is not None:
                await ctx.report_progress(progress=0, total=2, message="resolving")
            prepared = await prepare_variant(service, variant, genome_build)
            if ctx is not None:
                await ctx.report_progress(progress=1, total=2, message="scoring")
            try:
                payload, tele = await service.score(
                    model="spliceai",
                    build=prepared.genome_build,
                    variant_id=prepared.variant_id,
                    distance=max_distance,
                    mask=mask_to_int(mask),
                    gene_set=gene_set,
                    raw=variant,
                    consequence=prepared.consequence,
                )
            except DataNotFoundError as nf:
                if cross_build_check and prepared.resolution is None:
                    other = await cross_build_probe(
                        service,
                        model="spliceai",
                        requested_build=genome_build,
                        variant_id=prepared.variant_id,
                        distance=max_distance,
                        mask=mask_to_int(mask),
                        gene_set=gene_set,
                    )
                    if other:
                        raise BuildMismatchError(
                            variant_id=prepared.variant_id,
                            inferred_build=other,
                            requested_build=genome_build,
                        ) from nf
                raise
            shaped = shape_spliceai(
                payload,
                transcripts=transcripts,
                response_mode=response_mode,
                include_consequence=include_consequence,
            )
            if prepared.consequence:
                shaped["molecular_consequence"] = prepared.consequence
            gene = shaped["transcripts"][0].get("gene") if shaped["transcripts"] else None
            meta: dict[str, Any] = {
                "next_commands": [
                    cmd("predict_pangolin", variant=prepared.variant_id, genome_build=genome_build)
                ],
                "cache": tele.cache,
            }
            if response_mode != "minimal":
                meta["see_also"] = see_also_for(
                    prepared.variant_id, genome_build, gene, response_mode
                )
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

        return await run_mcp_tool(
            "predict_spliceai",
            call,
            context=McpErrorContext(
                tool_name="predict_spliceai", variant=variant, genome_build=genome_build
            ),
        )
