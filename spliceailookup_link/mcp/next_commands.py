"""Shared builders for _meta.next_commands entries.

Every tool emits next_commands in one shape: a list of {tool, arguments} dicts
whose arguments are directly callable (never empty). Centralising the builders
keeps the contract identical across tools.
"""

from __future__ import annotations

from typing import Any


def cmd(tool: str, **arguments: Any) -> dict[str, Any]:
    """One next_commands entry. Arguments must be directly callable (never empty)."""
    return {"tool": tool, "arguments": arguments}


def after_resolve(variant_id: str, genome_build: str) -> list[dict[str, Any]]:
    """After resolving a variant, the natural next step is the combined prediction."""
    return [cmd("predict_splicing", variant_id=variant_id, genome_build=genome_build)]


def after_resolve_many(variant_ids: list[str], genome_build: str) -> list[dict[str, Any]]:
    """One predict_splicing per allele so every candidate is directly callable."""
    return [cmd("predict_splicing", variant_id=v, genome_build=genome_build) for v in variant_ids]


def for_variant(variant_id: str, genome_build: str) -> list[dict[str, Any]]:
    """Standard follow-ups for a resolved coordinate: SpliceAI then Pangolin."""
    return [
        cmd("predict_spliceai", variant_id=variant_id, genome_build=genome_build),
        cmd("predict_pangolin", variant_id=variant_id, genome_build=genome_build),
    ]


def for_combined(variant_id: str, genome_build: str) -> list[dict[str, Any]]:
    """Same-server drill-down: full single-model scores for this variant."""
    return [
        cmd(
            "predict_spliceai",
            variant_id=variant_id,
            genome_build=genome_build,
            response_mode="full",
        ),
    ]
