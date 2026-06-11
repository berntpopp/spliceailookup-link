"""Tool registration entry points for the spliceailookup-link MCP facade."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP

from spliceailookup_link.mcp.tools.batch import register_batch_tools
from spliceailookup_link.mcp.tools.combined import register_combined_tools
from spliceailookup_link.mcp.tools.metadata import register_metadata_tools
from spliceailookup_link.mcp.tools.pangolin import register_pangolin_tools
from spliceailookup_link.mcp.tools.resolve import register_resolve_tools
from spliceailookup_link.mcp.tools.spliceai import register_spliceai_tools
from spliceailookup_link.services import SpliceService


def register_splice_tools(mcp: FastMCP, *, service_factory: Callable[[], SpliceService]) -> None:
    register_metadata_tools(mcp, service_factory=service_factory)
    register_resolve_tools(mcp, service_factory=service_factory)
    register_spliceai_tools(mcp, service_factory=service_factory)
    register_pangolin_tools(mcp, service_factory=service_factory)
    register_combined_tools(mcp, service_factory=service_factory)
    register_batch_tools(mcp, service_factory=service_factory)
