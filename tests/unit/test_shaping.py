"""Tests for SpliceAI/Pangolin response shaping + headlines."""

from __future__ import annotations

from spliceailookup_link.mcp.shaping import (
    pangolin_headline,
    shape_pangolin,
    shape_spliceai,
    spliceai_headline,
)
from tests.fixtures.api_responses import (
    PANGOLIN_TRAPPC9,
    SPLICEAI_MASKED_EMPTY_ABERR,
    SPLICEAI_TRAPPC9,
    SPLICEAI_TRAPPC9_ALL,
    SPLICEAI_TRAPPC9_DUP,
)


def test_transcripts_all_returns_non_mane() -> None:
    # Distinct scores -> not collapsed; both transcripts present incl. non-canonical.
    out = shape_spliceai(SPLICEAI_TRAPPC9_ALL, transcripts="all", response_mode="compact")
    priorities = {t["transcript_priority"] for t in out["transcripts"]}
    assert len(out["transcripts"]) == 2
    assert "non-canonical" in priorities


def test_f7_identical_transcripts_collapse() -> None:
    out = shape_spliceai(SPLICEAI_TRAPPC9_DUP, transcripts="all", response_mode="compact")
    assert len(out["transcripts"]) == 1
    rep = out["transcripts"][0]
    assert sorted(rep["shared_by"]) == ["ENST00000522608.1", "ENST00000999999.1"]


def test_f7_collapse_reduces_serialized_size() -> None:
    import json

    collapsed = shape_spliceai(SPLICEAI_TRAPPC9_DUP, transcripts="all")
    assert len(collapsed["transcripts"]) == 1
    assert "shared_by" in collapsed["transcripts"][0]
    assert json.dumps(collapsed)


def test_f7_max_transcripts_truncates_top_n() -> None:
    out = shape_spliceai(SPLICEAI_TRAPPC9_ALL, transcripts="all", max_transcripts=1)
    assert len(out["transcripts"]) == 1
    assert out["transcripts"][0]["max_delta_score"] == 0.83
    assert out["transcripts_truncated"] == {"kept": 1, "total": 2}


def test_consequence_aberrations_is_stable_path_when_empty() -> None:
    out = shape_spliceai(SPLICEAI_MASKED_EMPTY_ABERR, response_mode="compact")
    assert out["consequence"]["aberrations"] == []
    assert "raw" not in out["consequence"]


def test_full_mode_adds_transcript_info_as_sibling() -> None:
    out = shape_spliceai(SPLICEAI_MASKED_EMPTY_ABERR, response_mode="full")
    assert "aberrations" in out["consequence"]
    assert out["consequence"]["transcript_info"] == {"strand": "-", "exon_count": 23}


def test_populated_aberrations_unchanged() -> None:
    out = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="compact")
    assert out["consequence"]["aberrations"][0]["type"] == "exon_skipping"


def test_shape_spliceai_compact() -> None:
    shaped = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="compact")
    assert shaped["model"] == "SpliceAI"
    assert shaped["variant_id"] == "8-140300616-T-G"
    assert shaped["genome_build"] == "GRCh38"
    assert shaped["max_delta_score"] == 0.83
    t0 = shaped["transcripts"][0]
    assert t0["gene"] == "TRAPPC9"
    assert t0["transcript_priority"] == "MANE Select"
    assert t0["delta_scores"]["acceptor_loss"] == {"score": 0.83, "position": -2}
    # Compact mode omits the heavy fields.
    assert "ref_alt_scores" not in t0
    assert "exon_model" not in t0


def test_shape_spliceai_full_includes_ref_alt_and_exons() -> None:
    shaped = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="full")
    t0 = shaped["transcripts"][0]
    assert t0["ref_alt_scores"]["acceptor_loss"] == {"ref": 0.83, "alt": 0.0}
    assert t0["exon_model"]["cds_start"] == 139731061


def test_shape_spliceai_consequence() -> None:
    shaped = shape_spliceai(SPLICEAI_TRAPPC9, include_consequence=True)
    aberr = shaped["consequence"]["aberrations"][0]
    assert aberr["type"] == "exon_skipping"
    assert aberr["affected_region"]["region_number"] == 10


def test_shape_spliceai_can_drop_consequence() -> None:
    shaped = shape_spliceai(SPLICEAI_TRAPPC9, include_consequence=False)
    assert "consequence" not in shaped


