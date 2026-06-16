"""Hand-authored FastMCP facade for spliceailookup-link."""

from __future__ import annotations

from collections.abc import Callable

from fastmcp import FastMCP

from spliceailookup_link.mcp.errors import install_validation_error_handler
from spliceailookup_link.mcp.resources import RESEARCH_USE_NOTICE
from spliceailookup_link.mcp.tools import register_splice_tools
from spliceailookup_link.services import SpliceService

_INSTRUCTIONS = (
    "SpliceAI Lookup Link predicts the splicing impact of genetic variants using the "
    "Broad SpliceAI Lookup backends (SpliceAI + Pangolin + the SpliceAI-10k consequence "
    "calculator).\n"
    "- One-call answer: predict_splicing for CHROM-POS-REF-ALT (GRCh38 default); it runs "
    "both models and reports whether they agree.\n"
    "- HGVS / rsID / loose input: resolve_variant first (or just pass it to predict_*; "
    "HGVS and rsIDs are auto-resolved via Ensembl VEP), then predict_splicing.\n"
    "- Single model: predict_spliceai (set include_consequence=true for exon-skipping / "
    "frameshift predictions) or predict_pangolin.\n"
    "- Scores: acceptor/donor (SpliceAI) or splice (Pangolin) gain/loss deltas in 0-1 with "
    "a nt position; Δ>=0.5 is commonly high-confidence, 0.2-0.5 moderate.\n"
    "- Options: genome_build (GRCh37|GRCh38), max_distance (default 500), mask (raw|masked), "
    "gene_set (basic|comprehensive; comprehensive is much slower), transcripts (mane|all), "
    "response_mode (minimal|compact|standard|full).\n"
    "- Chaining: every response carries _meta.next_commands (ready-to-call {tool, arguments} "
    "steps) and _meta.see_also (cross-server hints for gnomad-link / genereviews-link / "
    "gtex-link). Read the top-level headline first. Set include_hints=false on predict_* / "
    "resolve_variant to drop these once the workflow is known.\n"
    "- All tools are read-only, idempotent, and safe to auto-call (no data side effects).\n"
    "- Upstream is interactive-use-only and rate-limited; calls can take 30s+. Discovery: "
    "call get_server_capabilities or read spliceailookup://capabilities. "
    f"{RESEARCH_USE_NOTICE}"
)


def create_spliceai_mcp(*, service_factory: Callable[[], SpliceService]) -> FastMCP:
    """Build the spliceailookup-link MCP server.

    `service_factory` is a lazy callable so the HTTP host can defer to
    `app.state.splice_service` (constructed in the FastAPI lifespan) rather than
    building the service eagerly at import time.
    """
    # Per-tool `task=True` (on the async prediction tools) opts those tools into the
    # 2025-11-25 background-task protocol via Docket. We deliberately do NOT set a
    # server-wide `tasks=True` default, which would force every component -- including
    # the sync resources -- to be task-eligible (and async).
    mcp = FastMCP(
        name="spliceailookup-link",
        instructions=_INSTRUCTIONS,
        mask_error_details=True,
    )
    register_splice_tools(mcp, service_factory=service_factory)
    install_validation_error_handler(mcp)
    return mcp
