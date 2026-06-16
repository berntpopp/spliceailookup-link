"""Capabilities, reference, usage, and citation payloads for the MCP server."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from mcp.types import LATEST_PROTOCOL_VERSION as MCP_PROTOCOL_VERSION

from spliceailookup_link.config import settings
from spliceailookup_link.mcp.provenance import data_sources as _data_sources

RESEARCH_USE_NOTICE = (
    "Research use only; not for clinical decision support. Splice predictions are "
    "computational and must be interpreted alongside orthogonal evidence."
)


def _server_version() -> str:
    try:
        return version("spliceailookup-link")
    except PackageNotFoundError:
        return "unknown"


def _capabilities_version(doc: dict[str, Any]) -> tuple[str, int]:
    serialized = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]
    return digest, len(serialized)


_CAPABILITIES_VERSION: str | None = None


def get_capabilities_version() -> str:
    """The full capabilities doc's content hash, computed once and cached.

    Echoed into every response `_meta` so a warm client compares the hash and
    skips re-fetching the capabilities document until it actually changes.
    """
    global _CAPABILITIES_VERSION
    if _CAPABILITIES_VERSION is None:
        _CAPABILITIES_VERSION = get_capabilities_resource()["capabilities_version"]
    return _CAPABILITIES_VERSION


def get_capabilities_resource(detail: str = "full") -> dict[str, Any]:
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
        "tool_safety": {
            "all_tools_read_only": True,
            "idempotent": True,
            "open_world": True,
            "note": (
                "All 7 tools are read-only, idempotent, and open-world "
                "(readOnlyHint/idempotentHint/openWorldHint are also set on every tool "
                "schema). They have no side effects on your data and are safe for a "
                "trusted client to auto-approve."
            ),
        },
        "token_tips": (
            "Once the workflow is known, set include_hints=false on predict_*/"
            "resolve_variant to drop next_commands, see_also, AND the static "
            "capabilities_version from _meta. Use response_mode='minimal' for just the "
            "headline + one decision number (provenance is omitted in minimal). "
            "include_see_also=false keeps next_commands but drops the cross-server hints."
        ),
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
            "Which tool? predict_splicing = BOTH models (default); "
            "predict_spliceai / predict_pangolin = ONE model only.",
        ],
        "parameters": {
            "variant_id": "CHROM-POS-REF-ALT (chr optional), HGVS, or rsID. resolve_variant normalizes it.",
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
            "gene_set": (
                "'basic' (default) = GENCODE v44 basic transcripts (MANE-prioritised) or "
                "'comprehensive' = all GENCODE (much slower). NOTE: 'basic' includes "
                "non-coding genes (e.g. lncRNAs like CCAT2 that have a basic transcript), "
                "not only protein-coding -- a low-scoring lncRNA hit is a real transcript, "
                "not the absence of annotation."
            ),
            "transcripts": "'mane' (default, MANE Select only) or 'all' (every overlapping transcript).",
            "response_mode": "'minimal', 'compact' (default), 'standard', or 'full' (adds REF/ALT raw scores, exon model).",
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
            "aberration_fields": (
                "consequence.aberrations[].status / size_is_coding / introduces_stop_codon "
                "are SAI-10k coding-impact fields, populated only for coding-relevant "
                "aberration classes; absent keys mean upstream did not compute them (not "
                "'false'). Under mask='masked' an empty aberrations list with a non-trivial "
                "delta carries a consequence.note that mask='raw' may reveal a suppressed "
                "aberration."
            ),
            "resolve_caveat": (
                "Coordinate inputs are normalized locally. In predict_*, a wrong REF allele is "
                "caught pre-flight (before the slow scoring call) via an Ensembl reference-base "
                "check and returned as a fast ref_mismatch (not a misleading ~17s not_found, and "
                "never a build_mismatch when the position is valid). resolve_variant runs the same "
                "REF check by default (check_ref=true) and returns ref_validated plus, on "
                "mismatch, a ref_warning -- it normalizes (still returns variant_id) but no longer "
                "silently passes a wrong REF; set check_ref=false to skip the lookup."
            ),
            "ensembl_id_normalization": (
                "gene_id / transcript_id are normalized: the GRCh37 GENCODE re-version suffix "
                "(e.g. ENSG00000198734.13_12 -> ENSG00000198734.13) is stripped so cross-build "
                "joins line up; response_mode='full' preserves the raw value under gencode_id."
            ),
        },
        "error_codes": [
            "invalid_input",
            "not_found",
            "ref_mismatch",
            "ambiguous",
            "build_mismatch",
            "unsupported_contig",
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
            "include_hints": (
                "predict_* and resolve_variant accept include_hints (default true). Set false to "
                "drop _meta.next_commands and see_also once you know the workflow -- trims the "
                "per-call token overhead. predict_splicing_batch already omits per-item hints. "
                "See hint_lifecycle for the recommended per-session pattern."
            ),
            "hint_lifecycle": (
                "next_commands and see_also are designed to be read once. After your first "
                "successful predict_* call in a session, set include_hints=false (and "
                "include_see_also=false) for the remaining calls to cut per-call tokens -- the "
                "workflow does not change within a session. The server is stateless, so the flag "
                "must be re-passed on each call (there is no sticky session default)."
            ),
            "include_see_also": (
                "predict_* accept include_see_also (default true), independent of include_hints: "
                "set it false to KEEP the hot _meta.next_commands chaining path while dropping the "
                "4 cross-server see_also entries -- the bigger per-call token cost."
            ),
            "ref_validated": (
                "resolve_variant only: true when the coordinate REF matched the requested-build "
                "Ensembl reference base; false (with a ref_warning) on mismatch; omitted when the "
                "check was skipped (check_ref=false), inconclusive (Ensembl down), or N/A "
                "(HGVS/rsID, ambiguous, non-nuclear contig)."
            ),
            "v0_9_0_shape": (
                "Every prediction mode exposes the headline number consistently: single-model "
                "results carry top:{class,score,position} + max_delta_score in minimal, compact, "
                "AND full; predict_splicing carries agreement:{verdict, spliceai_max_delta, "
                "pangolin_max_delta} in every mode (the older minimal-only spliceai_max/"
                "pangolin_max names are removed). interpretation.threshold_basis appears only in "
                "response_mode='full' (the band is always present; the glossary is in "
                "spliceailookup://reference). predict_splicing still carries the request params on "
                "the ENVELOPE only (sub-blocks omit them; per-model headlines are full-only); "
                "standalone predict_spliceai / predict_pangolin keep them. A coordinate whose "
                "position exceeds the chromosome length in ALL builds is invalid_input (not "
                "build_mismatch -- no build can score it), rejected locally before any upstream "
                "call. ref_mismatch fallbacks are actionable: the matching build, a REF/ALT swap, "
                "or get_server_capabilities -- never the same wrong coordinate back into "
                "resolve_variant. _meta.rate_budget appears on every prediction success (pacing) "
                "and adds retry_after_s on a rate_limited error. There is no warm_ttl_remaining_s; "
                "use served_warm."
            ),
            "molecular_consequence": (
                "the resolver's (Ensembl VEP) most-severe molecular consequence for HGVS/rsID "
                "inputs, e.g. missense_variant; present only when the variant was resolved via "
                "VEP (not for direct coordinate input). DISTINCT from the top-level `consequence` "
                "object which is the SAI-10k splice-aberration prediction (exon_skipping, "
                "intron_retention, frameshift, etc.)."
            ),
            "observability": (
                "every _meta carries request_id, timing.elapsed_ms, and served_warm "
                "(true on a cache hit or a sub-cold-start upstream answer -- use it to "
                "choose blocking vs a background task); prediction payloads add cache "
                "('hit'|'miss'|'partial') and upstream_elapsed_ms (on a miss). On the lean "
                "path (response_mode='minimal' or include_hints=false) the repetitive "
                "capabilities_version and cache_ttl_s/cache_age_s are dropped to save tokens "
                "(fetch capabilities_version from get_server_capabilities)."
            ),
            "capabilities_version": (
                "stable content hash of this document (+ descriptor_chars), ALSO echoed in "
                "every response's _meta so a warm client compares it and skips re-fetching "
                "the full capabilities until it changes. detail='lean' returns a trimmed doc."
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
            "rate_budget": (
                "_meta.rate_budget appears on every prediction success as "
                "{limit, unit:'concurrent_requests', min_interval_ms} -- the cap is a LOCAL "
                "concurrency semaphore (not a time-windowed quota), and min_interval_ms is the "
                "recommended soft spacing between cache-miss scoring calls so you can pace a burst "
                "instead of discovering the limit by hitting it. On a rate_limited error it adds "
                "remaining:0 and retry_after_s for immediate backoff. Cached responses do not "
                "consume the budget. (remaining=0 is exact for local saturation; for an upstream "
                "429 it is a conservative floor.)"
            ),
        },
        "batch_semantics": (
            "predict_splicing_batch runs items through the concurrency cap so a slow or failing "
            "item never spuriously rate_limits its siblings, and retries a per-item "
            "rate_limited/upstream_unavailable failure once. summary splits failures into "
            "terminal_failed (invalid_input / not_found / ref_mismatch / build_mismatch / "
            "ambiguous / unsupported_contig -- do not resubmit) and retryable_failed; the "
            "variants in retryable_failed are listed in the top-level retry_variants array for "
            "resubmission (ideally as a background task). summary.retried counts auto-retries. "
            "predict_splicing_batch accepts max_items=25 variants; submitting more returns "
            "validation_failed (the cap is enforced, not silently truncated). Each item returns "
            "about one compact predict_splicing result, and the envelope _meta echoes "
            "items_submitted and max_items."
        ),
        "prediction_deadline": (
            "Foreground predict_* calls have a server soft deadline "
            f"({settings.PREDICT_SOFT_DEADLINE_SECONDS}s); exceeding it returns a retryable "
            "upstream_unavailable. Background tasks bypass the deadline -- use them for "
            "comprehensive gene_set / large max_distance."
        ),
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
        "warmth": {
            "scope": "warmup warms the (basic gene_set, chosen mask) path per model.",
            "ttl": "upstream-controlled (Cloud Run idle scale-down, ~minutes); not guaranteed.",
            "caveat": (
                "Cloud Run autoscales per-instance, so a subsequent call with other params or "
                "under concurrency may still cold-start. For a guaranteed-cold first call, "
                "prefer a background task over relying on warmup."
            ),
        },
        "agreement_verdicts": [
            "concordant_high",
            "concordant_moderate",
            "concordant_low",
            "discordant",
            "discordant_subthreshold",
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
            "standard": "per-transcript deltas (same as compact in this server)",
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
        "data_sources": _data_sources(),
    }
    version_hash, chars = _capabilities_version(doc)
    doc["capabilities_version"] = version_hash
    doc["descriptor_chars"] = chars
    if detail == "lean":
        return _lean_capabilities(doc)
    return doc


def _lean_capabilities(full: dict[str, Any]) -> dict[str, Any]:
    """SEP-1576-aligned lean view: tool list + verdicts + codes + hash, params by reference."""
    return {
        "server": full["server"],
        "server_version": full["server_version"],
        "mcp_protocol_version": full["mcp_protocol_version"],
        "research_use_only": True,
        "tool_safety": full["tool_safety"],
        "tools": full["tools"],
        "recommended_workflows": full["recommended_workflows"],
        "agreement_verdicts": full["agreement_verdicts"],
        "interpretation_bands": full["interpretation_bands"],
        "error_codes": full["error_codes"],
        "resources": full["resources"],
        "params_by_reference": (
            "Per-parameter docs live in each tool's input schema and "
            "spliceailookup://reference; omitted here to avoid duplication (SEP-1576). "
            "Call get_server_capabilities(detail='full') for the complete document."
        ),
        "capabilities_version": full["capabilities_version"],
        "descriptor_chars": full["descriptor_chars"],
    }


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
                    "when": "variant could not be parsed / upstream rejected the input shape; "
                    "also when a coordinate's position is out of range (exceeds the chromosome "
                    "length in all supported builds), rejected locally before any upstream call",
                },
                "not_found": {
                    "retryable": False,
                    "when": "well-formed variant but no overlapping transcript for the gene set",
                },
                "ref_mismatch": {
                    "retryable": False,
                    "when": "the coordinate REF does not match the genome reference at that "
                    "position/build (swapped REF/ALT, wrong strand, or a typo). Detected "
                    "pre-flight via an Ensembl reference-base check (fast, before scoring). If "
                    "the REF matches the other build, other_build_hint carries a secondary "
                    "suggestion -- but this stays a ref_mismatch, not a build_mismatch redirect.",
                },
                "ambiguous": {
                    "retryable": False,
                    "when": "input (e.g. an rsID) maps to >1 ALT allele; pick one variant_id "
                    "(see variant_ids / next_commands) and retry",
                },
                "build_mismatch": {
                    "retryable": False,
                    "when": "the coordinate is valid only on the OTHER build -- in range there "
                    "and/or it scores there; set genome_build correctly (the fallback carries the "
                    "inferred build). A position out of range in EVERY build is invalid_input, not "
                    "this; a wrong REF at an in-range position is ref_mismatch.",
                },
                "unsupported_contig": {
                    "retryable": False,
                    "when": "variant is on a non-nuclear contig (MT or non-standard) the "
                    "SpliceAI/Pangolin models do not score; use gnomad-link for MT variants",
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