def test_spliceai_headline_mentions_gene_and_class() -> None:
    shaped = shape_spliceai(SPLICEAI_TRAPPC9)
    h = spliceai_headline(shaped)
    assert "TRAPPC9" in h
    assert "acceptor loss" in h
    assert "0.83" in h
    assert "exon skipping" in h


def test_shape_pangolin_signed_loss() -> None:
    shaped = shape_pangolin(PANGOLIN_TRAPPC9)
    assert shaped["model"] == "Pangolin"
    loss = shaped["transcripts"][0]["delta_scores"]["splice_loss"]
    assert loss["score"] == 0.85  # absolute magnitude
    assert loss["signed_score"] == -0.85  # original signed value preserved
    assert shaped["max_delta_score"] == 0.85


def test_pangolin_headline() -> None:
    shaped = shape_pangolin(PANGOLIN_TRAPPC9)
    h = pangolin_headline(shaped)
    assert "TRAPPC9" in h
    assert "splice loss" in h


def test_minimal_mode_is_headline_tier() -> None:
    shaped = shape_spliceai(SPLICEAI_TRAPPC9, transcripts="all", response_mode="minimal")
    assert "transcripts" not in shaped
    assert "delta_scores" not in shaped
    assert shaped["max_delta_score"] == 0.83
    assert shaped["top"]["score"] == 0.83
    assert shaped["top"]["class"] == "acceptor_loss"
    assert shaped["interpretation"]["band"] == "high"
    assert "TRAPPC9" in shaped["headline"]


def test_minimal_smaller_than_compact_single_model() -> None:
    import json

    minimal = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="minimal")
    compact = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="compact")
    assert len(json.dumps(minimal)) < len(json.dumps(compact))


def test_no_scores_headline_safe() -> None:
    empty = {"variant": "1-1-A-T", "hg": "38", "scores": []}
    shaped = shape_spliceai(empty)
    assert "no transcript scores" in shaped["headline"]


def test_interpretation_band_present_compact() -> None:
    out = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="compact")
    assert out["interpretation"]["band"] == "high"
    assert "0.5" in out["interpretation"]["threshold_basis"]


def test_interpretation_band_absent_threshold_in_minimal() -> None:
    out = shape_spliceai(SPLICEAI_TRAPPC9, response_mode="minimal")
    assert out["interpretation"]["band"] == "high"
    assert "threshold_basis" not in out["interpretation"]


# --- F20: GRCh37 GENCODE _NN id normalization ---
def _grch37_payload() -> dict:
    return {
        "variant": "1-169519049-T-C",
        "hg": "37",
        "distance": 500,
        "mask": 0,
        "bc": "basic",
        "scores": [
            {
                "g_name": "F5",
                "g_id": "ENSG00000198734.13_12",
                "t_id": "ENST00000367797.9_9",
                "t_priority": "MS",
                "DS_AG": 0.1,
                "DP_AG": -5,
                "DS_AL": 0.0,
                "DP_AL": 1,
                "DS_DG": 0.0,
                "DP_DG": 2,
                "DS_DL": 0.0,
                "DP_DL": 3,
            }
        ],
    }


def test_f20_grch37_gencode_ids_are_normalized():
    out = shape_spliceai(_grch37_payload(), transcripts="all", response_mode="compact")
    t = out["transcripts"][0]
    assert t["gene_id"] == "ENSG00000198734.13"
    assert t["transcript_id"] == "ENST00000367797.9"


def test_f20_full_mode_preserves_raw_gencode_id():
    out = shape_spliceai(_grch37_payload(), transcripts="all", response_mode="full")
    t = out["transcripts"][0]
    assert t["gene_id"] == "ENSG00000198734.13"
    assert t["gencode_id"] == {
        "gene_id": "ENSG00000198734.13_12",
        "transcript_id": "ENST00000367797.9_9",
    }


def test_f20_grch38_clean_ids_untouched():
    payload = _grch37_payload()
    payload["hg"] = "38"
    payload["scores"][0]["g_id"] = "ENSG00000198734.13"
    payload["scores"][0]["t_id"] = "ENST00000367797.9"
    out = shape_spliceai(payload, transcripts="all", response_mode="full")
    t = out["transcripts"][0]
    assert t["gene_id"] == "ENSG00000198734.13"
    assert "gencode_id" not in t
