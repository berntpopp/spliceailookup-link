"""predict_pangolin: Pangolin splice gain/loss scores for a variant."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from spliceailookup_link.api import DataNotFoundError
from spliceailookup_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from spliceailookup_link.mcp.errors import BuildMismatchError, McpErrorContext, run_mcp_tool
from spliceailookup_link.mcp.next_commands import cmd
from spliceailookup_link.mcp.shaping import shape_pangolin
from spliceailookup_link.mcp.tools._common import (
    cross_build_probe,
    mask_to_int,
    prepare_variant,
    see_also_for,
)
from spliceailookup_link.services import SpliceService


def register_pangolin_tools(mcp: FastMCP, *, service_factory: Callable[[], SpliceService]) -> None:
    @mcp.tool(
        name="predict_pangolin",
        title="Predict Splicing Impact (Pangolin)",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"prediction", "pangolin"},
    )
    async def predict_pangolin(
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
            Field(description="raw (default) or masked."),
        ] = "raw",
        gene_set: Annotated[
            Literal["basic", "comprehensive"],
            Field(description="basic (default) or comprehensive GENCODE (much slower)."),
        ] = "basic",
        transcripts: Annotated[
            Literal["mane", "all"],
            Field(description="mane (default) or all overlapping transcripts."),
        ] = "mane",
        response_mode: Annotated[
            Literal["compact", "full", "minimal"],
            Field(description="compact (default), full (adds REF/ALT + all-non-zero), or minimal."),
        ] = "compact",
        cross_build_check: Annotated[
            bool,
            Field(description="On not_found, probe the other build to detect a build_mismatch."),
        ] = True,
    ) -> dict[str, Any]:
        """Use this for the Pangolin splice gain/loss scores of a single variant. Pangolin is an independent splice model; agreement with SpliceAI strengthens a prediction, disagreement warrants caution. Use predict_splicing to get both models in one call. Returns ~1-3kB. Note: cold calls take 10-30s."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            prepared = await prepare_variant(service, variant, genome_build)
            try:
                payload, tele = await service.score(
                    model="pangolin",
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
                        model="pangolin",
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
            shaped = shape_pangolin(payload, transcripts=transcripts, response_mode=response_mode)
            gene = shaped["transcripts"][0].get("gene") if shaped["transcripts"] else None
            meta: dict[str, Any] = {
                "next_commands": [
                    cmd("predict_spliceai", variant=prepared.variant_id, genome_build=genome_build)
                ],
                "cache": tele.cache,
            }
            if response_mode != "minimal":
                meta["see_also"] = see_also_for(
                    prepared.variant_id, genome_build, gene, response_mode
                )
            if tele.upstream_elapsed_ms is not None:
                meta["upstream_elapsed_ms"] = tele.upstream_elapsed_ms
            if prepared.resolution is not None:
                meta["resolved_from"] = prepared.resolution.get("raw_input")
            shaped["_meta"] = meta
            return shaped

        return await run_mcp_tool(
            "predict_pangolin",
            call,
            context=McpErrorContext(
                tool_name="predict_pangolin", variant=variant, genome_build=genome_build
            ),
        )
