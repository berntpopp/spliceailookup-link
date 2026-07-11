"""Guard: spliceailookup-link exposes no externally sourced free-text field.

Classification: `no-untrusted-text` (see genefoundry-router
``docs/conformance/untrusted-text-inventory.yml``, row ``spliceai``). Unlike a
"rich prose" backend (GeneReviews abstracts, HPO term definitions, ClinVar
submitter comments, ...), every MCP tool here returns numeric splice deltas,
positions, and curated/enumerated identifiers. This module is a *regression*
guard, not a fence: it proves the human-readable ``headline`` /
``consequence_summary`` fields are locally synthesized by
``spliceailookup_link/mcp/shaping.py`` from those numbers -- not copied
verbatim from an upstream prose field -- and that no upstream free-text
surface has been (re-)introduced.

Two narrower surfaces were specifically investigated (not just assumed clean)
because they *do* carry upstream-sourced strings, and are recorded here so a
future change that widens either one fails loudly:

1. ``consequence.aberrations[].type`` / minimal-mode ``consequence_summary``
   is copied verbatim from the upstream SpliceAI-10k ``aberration_type``
   field. That field is NOT free text: it is one of a closed set of six
   terminal classes from a published classification algorithm (Canson et al.
   2023), computed deterministically from genomic coordinates by Broad's own
   ``sai10k_predictions.py`` -- the same category as an SO/HGVS term, not
   externally-authored prose. ``KNOWN_ABERRATION_TYPES`` below is that closed
   set, confirmed 2026-07-11 against
   ``github.com/broadinstitute/SpliceAI-lookup`` (docstring of
   ``sai10k_find_aberrations`` in ``google_cloud_run_services/
   sai10k_predictions.py``).
2. ``consequence.error`` is copied verbatim from the upstream
   ``sai10kPredictionsError`` field. Confirmed 2026-07-11 against
   ``google_cloud_run_services/server.py`` (the ``except Exception`` handler
   around the SAI-10k sub-computation): the server explicitly does NOT
   forward exception internals (message, file paths, dict-key names) to the
   client "so internal details ... aren't echoed back through the JSON
   response" -- it always sets this field to the single hardcoded sentinel
   ``"Internal error computing SAI-10k predictions."``. Effectively an enum
   of cardinality one, not a free-prose surface.

The top-level upstream ``error`` field (distinct from ``sai10kPredictionsError``
above) is handled by ``spliceailookup_link/api/scoring_client.py`` by raising a
typed exception *before* ``shape_spliceai``/``shape_pangolin`` ever run, so it
never reaches a tool's success schema; it is out of scope for this
success-payload guard (diagnostic error-envelope text, not a result field).
"""

from __future__ import annotations

from typing import Any

from spliceailookup_link.mcp.shaping import (
    pangolin_headline,
    shape_pangolin,
    shape_spliceai,
    spliceai_headline,
)
from spliceailookup_link.mcp.tools._predict_shape import assess_agreement, combined_headline
from spliceailookup_link.mcp.tools.resolve import _RESULT_SCHEMA as RESOLVE_RESULT_SCHEMA

# Fleet-standard forbidden key names for an externally-authored prose surface
# (GeneReviews-style narrative, HPO/OMIM definitions, curator notes, ...).
FORBIDDEN_FREETEXT_KEYS = {"definition", "description", "summary", "abstract", "notes", "comment"}

# The SpliceAI-10k model's closed vocabulary of terminal aberration classes
# (Canson et al. 2023 flowchart v1.1) -- confirmed against Broad's
# sai10k_predictions.py. `aberration_type` (and therefore consequence_summary
# / consequence.aberrations[].type) can ONLY ever be one of these six tokens.
KNOWN_ABERRATION_TYPES = frozenset(
    {
        "exon_skipping",
        "whole_intron_retention",
        "pseudoexon",
        "increased_exon_inclusion",
        "partial_exon_deletion",
        "partial_intron_retention",
    }
)

# The exact, single sentinel string Broad's server hardcodes for a SAI-10k
# sub-computation failure -- confirmed against server.py; never interpolated.
KNOWN_SAI10K_ERROR_SENTINEL = "Internal error computing SAI-10k predictions."

