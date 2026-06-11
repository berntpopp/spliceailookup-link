"""Capabilities, reference, usage, and citation payloads for the MCP server."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from mcp.types import LATEST_PROTOCOL_VERSION as MCP_PROTOCOL_VERSION

from spliceailookup_link.config import settings

RESEARCH_USE_NOTICE = (
    "Research use only; not for clinical decision support. Splice predictions are "
    "computational and must be interpreted alongside orthogonal evidence."
)

# Upstream model provenance surfaced in every response's _meta.
SPLICEAI_MODEL = "SpliceAI (Illumina) via Broad SpliceAI Lookup"
PANGOLIN_MODEL = "Pangolin (Tongji/Invitae) via Broad SpliceAI Lookup"
SAI10K_MODEL = "SpliceAI-10k calculator (consequence prediction)"


def _server_version() -> str:
    try:
        return version("spliceailookup-link")
    except PackageNotFoundError:
        return "unknown"


def _capabilities_version(doc: dict[str, Any]) -> tuple[str, int]:
    serialized = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
    return digest, len(serialized)


def get_capabilities_resource() -> dict[str, Any]:
    doc: dict[str, Any] = {
        "server": "spliceailookup-link",
        "server_version": _server_version(),
        "mcp_protocol_version": MCP_PROTOCOL_VERSION,
        "research_use_only": True,
        "what_it_does": (
            "Predicts the splicing impact of a genetic variant using SpliceAI and "
            "Pangolin, plus the SpliceAI-10k consequence prediction (exon skipping, "
            "intron retention, frameshift). Resolves HGVS / rsIDs to coordinates via "
            "Ensembl VEP."
        ),
        "genome_builds": ["GRCh37", "GRCh38"],
        "default_genome_build": "GRCh38",
        "tools": [
            "get_server_capabilities",
            "resolve_variant",
            "predict_spliceai",
            "predict_pangolin",
            "predict_splicing",
            "predict_splicing_batch",
            "warmup",
        ],
        "recommended_workflows": [
            "CHROM-POS-REF-ALT (GRCh38) -> predict_splicing (runs SpliceAI + Pangolin)",
            "HGVS or rsID -> resolve_variant -> predict_splicing",
            "SpliceAI only -> predict_spliceai (set include_consequence=true for SAI-10k aberrations)",
            "Pangolin only -> predict_pangolin",
            "Many variants (gene panel) -> predict_splicing_batch (one envelope, fans out server-side)",
            "Before a burst -> warmup (pre-warm the cold upstream containers)",
        ],
        "parameters": {
            "variant": "CHROM-POS-REF-ALT (chr optional), HGVS, or rsID. resolve_variant normalizes it.",
            "genome_build": "GRCh37 or GRCh38 (default GRCh38).",
            "max_distance": (
                "nt window scanned around the variant (1-10000, default 500). Larger = slower; "
                "the SAI-10k consequence prediction benefits from a wider window."
            ),
            "mask": (
                "'raw' (default) or 'masked'. Masked hides gains at annotated and losses at "
                "unannotated sites. Use raw for alternative-splicing analysis, masked for "
                "variant interpretation."
            ),
            "gene_set": "'basic' (default, MANE/curated) or 'comprehensive' (all GENCODE; much slower).",
            "transcripts": "'mane' (default, MANE Select only) or 'all' (every overlapping transcript).",
            "response_mode": "'compact' (default), 'full' (adds REF/ALT raw scores, exon model), or 'minimal'.",
        },
        "score_glossary": {
            "spliceai_delta": (
                "acceptor_gain / acceptor_loss / donor_gain / donor_loss, each 0-1 "
                "(probability the variant changes that splice-site class), with a position "
                "offset in nt relative to the variant. A delta >= 0.5 is commonly treated as "
                "a high-confidence prediction; 0.2-0.5 as moderate."
            ),
            "pangolin_delta": (
                "splice_gain / splice_loss, each with a position. Pangolin reports loss as a "
                "negative magnitude upstream; this server reports the absolute delta plus the "
                "direction."
            ),
            "sai10k_consequence": (
                "predicted transcript aberration (e.g. exon_skipping, intron_retention) with "
                "coding status (e.g. frameshift, in-frame) from the SpliceAI-10k calculator. "
                "consequence.aberrations is the stable path in every response_mode (it may be an "
                "empty list under mask='masked', which zeroes the relevant site, even when raw "
                "mode predicts an aberration); full mode adds consequence.transcript_info."
            ),
        },
        "error_codes": [
            "invalid_input",
            "not_found",
            "build_mismatch",
            "rate_limited",
            "validation_failed",
            "upstream_unavailable",
            "internal_error",
        ],
        "response_fields": {
            "headline": "one-line plain-English answer at the top of every prediction payload.",
            "max_delta_score": "the strongest delta score across reported transcripts.",
            "next_commands": (
                "_meta.next_commands is a ready-to-call list of {tool, arguments} steps on every "
                "success and error envelope; execute the first entry to advance."
            ),
            "see_also": (
                "_meta.see_also points at sibling MCP servers for cross-domain follow-up "
                "(gnomad-link for allele frequency, genereviews-link / gtex-link for context, "
                "uniprot-link for protein domains, features, and disease variants). "
                "These are hints, not callable next_commands on this server. Omitted in minimal "
                "mode; collapsed to {server, hint} in compact; full example args in full mode."
            ),
            "molecular_consequence": (
                "the resolver's (Ensembl VEP) most-severe molecular consequence for HGVS/rsID "
                "inputs, e.g. missense_variant; present only when the variant was resolved via "
                "VEP (not for direct coordinate input). DISTINCT from the top-level `consequence` "
                "object which is the SAI-10k splice-aberration prediction (exon_skipping, "
                "intron_retention, frameshift, etc.)."
            ),
            "observability": (
                "every _meta carries request_id and timing.elapsed_ms; prediction payloads add "
                "cache ('hit'|'miss'|'partial') and upstream_elapsed_ms (on a miss)."
            ),
            "capabilities_version": (
                "stable content hash of this document (+ descriptor_chars); a warm client can "
                "compare it and skip re-fetching the full capabilities when unchanged."
            ),
        },
        "limitations": [
            "Upstream is interactive-use-only and rate-limited (several requests/min); calls can "
            "take 30s+. comprehensive gene_set with a large max_distance can time out (HTTP 503).",
            "Indels and complex variants are supported only to the extent the upstream model is.",
            "Allele frequency, ClinVar, gene-disease, and expression context are out of scope -- "
            "delegate to gnomad-link, genereviews-link, and gtex-link respectively.",
            "AlphaMissense / PrimateAI / PromoterAI / CADD shown on the website are not exposed here.",
            RESEARCH_USE_NOTICE,
        ],
        "concurrency": {
            "max_concurrent_requests": settings.MAX_CONCURRENCY,
            "queue_wait_seconds": settings.QUEUE_WAIT_TIMEOUT,
            "guidance": (
                "Fan out at most max_concurrent_requests scoring calls at once; excess calls wait "
                "up to queue_wait_seconds then return a retryable rate_limited error. Results are "
                "cached, so repeat queries are free."
            ),
        },
        "background_execution": {
            "task_support": "optional",
            "task_eligible_tools": [
                "predict_spliceai",
                "predict_pangolin",
                "predict_splicing",
                "predict_splicing_batch",
            ],
            "how_to": (
                "Augment the tools/call with a `task` field (MCP 2025-11-25 Tasks); "
                "the call returns a taskId, poll tasks/get, retrieve via tasks/result."
            ),
            "backend": (
                "in-process (memory://); tasks are session-local, lost on server restart, "
                "and not auth-context-bound -- retrieve results within the session."
            ),
            "recommended_for": "cold predict_* calls (13-40s) and predict_splicing_batch.",
        },
        "agreement_verdicts": [
            "concordant_high",
            "concordant_moderate",
            "concordant_low",
            "discordant",
            "incomplete",
        ],
        "interpretation_bands": {
            "high": "delta>=0.5",
            "moderate": "0.2-0.5",
            "low": ">0-0.2",
            "none": "0",
        },
        "response_mode_tiers": {
            "minimal": "headline + single decision number + band",
            "compact": "per-transcript deltas (default)",
            "full": "compact + REF/ALT raw scores + exon model",
        },
        "transcript_collapse": (
            "transcripts=all collapses byte-identical blocks into one with "
            "shared_by:[ids]; max_transcripts caps and adds transcripts_truncated."
        ),
        "resources": {
            "spliceailookup://capabilities": "this capabilities document",
            "spliceailookup://usage": "compact usage notes",
            "spliceailookup://reference": "error taxonomy, score glossary, and upstream contract",
            "spliceailookup://research-use": "research-use-only notice",
            "spliceailookup://citations": "SpliceAI / Pangolin / SAI-10k / Ensembl citations",
        },
        "data_sources": {
            "spliceai": SPLICEAI_MODEL,
            "pangolin": PANGOLIN_MODEL,
            "sai10k": SAI10K_MODEL,
            "resolver": "Ensembl VEP REST",
        },
    }
    version_hash, chars = _capabilities_version(doc)
    doc["capabilities_version"] = version_hash
    doc["descriptor_chars"] = chars
    return doc


def get_reference_resource() -> dict[str, Any]:
    """Detailed, opt-in contracts referenced from the lean capabilities doc."""
    return {
        "error_taxonomy": {
            "envelope_fields": [
                "success",
                "error_code",
                "message",
                "retryable",
                "recovery_action",
                "fallback_tool",
                "fallback_args",
                "recovery",
            ],
            "recovery_actions": {
                "retry_backoff": "wait, then retry the SAME call",
                "reformulate_input": "fix the variant/fields, same tool",
                "switch_tool": "call fallback_tool with fallback_args, then retry",
            },
            "codes": {
                "invalid_input": {
                    "retryable": False,
                    "when": "variant could not be parsed / upstream rejected the input shape",
                },
                "not_found": {
                    "retryable": False,
                    "when": "well-formed variant but no overlapping transcript for the gene set",
                },
                "build_mismatch": {
                    "retryable": False,
                    "when": "coordinate clearly belongs to the other build; set genome_build correctly",
                },
                "rate_limited": {
                    "retryable": True,
                    "when": "HTTP 429 or local concurrency saturation; back off and retry",
                },
                "validation_failed": {
                    "retryable": False,
                    "when": "arguments failed schema or local guard validation",
                },
                "upstream_unavailable": {
                    "retryable": True,
                    "when": "transient upstream/network fault or a slow comprehensive+distance 503",
                },
                "internal_error": {"retryable": False, "when": "unexpected server fault"},
            },
        },
        "upstream_contract": {
            "scoring_endpoint": "GET {spliceai|pangolin}-{37|38}-...a.run.app/{model}/",
            "params": ["variant", "hg", "distance", "mask", "bc", "raw", "variant_consequence"],
            "error_convention": (
                "Upstream returns HTTP 200 with an `error` string on failure; this server maps "
                "parse errors to invalid_input and no-overlap to not_found."
            ),
            "latency": "0.4s cached, ~13-36s cold; comprehensive + distance=500 can 503.",
        },
        "field_glossary": {
            "delta_score": "0-1 probability the variant alters the splice-site class",
            "position": "nt offset of the affected site relative to the variant (negative = upstream)",
            "transcript_priority": "MANE Select (MS) is the canonical clinical transcript",
            "aberration_type": "SAI-10k predicted transcript-level effect (e.g. exon_skipping)",
        },
        "research_use_only": True,
    }


def get_usage_resource() -> str:
    return (
        "# spliceailookup-link MCP Usage\n\n"
        "Give a variant as CHROM-POS-REF-ALT (GRCh38 by default), HGVS, or rsID. "
        "`predict_splicing` is the one-call entry point: it resolves HGVS/rsIDs, then runs "
        "SpliceAI and Pangolin and returns a merged headline. Use `predict_spliceai` "
        "(set `include_consequence=true` for SAI-10k exon-skipping/frameshift predictions) "
        "or `predict_pangolin` for a single model. Compact responses are the default; pass "
        "`response_mode='full'` for REF/ALT raw scores and the exon model.\n\n"
        f"{RESEARCH_USE_NOTICE}"
    )


def get_citations_resource() -> dict[str, Any]:
    return {
        "spliceai": (
            "Jaganathan K, et al. Predicting Splicing from Primary Sequence with Deep "
            "Learning. Cell. 2019;176(3):535-548. PMID:30661751. doi:10.1016/j.cell.2018.12.015"
        ),
        "pangolin": (
            "Zeng T, Li YI. Predicting RNA splicing from DNA sequence using Pangolin. "
            "Genome Biology. 2022;23:103. PMID:35449021. doi:10.1186/s13059-022-02664-4"
        ),
        "sai10k": (
            "Canson DM, et al. SpliceAI-10k calculator for the prediction of pseudoexonization, "
            "intron retention, and exon deletion. Bioinformatics. 2023. (SpliceAI-10k)"
        ),
        "tool": (
            "SpliceAI Lookup tool, Broad Institute: https://spliceailookup.broadinstitute.org "
            "(github.com/broadinstitute/SpliceAI-lookup)."
        ),
        "resolver": "Ensembl Variant Effect Predictor (VEP) REST API, https://rest.ensembl.org",
        "research_use_only": True,
    }
