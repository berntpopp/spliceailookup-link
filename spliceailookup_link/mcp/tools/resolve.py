"""resolve_variant: normalize HGVS / rsID / loose coordinates to CHROM-POS-REF-ALT."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from spliceailookup_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from spliceailookup_link.mcp.errors import McpErrorContext, run_mcp_tool
from spliceailookup_link.mcp.next_commands import after_resolve_many
from spliceailookup_link.mcp.schema_relax import relax_output_schema
from spliceailookup_link.services import SpliceService
from spliceailookup_link.variant import unsupported_contig_reason

_OUTPUT_SCHEMA = relax_output_schema(
    {
        "type": "object",
        "properties": {
            "variant_id": {"type": "string"},
            "genome_build": {"type": "string"},
            "input_kind": {"type": "string"},
            "source": {"type": "string"},
            "gene_symbol": {"type": ["string", "null"]},
            "consequence": {"type": ["string", "null"]},
            "assembly_name": {"type": ["string", "null"]},
            "ambiguous": {"type": "boolean"},
            "scoring_supported": {"type": "boolean"},
            "variant_ids": {"type": "array", "items": {"type": "string"}},
            "note": {"type": ["string", "null"]},
        },
        "required": ["variant_id", "genome_build"],
    }
)


def register_resolve_tools(mcp: FastMCP, *, service_factory: Callable[[], SpliceService]) -> None:
    @mcp.tool(
        name="resolve_variant",
        title="Resolve Variant to Coordinates",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"resolve"},
        output_schema=_OUTPUT_SCHEMA,
    )
    async def resolve_variant(
        variant: Annotated[
            str,
            Field(
                min_length=1,
                max_length=200,
                description=(
                    "A variant in any supported form: CHROM-POS-REF-ALT (chr optional; "
                    "dash/colon/space delimited), transcript or genomic HGVS "
                    "(e.g. NM_000123.4:c.10A>T or 17:g.43044295G>A), or an rsID (e.g. rs6025)."
                ),
                examples=["NM_001089.3(ABCA3):c.875A>T", "chr8-140300616-T-G", "rs6025"],
            ),
        ],
        genome_build: Annotated[
            Literal["GRCh37", "GRCh38"],
            Field(description="Reference build for resolution and scoring. GRCh38 default."),
        ] = "GRCh38",
        include_hints: Annotated[
            bool,
            Field(description="Include _meta.next_commands (default true; set false to trim tokens)."),
        ] = True,
    ) -> dict[str, Any]:
        """Use this when the caller's variant is HGVS, an rsID, or loosely formatted, and you need the canonical CHROM-POS-REF-ALT that the prediction tools require. Coordinate inputs are normalized locally; HGVS/rsIDs are resolved via Ensembl VEP, which also returns the most-severe consequence and gene symbol. Then call predict_splicing. Returns <1kB. Coordinate inputs are normalized, not validated: a wrong REF allele passes resolution and only fails at prediction time."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            result = await service.resolve(variant, genome_build)
            reason = unsupported_contig_reason(result["variant_id"])
            if reason is not None:
                result["scoring_supported"] = False
                result["note"] = (
                    f"{reason} For mitochondrial variants, use gnomad-link "
                    "get_mitochondrial_variant."
                )
            ids = result.get("variant_ids") or [result["variant_id"]]
            result["_meta"] = (
                {"next_commands": after_resolve_many(ids, genome_build)} if include_hints else {}
            )
            return result

        return await run_mcp_tool(
            "resolve_variant",
            call,
            context=McpErrorContext(
                tool_name="resolve_variant",
                variant=variant,
                genome_build=genome_build,
                query=variant,
            ),
        )
