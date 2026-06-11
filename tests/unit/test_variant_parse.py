"""Tests for variant input parsing/normalization."""

from __future__ import annotations

import pytest

from spliceailookup_link.variant import (
    VariantParseError,
    clean_hgvs,
    normalize_coordinate,
    parse_variant_input,
    split_variant_id,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("chr8-140300616-T-G", "8-140300616-T-G"),
        ("8-140300616-T-G", "8-140300616-T-G"),
        ("6   31740453   G   T", "6-31740453-G-T"),
        ("6\t31740453\tG\tT", "6-31740453-G-T"),
        ("chr8:140300616:T:G", "8-140300616-T-G"),
        ("CHRX-100-a-c", "X-100-A-C"),
        ("MT-150-A-G", "MT-150-A-G"),
    ],
)
def test_normalize_coordinate(raw: str, expected: str) -> None:
    assert normalize_coordinate(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["rs6025", "NM_001089.3:c.875A>T", "not a variant at all", "8-140300616-T", "99-1-A-T"],
)
def test_normalize_coordinate_rejects_non_coordinates(raw: str) -> None:
    assert normalize_coordinate(raw) is None


def test_clean_hgvs_strips_gene_and_protein() -> None:
    assert clean_hgvs("NM_001089.3(ABCA3):c.875A>T (p.Glu292Val)") == "NM_001089.3:c.875A>T"


def test_parse_coordinate() -> None:
    parsed = parse_variant_input("chr8-140300616-T-G")
    assert parsed.kind == "coordinate"
    assert parsed.value == "8-140300616-T-G"


def test_parse_rsid() -> None:
    parsed = parse_variant_input("RS6025")
    assert parsed.kind == "rsid"
    assert parsed.value == "rs6025"


def test_parse_hgvs() -> None:
    parsed = parse_variant_input("NM_001089.3(ABCA3):c.875A>T (p.Glu292Val)")
    assert parsed.kind == "hgvs"
    assert parsed.value == "NM_001089.3:c.875A>T"


def test_parse_genomic_hgvs() -> None:
    parsed = parse_variant_input("17:g.43044295G>A")
    assert parsed.kind == "hgvs"


@pytest.mark.parametrize("raw", ["", "   ", "gibberish input"])
def test_parse_rejects_garbage(raw: str) -> None:
    with pytest.raises(VariantParseError):
        parse_variant_input(raw)


def test_split_variant_id() -> None:
    assert split_variant_id("8-140300616-T-G") == ("8", 140300616, "T", "G")


def test_split_variant_id_malformed() -> None:
    with pytest.raises(VariantParseError):
        split_variant_id("8-140300616-T")