_RAW_SPLICEAI_TRANSCRIPT: dict[str, Any] = {
    "g_id": "ENSG00000000001.1",
    "g_name": "TESTGENE1",
    "t_id": "ENST00000000001.1",
    "t_priority": "MS",
    "t_refseq_ids": ["NM_000001.1"],
    "t_strand": "+",
    "t_type": "protein_coding",
    "DS_AG": "0.00",
    "DP_AG": 0,
    "DS_AL": "0.83",
    "DP_AL": -2,
    "DS_DG": "0.00",
    "DP_DG": 0,
    "DS_DL": "0.00",
    "DP_DL": 0,
}

_RAW_PANGOLIN_TRANSCRIPT: dict[str, Any] = {
    "g_id": "ENSG00000000001.1",
    "g_name": "TESTGENE1",
    "t_id": "ENST00000000001.1",
    "t_priority": "MS",
    "t_refseq_ids": ["NM_000001.1"],
    "t_strand": "+",
    "DS_SG": "0.10",
    "DP_SG": 5,
    "DS_SL": "-0.75",
    "DP_SL": -10,
}


def _all_keys(obj: Any) -> set[str]:
    """Recursively collect every dict key in a (possibly nested) structure."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _all_keys(item)
    return keys


# --- (a) enumerate every MCP tool's output schema -------------------------------


def test_predict_spliceai_output_has_no_free_text_surface() -> None:
    """predict_spliceai (every response_mode): no key resembles upstream prose."""
    payload = {"variant": "1-1000-A-T", "hg": "38", "scores": [_RAW_SPLICEAI_TRANSCRIPT]}
    for mode in ("minimal", "compact", "standard", "full"):
        shaped = shape_spliceai(payload, response_mode=mode)  # type: ignore[arg-type]
        keys = _all_keys(shaped)
        assert keys.isdisjoint(FORBIDDEN_FREETEXT_KEYS), (
            f"predict_spliceai ({mode}) introduced an unclassified free-text "
            f"field: {keys & FORBIDDEN_FREETEXT_KEYS}"
        )


def test_predict_pangolin_output_has_no_free_text_surface() -> None:
    """predict_pangolin (every response_mode): no key resembles upstream prose."""
    payload = {"variant": "1-1000-A-T", "hg": "38", "scores": [_RAW_PANGOLIN_TRANSCRIPT]}
    for mode in ("minimal", "compact", "standard", "full"):
        shaped = shape_pangolin(payload, response_mode=mode)  # type: ignore[arg-type]
        keys = _all_keys(shaped)
        assert keys.isdisjoint(FORBIDDEN_FREETEXT_KEYS), (
            f"predict_pangolin ({mode}) introduced an unclassified free-text "
            f"field: {keys & FORBIDDEN_FREETEXT_KEYS}"
        )


def test_resolve_variant_output_schema_has_no_free_text_surface() -> None:
    """resolve_variant's declared output schema: curated identifiers/SO terms only."""
    props = set(RESOLVE_RESULT_SCHEMA["properties"])
    assert props.isdisjoint(FORBIDDEN_FREETEXT_KEYS), (
        f"resolve_variant introduced an unclassified free-text field: "
        f"{props & FORBIDDEN_FREETEXT_KEYS}"
    )


def test_combined_agreement_output_has_no_free_text_surface() -> None:
    """assess_agreement's verdict dict (feeds predict_splicing): no prose keys."""
    agreement = assess_agreement(0.83, 0.10)
    keys = _all_keys(agreement)
    assert keys.isdisjoint(FORBIDDEN_FREETEXT_KEYS), (
        f"assess_agreement introduced an unclassified free-text field: "
        f"{keys & FORBIDDEN_FREETEXT_KEYS}"
    )


# --- (b) headline / consequence_summary are server-synthesized, not passthrough --


def test_spliceai_headline_is_pure_function_of_numeric_deltas() -> None:
    """headline is built by the local formatter from numbers; upstream has no text field.

    The synthetic payload carries no upstream prose field of any kind (only
    numeric DS_*/DP_* scores/positions + curated identifiers) -- headline is
    computed purely from those numbers, so there is structurally nothing to
    "pass through". Changing only the delta score changes the synthesized
    sentence proportionally, proving it is not a cached/copied string.
    """
    payload = {"variant": "1-1000-A-T", "hg": "38", "scores": [_RAW_SPLICEAI_TRANSCRIPT]}
    shaped = shape_spliceai(payload, response_mode="compact")
    assert shaped["headline"] == (
        "SpliceAI (GRCh38): TESTGENE1 — strong acceptor loss (Δ=0.83 at -2 bp)."
    )
    assert shaped["headline"] == spliceai_headline(shaped)

    weak_transcript = dict(_RAW_SPLICEAI_TRANSCRIPT, DS_AL="0.15")
    weak_payload = {"variant": "1-1000-A-T", "hg": "38", "scores": [weak_transcript]}
    weak_shaped = shape_spliceai(weak_payload, response_mode="compact")
    assert weak_shaped["headline"] == (
        "SpliceAI (GRCh38): TESTGENE1 — weak acceptor loss (Δ=0.15 at -2 bp)."
    )


