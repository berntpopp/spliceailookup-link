"""predict_splicing: resolve-if-needed + SpliceAI + Pangolin in one call."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from spliceailookup_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from spliceailookup_link.mcp.errors import McpErrorContext, run_mcp_tool
from spliceailookup_link.mcp.shaping import shape_pangolin, shape_spliceai
from spliceailookup_link.mcp.tools._common import mask_to_int, prepare_variant, see_also_for
from spliceailookup_link.services import SpliceService

_HIGH = 0.5
_LOW = 0.2


def _assess_agreement(sai_max: float | None, pang_max: float | None) -> dict[str, Any]:
    """Summarise whether the two independent models agree on impact magnitude."""
    if sai_max is None or pang_max is None:
        return {"verdict": "incomplete", "detail": "one model returned no score"}
    both_high = sai_max >= _HIGH and pang_max >= _HIGH
    both_low = sai_max < _LOW and pang_max < _LOW
    if both_high:
        verdict = "concordant_high"
        detail = "both models predict a strong splicing effect"
    elif both_low:
        verdict = "concordant_low"
        detail = "both models predict little or no splicing effect"
    else:
        verdict = "discordant"
        detail = "models disagree on the magnitude of the splicing effect; interpret with caution"
    return {
        "verdict": verdict,
        "detail": detail,
        "spliceai_max_delta": sai_max,
        "pangolin_max_delta": pang_max,
    }


def register_combined_tools(mcp: FastMCP, *, service_factory: Callable[[], SpliceService]) -> None:
    @mcp.tool(
        name="predict_splicing",
        title="Predict Splicing Impact (SpliceAI + Pangolin)",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"prediction"},
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
    ) -> dict[str, Any]:
        """Use this as the default one-call answer for "what does this variant do to splicing?". It resolves HGVS/rsIDs, runs SpliceAI and Pangolin (two independent models), includes the SpliceAI-10k consequence prediction, and reports whether the models agree. Read the top-level headline first. For a single model use predict_spliceai / predict_pangolin. Returns ~3-6kB. Note: cold calls take 15-40s (two model calls)."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            prepared = await prepare_variant(service, variant, genome_build)
            common: dict[str, Any] = {
                "build": prepared.genome_build,
                "variant_id": prepared.variant_id,
                "distance": max_distance,
                "mask": mask_to_int(mask),
                "gene_set": gene_set,
                "raw": variant,
                "consequence": prepared.consequence,
            }
            gathered: list[Any] = await asyncio.gather(
                service.score(model="spliceai", **common),
                service.score(model="pangolin", **common),
                return_exceptions=True,
            )
            sai_res, pang_res = gathered[0], gathered[1]
            # Both failing the same way (e.g. variant outside any transcript) should
            # surface as a single error envelope, so re-raise the SpliceAI fault.
            if isinstance(sai_res, BaseException) and isinstance(pang_res, BaseException):
                raise sai_res

            result: dict[str, Any] = {
                "variant_id": prepared.variant_id,
                "genome_build": genome_build,
                "max_distance": max_distance,
                "mask": mask,
                "gene_set": gene_set,
            }
            gene: str | None = None
            sai_max = pang_max = None
            consequence = None
            partial: list[str] = []
            teles = []

            if isinstance(sai_res, BaseException):
                partial.append(f"spliceai_failed: {sai_res!s}"[:200])
            else:
                sai_payload, sai_tele = sai_res
                teles.append(sai_tele)
                shaped_sai = shape_spliceai(
                    sai_payload, transcripts=transcripts, response_mode=response_mode
                )
                result["spliceai"] = shaped_sai
                sai_max = shaped_sai.get("max_delta_score")
                consequence = shaped_sai.get("consequence")
                if shaped_sai["transcripts"]:
                    gene = shaped_sai["transcripts"][0].get("gene")

            if isinstance(pang_res, BaseException):
                partial.append(f"pangolin_failed: {pang_res!s}"[:200])
            else:
                pang_payload, pang_tele = pang_res
                teles.append(pang_tele)
                shaped_pang = shape_pangolin(
                    pang_payload, transcripts=transcripts, response_mode=response_mode
                )
                result["pangolin"] = shaped_pang
                pang_max = shaped_pang.get("max_delta_score")
                if gene is None and shaped_pang["transcripts"]:
                    gene = shaped_pang["transcripts"][0].get("gene")

            if consequence:
                result["consequence"] = consequence
            result["agreement"] = _assess_agreement(sai_max, pang_max)
            result["headline"] = _combined_headline(
                gene, genome_build, sai_max, pang_max, consequence
            )

            meta: dict[str, Any] = {
                "see_also": see_also_for(prepared.variant_id, genome_build, gene)
            }
            caches = [t.cache for t in teles]
            if caches:
                meta["cache"] = (
                    "hit"
                    if all(c == "hit" for c in caches)
                    else "miss"
                    if all(c == "miss" for c in caches)
                    else "partial"
                )
                ups = [t.upstream_elapsed_ms for t in teles if t.upstream_elapsed_ms is not None]
                if ups:
                    meta["upstream_elapsed_ms"] = max(ups)
            if prepared.resolution is not None:
                meta["resolved_from"] = prepared.resolution.get("raw_input")
                meta["resolved_consequence"] = prepared.consequence
            if partial:
                meta["partial"] = partial
            result["_meta"] = meta
            return result

        return await run_mcp_tool(
            "predict_splicing",
            call,
            context=McpErrorContext(
                tool_name="predict_splicing", variant=variant, genome_build=genome_build
            ),
        )


def _combined_headline(
    gene: str | None,
    build: str,
    sai_max: float | None,
    pang_max: float | None,
    consequence: dict[str, Any] | None,
) -> str:
    gene_label = gene or "variant"
    parts: list[str] = []
    if sai_max is not None:
        parts.append(f"SpliceAI Δ={sai_max:.2f}")
    if pang_max is not None:
        parts.append(f"Pangolin Δ={pang_max:.2f}")
    scores = "; ".join(parts) if parts else "no scores"
    aberr = None
    if consequence and consequence.get("aberrations"):
        aberr = (consequence["aberrations"][0] or {}).get("type")
    tail = f"; predicted {aberr.replace('_', ' ')}" if aberr else ""
    if sai_max is not None and pang_max is not None:
        agree = "agree" if (sai_max >= _HIGH) == (pang_max >= _HIGH) else "disagree"
        verdict = f"; models {agree}"
    else:
        verdict = ""
    return f"{gene_label} ({build}): {scores}{verdict}{tail}."
