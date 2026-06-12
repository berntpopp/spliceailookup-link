"""predict_splicing: resolve-if-needed + SpliceAI + Pangolin in one call."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from pydantic import Field

from spliceailookup_link.config import settings
from spliceailookup_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from spliceailookup_link.mcp.errors import McpErrorContext, run_mcp_tool
from spliceailookup_link.mcp.next_commands import for_combined
from spliceailookup_link.mcp.tools._common import see_also_for
from spliceailookup_link.mcp.tools._predict import predict_one
from spliceailookup_link.services import SpliceService
from spliceailookup_link.services.telemetry import is_served_warm


def register_combined_tools(mcp: FastMCP, *, service_factory: Callable[[], SpliceService]) -> None:
    @mcp.tool(
        name="predict_splicing",
        title="Predict Splicing Impact (SpliceAI + Pangolin)",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"prediction"},
        task=True,
    )
    async def predict_splicing(
        variant: Annotated[
            str,
            Field(
                min_length=1,
                max_length=200,
                description="CHROM-POS-REF-ALT, HGVS, or rsID (HGVS/rsIDs are auto-resolved).",
                examples=["chr8-140300616-T-G", "NM_001089.3(ABCA3):c.875A>T", "6 31740453 G T"],
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
        response_mode: Annotated[
            Literal["compact", "full", "minimal"],
            Field(description="compact (default), full (adds REF/ALT + exon model), or minimal."),
        ] = "compact",
        cross_build_check: Annotated[
            bool,
            Field(description="On not_found, probe the other build to detect a build_mismatch."),
        ] = True,
        include_hints: Annotated[
            bool,
            Field(
                description="Include _meta.next_commands + see_also chaining hints (default true; "
                "set false to trim tokens once you know the workflow)."
            ),
        ] = True,
        include_see_also: Annotated[
            bool,
            Field(
                description="Include _meta.see_also cross-server hints (default true; independent "
                "of include_hints -- set false to keep next_commands but drop the 4 cross-server "
                "entries)."
            ),
        ] = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """BOTH models (SpliceAI + Pangolin) in one call -- the default "what does this variant do to splicing?" answer. Use this as the default one-call answer for "what does this variant do to splicing?". It resolves HGVS/rsIDs, runs SpliceAI and Pangolin (two independent models), includes the SpliceAI-10k consequence prediction, and reports whether the models agree. Read the top-level headline first. For a single model use predict_spliceai / predict_pangolin. Returns ~3-6kB. Note: cold calls take 15-40s (two model calls). Supports MCP background tasks (execution.taskSupport=optional): augment the call with a task to fire-and-continue instead of blocking 15-40s."""

        lean = response_mode == "minimal" or not include_hints

        async def call() -> dict[str, Any]:
            service = service_factory()
            result = await predict_one(
                service,
                variant=variant,
                genome_build=genome_build,
                max_distance=max_distance,
                mask=mask,
                gene_set=gene_set,
                transcripts=transcripts,
                response_mode=response_mode,
                cross_build_check=cross_build_check,
                ctx=ctx,
            )
            tel = result.pop("_telemetry")
            meta: dict[str, Any] = {}
            if include_hints:
                meta["next_commands"] = for_combined(result["variant_id"], genome_build)
                if include_see_also and response_mode != "minimal":
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

        return await run_mcp_tool(
            "predict_splicing",
            call,
            context=McpErrorContext(
                tool_name="predict_splicing", variant=variant, genome_build=genome_build
            ),
            lean_meta=lean,
        )
