"""Capabilities tool plus resource handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp import FastMCP
from mcp.types import Annotations

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
    async def get_server_capabilities() -> dict[str, Any]:
        """Use this first in a cold session to learn the tools, parameters (genome_build, max_distance, mask, gene_set, transcripts, response_mode), score glossary, recommended workflows, error codes, and limitations. Returns ~4kB."""

        async def call() -> dict[str, Any]:
            return get_capabilities_resource()

        return await run_mcp_tool("get_server_capabilities", call)

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
