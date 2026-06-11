"""Tests for the GRCh37/GRCh38 build-mismatch heuristic."""

from __future__ import annotations

from spliceailookup_link.mcp.build_check import detect_build_mismatch, likely_build


def test_position_only_valid_on_grch37() -> None:
    # chr8: GRCh38 len 145,138,636; GRCh37 len 146,364,022.
    assert likely_build("8", 145_500_000) == "GRCh37"


def test_position_only_valid_on_grch38() -> None:
    # chr16: GRCh38 len 90,338,345; GRCh37 len 90,354,753. Pick a pos valid on
    # GRCh37 only is not possible for 16 (38<37); use chr17 where 38>37.
    # chr17: GRCh38 83,257,441; GRCh37 81,195,210 -> pos in between is GRCh38-only.
    assert likely_build("17", 82_000_000) == "GRCh38"


def test_ambiguous_position_returns_none() -> None:
    assert likely_build("8", 1_000_000) is None


def test_mito_returns_none() -> None:
    assert likely_build("MT", 15_000) is None
    assert likely_build("M", 15_000) is None


def test_detect_mismatch_flags_wrong_build() -> None:
    assert detect_build_mismatch("8-145500000-A-T", "GRCh38") == "GRCh37"


def test_detect_mismatch_none_when_consistent() -> None:
    assert detect_build_mismatch("8-145500000-A-T", "GRCh37") is None


def test_detect_mismatch_none_for_ambiguous() -> None:
    assert detect_build_mismatch("8-140300616-T-G", "GRCh38") is None


def test_detect_mismatch_handles_malformed() -> None:
    assert detect_build_mismatch("garbage", "GRCh38") is None
