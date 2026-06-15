"""resolve_variant: normalize HGVS / rsID / loose coordinates to CHROM-POS-REF-ALT."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from spliceailookup_link.config import settings
from spliceailookup_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from spliceailookup_link.mcp.errors import McpErrorContext, run_mcp_tool
from spliceailookup_link.mcp.next_commands import after_resolve_many
from spliceailookup_link.mcp.schema_relax import relax_output_schema
from spliceailookup_link.mcp.tools._diagnose import check_ref as run_ref_check
from spliceailookup_link.services import SpliceService
from spliceailookup_link.variant import unsupported_contig_reason

_OUTPUT_SCHEMA = relax_output_schema(
    {
        "type": "object",
        "properties": {
            "variant_id": {"type": ["string", "null"]},
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
            "ref_validated": {"type": ["boolean", "null"]},
            "ref_warning": {"type": ["string", "null"]},
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
        variant_id: Annotated[
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
        check_ref: Annotated[
            bool,
            Field(
                description="Validate a coordinate REF against the requested build (one Ensembl "
                "lookup) and return a ref_warning on mismatch (default true; set false to skip)."
            ),
        ] = True,
        include_hints: Annotated[
            bool,
            Field(
                description="Include _meta.next_commands (default true; set false to trim tokens)."
            ),
        ] = True,
    ) -> dict[str, Any]:
        """Use this when the caller's variant is HGVS, an rsID, or loosely formatted, and you need the canonical CHROM-POS-REF-ALT that the prediction tools require. Coordinate inputs are normalized locally; HGVS/rsIDs are resolved via Ensembl VEP, which also returns the most-severe consequence and gene symbol. Then call predict_splicing. Returns <1kB. Coordinate inputs are normalized; by default the REF base is also checked against the requested build (one Ensembl lookup) and a ref_warning + ref_validated:false is returned on mismatch (set check_ref=false to skip). HGVS/rsIDs are resolved and validated via Ensembl VEP."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            result = await service.resolve(variant_id, genome_build)
            # All candidates of an ambiguous result share a locus, so the contig
            # check on the first id is representative (and never None).
            ids = result.get("variant_ids") or [result["variant_id"]]
            reason = unsupported_contig_reason(ids[0])
            if reason is not None:
                result["scoring_supported"] = False
                result["note"] = (
                    f"{reason} For mitochondrial variants, use gnomad-link "
                    "get_mitochondrial_variant."
                )
            if result.get("ambiguous"):
                # D3: force the caller to pick from variant_ids[] rather than
                # silently inheriting the first allele via the singular variant_id.
                result["variant_id"] = None
            # D1/C5: coordinate inputs get a soft REF check (warn, never block).
            if (
                check_ref
                and settings.PREFLIGHT_REF_CHECK_ENABLED
                and reason is None
                and not result.get("ambiguous")
                and result.get("input_kind") == "coordinate"
                and result.get("variant_id")
            ):
                verdict = await run_ref_check(
                    service, variant_id=result["variant_id"], requested_build=genome_build
                )
                if verdict.status == "match":
                    result["ref_validated"] = True
                elif verdict.status == "mismatch":
                    result["ref_validated"] = False
                    warning = (
                        f"REF '{verdict.observed_ref}' does not match the {genome_build} "
                        f"reference base '{verdict.requested_base}' at "
                        f"{verdict.chrom}:{verdict.pos}; predict_* will reject this as "
                        "ref_mismatch. Re-check the allele or genome_build."
                    )
                    if verdict.other_build:
                        warning += (
                            f" (REF matches the {verdict.other_build} reference base; set "
                            f"genome_build={verdict.other_build} if that build was intended.)"
                        )
                    result["ref_warning"] = warning
                # inconclusive / skip: leave ref_validated unset (do not claim a check
                # we could not perform).
            result["_meta"] = (
                {"next_commands": after_resolve_many(ids, genome_build)} if include_hints else {}
            )
            return result

        return await run_mcp_tool(
            "resolve_variant",
            call,
            context=McpErrorContext(
                tool_name="resolve_variant",
                variant=variant_id,
                genome_build=genome_build,
                query=variant_id,
            ),
        )
