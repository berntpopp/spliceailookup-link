"""Capabilities tool plus resource handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from mcp.types import Annotations
from pydantic import Field

from spliceailookup_link.config import settings
from spliceailookup_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from spliceailookup_link.mcp.errors import run_mcp_tool
from spliceailookup_link.mcp.resources import (
    RESEARCH_USE_NOTICE,
    get_capabilities_resource,
    get_citations_resource,
    get_reference_resource,
    get_usage_resource,
)
from spliceailookup_link.services import SpliceService

_RESOURCE_ANNOTATIONS = Annotations(audience=["assistant"], priority=1.0)


def register_metadata_tools(mcp: FastMCP, *, service_factory: Callable[[], SpliceService]) -> None:
    @mcp.tool(
        name="get_server_capabilities",
        title="Get spliceailookup-link Capabilities",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"metadata"},
    )
    async def get_server_capabilities(
        detail: Annotated[
            Literal["full", "lean"],
            Field(
                description="full (default, complete doc) or lean (tool list + hash + glossary; params by reference)."
            ),
        ] = "full",
    ) -> dict[str, Any]:
        """Use this first in a cold session to learn the tools, parameters, score glossary, recommended workflows, error codes, and limitations. detail='lean' returns a trimmed doc (tool list + verdicts + error codes + capabilities_version) that omits per-parameter prose already in the tool schemas. Full ~4kB, lean ~1-2kB."""

        async def call() -> dict[str, Any]:
            return get_capabilities_resource(detail=detail)

        return await run_mcp_tool("get_server_capabilities", call)

    @mcp.tool(
        name="warmup",
        title="Warm Up Upstream Scoring Containers",
        annotations=READ_ONLY_OPEN_WORLD,
        tags={"ops"},
    )
    async def warmup(
        genome_build: Annotated[
            Literal["GRCh37", "GRCh38"],
            Field(description="Build whose scoring containers to warm. GRCh38 default."),
        ] = "GRCh38",
        mask: Annotated[
            Literal["raw", "masked", "both"],
            Field(
                description="Which mask path(s) to warm: raw (default), masked, or both "
                "(warms raw and masked per model in one call)."
            ),
        ] = "raw",
    ) -> dict[str, Any]:
        """Pre-warm the SpliceAI + Pangolin Cloud Run containers before a burst so the first real call does not eat the 10-40s cold start. Warms the (basic gene_set, chosen mask) path per model; pass mask='both' to warm raw and masked together. Cloud Run scales per-instance, so other param combos or concurrent calls may still cold-start and warmth decays after minutes idle. Returns per-(model,mask) elapsed_ms, coverage, and stay_warm_estimate_s. Returns <1kB."""

        async def call() -> dict[str, Any]:
            service = service_factory()
            detail: dict[str, Any] = {}
            if mask == "both":
                # Suffix keys per mask only when warming both, so the single-mask
                # path keeps its existing {spliceai, pangolin} detail shape.
                for mask_name, mask_int in (("raw", 0), ("masked", 1)):
                    per_model = await service.warmup(genome_build, mask_int)
                    for model_name, d in per_model.items():
                        detail[f"{model_name}_{mask_name}"] = d
            else:
                detail = await service.warmup(genome_build, 1 if mask == "masked" else 0)
            warmed = all(d.get("status") == "ok" for d in detail.values())
            return {
                "warmed": warmed,
                "genome_build": genome_build,
                "detail": detail,
                "coverage": {
                    "models": ["spliceai", "pangolin"],
                    "mask": mask,
                    "gene_set": "basic",
                },
                "stay_warm_estimate_s": settings.WARMUP_STAY_WARM_ESTIMATE_SECONDS,
                "note": (
                    "Warms the (mask, basic gene_set) path per model. Cloud Run "
                    "autoscales per-instance: subsequent calls with other params or under "
                    "concurrency may still cold-start, and warmth decays after minutes idle. "
                    "stay_warm_estimate_s is a conservative estimate, not a guarantee. "
                    "For a guaranteed-cold first call, prefer a background task."
                ),
            }

        return await run_mcp_tool("warmup", call)

    @mcp.resource(
        "spliceailookup://capabilities",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def capabilities_resource() -> dict[str, Any]:
        return get_capabilities_resource()

    @mcp.resource("spliceailookup://usage", annotations=_RESOURCE_ANNOTATIONS)
    def usage_resource() -> str:
        return get_usage_resource()

    @mcp.resource(
        "spliceailookup://reference",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def reference_resource() -> dict[str, Any]:
        return get_reference_resource()

    @mcp.resource(
        "spliceailookup://research-use",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def research_use_resource() -> dict[str, Any]:
        return {"notice": RESEARCH_USE_NOTICE}

    @mcp.resource(
        "spliceailookup://citations",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def citations_resource() -> dict[str, Any]:
        return get_citations_resource()