def test_pangolin_headline_is_pure_function_of_numeric_deltas() -> None:
    payload = {"variant": "1-1000-A-T", "hg": "38", "scores": [_RAW_PANGOLIN_TRANSCRIPT]}
    shaped = shape_pangolin(payload, response_mode="compact")
    assert shaped["headline"] == (
        "Pangolin (GRCh38): TESTGENE1 — strong splice loss (Δ=0.75 at -10 bp)."
    )
    assert shaped["headline"] == pangolin_headline(shaped)


def test_combined_headline_and_agreement_detail_come_from_fixed_local_table() -> None:
    """predict_splicing's headline + agreement.detail: local verdict table, two floats in.

    ``assess_agreement`` takes only the two numeric max-deltas as input -- it
    has no upstream text parameter to pass through -- and ``combined_headline``
    renders its verdict clause from the module-level ``_VERDICT_CLAUSE``
    lookup table, not any upstream field.
    """
    agreement = assess_agreement(0.83, 0.10)
    assert agreement == {
        "verdict": "discordant",
        "detail": "one model predicts a high-confidence effect and the other does not; "
        "interpret with caution",
        "spliceai_max_delta": 0.83,
        "pangolin_max_delta": 0.10,
    }
    headline = combined_headline("TESTGENE1", "GRCh38", 0.83, 0.10, None, agreement)
    assert headline == "TESTGENE1 (GRCh38): SpliceAI Δ=0.83; Pangolin Δ=0.10; models disagree."


# --- (c) the two upstream-sourced strings that DO exist are provably bounded -----


def test_consequence_summary_is_bounded_to_the_six_known_aberration_classes() -> None:
    """minimal-mode consequence_summary copies aberration_type verbatim -- but that
    field is a closed six-value enum (Canson et al. 2023), not free prose. If a
    future upstream/local change ever produced a value outside the documented
    closed set, this assertion fails loudly.
    """
    payload = {
        "variant": "1-1000-A-T",
        "hg": "38",
        "scores": [_RAW_SPLICEAI_TRANSCRIPT],
        "sai10kPredictions": {
            "aberrations": [
                {
                    "aberration_type": "exon_skipping",
                    "affected_region": {
                        "region_type": "intron",
                        "region_number": 10,
                        "distance_to_boundary": 2,
                        "nearest_boundary": "acceptor",
                    },
                    "status": None,
                }
            ]
        },
    }
    shaped = shape_spliceai(payload, response_mode="minimal")
    assert shaped["consequence_summary"] == "exon_skipping"
    assert shaped["consequence_summary"] in KNOWN_ABERRATION_TYPES
    # The full (non-minimal) shape carries the same enum verbatim in
    # consequence.aberrations[].type -- also bounded to the closed set.
    full_shaped = shape_spliceai(payload, response_mode="compact")
    aberr_type = full_shaped["consequence"]["aberrations"][0]["type"]
    assert aberr_type in KNOWN_ABERRATION_TYPES


def test_consequence_error_is_bounded_to_the_single_hardcoded_sentinel() -> None:
    """consequence.error copies sai10kPredictionsError verbatim -- but Broad's
    server hardcodes that field to ONE literal sentinel string with zero
    interpolation of exception internals (see module docstring for the
    verified upstream source). Any other value here would mean Broad's server
    started forwarding raw exception text, which this test would catch on the
    next fixture-driven integration check.
    """
    payload = {
        "variant": "1-1000-A-T",
        "hg": "38",
        "scores": [_RAW_SPLICEAI_TRANSCRIPT],
        "sai10kPredictionsError": KNOWN_SAI10K_ERROR_SENTINEL,
    }
    shaped = shape_spliceai(payload, response_mode="compact")
    assert shaped["consequence"]["error"] == KNOWN_SAI10K_ERROR_SENTINEL
