"""Unit tests for combined-prediction presentation helpers (F6/F6b/#4)."""

from __future__ import annotations

import pytest

from spliceailookup_link.mcp.tools._predict_shape import (
    assess_agreement,
    combined_headline,
)


@pytest.mark.parametrize(
    ("sai", "pang", "verdict"),
    [
        (0.83, 0.85, "concordant_high"),
        (0.30, 0.32, "concordant_moderate"),
        (0.05, 0.09, "concordant_low"),
        (0.31, 0.09, "discordant"),
        (0.21, 0.05, "discordant"),
        (0.80, None, "incomplete"),
    ],
)
def test_assess_agreement_bands(sai, pang, verdict) -> None:
    assert assess_agreement(sai, pang)["verdict"] == verdict


@pytest.mark.parametrize(
    ("sai", "pang", "needle"),
    [
        (0.83, 0.85, "models agree"),
        (0.30, 0.32, "models agree"),
        (0.05, 0.09, "models agree"),
        (0.31, 0.09, "models disagree"),
        (0.21, 0.05, "models disagree"),
    ],
)
def test_headline_clause_matches_verdict(sai, pang, needle) -> None:
    agreement = assess_agreement(sai, pang)
    headline = combined_headline("TRAPPC9", "GRCh38", sai, pang, None, agreement)
    assert needle in headline
    if agreement["verdict"] == "discordant":
        assert "models agree" not in headline


def test_headline_incomplete_when_one_model_missing() -> None:
    agreement = assess_agreement(0.8, None)
    headline = combined_headline("TRAPPC9", "GRCh38", 0.8, None, None, agreement)
    assert "only one model scored" in headline
