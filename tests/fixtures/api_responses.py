"""Canned upstream payloads captured from the live SpliceAI Lookup + Ensembl VEP APIs.

Trimmed from real responses for chr8-140300616-T-G (TRAPPC9) and
NM_001089.3:c.875A>T (ABCA3). Used to mock httpx in unit tests so they stay
deterministic and offline.
"""

from __future__ import annotations

from typing import Any

# SpliceAI success (one MANE Select transcript shown; real responses repeat per transcript).
SPLICEAI_TRAPPC9: dict[str, Any] = {
    "variant": "8-140300616-T-G",
    "hg": "38",
    "bc": "basic",
    "distance": 500,
    "mask": 0,
    "genomeVersion": "38",
    "chrom": "8",
    "pos": 140300616,
    "ref": "T",
    "alt": "G",
    "scores": [
        {
            "DS_AG": "0.04",
            "DS_AL": "0.83",
            "DS_DG": "0.00",
            "DS_DL": "0.62",
            "DP_AG": -32,
            "DP_AL": -2,
            "DP_DG": 66,
            "DP_DL": -147,
            "DS_AG_REF": "0.01",
            "DS_AL_REF": "0.83",
            "DS_DG_REF": "0.00",
            "DS_DL_REF": "0.91",
            "DS_AG_ALT": "0.06",
            "DS_AL_ALT": "0.00",
            "DS_DG_ALT": "0.00",
            "DS_DL_ALT": "0.30",
            "SCORES_FOR_INSERTED_BASES": [],
            "g_id": "ENSG00000167632.19",
            "g_name": "TRAPPC9",
            "t_id": "ENST00000438773.4",
            "t_priority": "MS",
            "t_refseq_ids": ["NM_001160372.4"],
            "t_strand": "-",
            "t_type": "protein_coding",
            "EXON_STARTS": [139727725, 140300469],
            "EXON_ENDS": [139731228, 140300614],
            "CDS_START": 139731061,
            "CDS_END": 140451373,
        }
    ],
    "source": "spliceai",
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
                "status": "frameshift",
                "size_is_coding": True,
                "introduces_stop_codon": True,
            }
        ]
    },
}

PANGOLIN_TRAPPC9: dict[str, Any] = {
    "variant": "8-140300616-T-G",
    "hg": "38",
    "bc": "basic",
    "distance": 500,
    "mask": "False",
    "genomeVersion": "38",
    "chrom": "8",
    "pos": 140300616,
    "ref": "T",
    "alt": "G",
    "scores": [
        {
            "DS_SG": "0.29",
            "DS_SL": "-0.85",
            "DP_SG": 34,
            "DP_SL": -2,
            "SG_REF": "0.06",
            "SG_ALT": "0.35",
            "SL_REF": "0.90",
            "SL_ALT": "0.05",
            "g_id": "ENSG00000167632.19",
            "g_name": "TRAPPC9",
            "t_id": "ENST00000438773.4",
            "t_priority": "MS",
            "t_refseq_ids": ["NM_001160372.4"],
            "t_strand": "-",
            "t_type": "protein_coding",
        }
    ],
    "source": "pangolin:model:cache",
    "allNonZeroScores": [{"pos": 140300469, "SL_REF": "0.92", "SL_ALT": "0.65"}],
    "allNonZeroScoresStrand": "-",
    "allNonZeroScoresTranscriptId": "ENST00000438773.4",
}

# Upstream "errors" are HTTP 200 bodies carrying an `error` string.
SPLICEAI_PARSE_ERROR: dict[str, Any] = {
    "variant": "notavariant",
    "hg": "38",
    "distance": "50",
    "mask": "0",
    "source": "spliceai",
    "error": "Unable to parse variant: notavariant",
}

SPLICEAI_NO_SCORES: dict[str, Any] = {
    "variant": "6-31740453-G-T",
    "hg": "37",
    "distance": "50",
    "mask": "0",
    "source": "spliceai",
    "error": (
        "The SpliceAI model did not return any scores for 6-31740453-G-T. This may be "
        "because the variant does not overlap any exons or introns defined by the "
        "GENCODE 'basic' annotation."
    ),
}

# Ensembl VEP HGVS resolution (NM_001089.3:c.875A>T -> ABCA3 on chr16).
VEP_ABCA3: list[dict[str, Any]] = [
    {
        "input": "NM_001089.3:c.875A>T",
        "id": "NM_001089.3:c.875A>T",
        "seq_region_name": "16",
        "start": 2317763,
        "end": 2317763,
        "allele_string": "A/T",
        "strand": -1,
        "vcf_string": "16-2317763-T-A",
        "most_severe_consequence": "missense_variant",
        "assembly_name": "GRCh38",
        "transcript_consequences": [{"gene_symbol": "ABCA3", "gene_id": "ENSG00000167972"}],
    }
]
