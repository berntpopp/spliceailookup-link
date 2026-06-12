"""Resolve-and-dedup pass for predict_splicing_batch.

Maps each input to its canonical variant_id so a variant submitted twice (e.g.
once as a coordinate and once as its HGVS) is scored once against the scarce,
rate-limited upstream and the result is re-expanded to every original position.

Inputs that fail to resolve, or that resolve ambiguously, are NOT grouped -- they
flow through the normal per-item path so their own error surfaces at their own
position. Resolution here is cheap: the service caches it, and the owner item's
later prepare_variant call hits that cache.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from spliceailookup_link.config import GenomeBuild
from spliceailookup_link.services import SpliceService
from spliceailookup_link.variant import parse_variant_input


@dataclass
class DedupPlan:
    # original index -> canonical variant_id (only for inputs that resolved cleanly)
    canonical: dict[int, str] = field(default_factory=dict)
    # canonical variant_id -> first input index that owns the real upstream call
    owner: dict[str, int] = field(default_factory=dict)
    # indices that did not resolve / were ambiguous: score via the original input
    passthrough: list[int] = field(default_factory=list)

    @property
    def unique_count(self) -> int:
        """Distinct variants that will trigger a real upstream call."""
        return len(self.owner) + len(self.passthrough)

    @property
    def duplicate_count(self) -> int:
        """Inputs served from a sibling's result instead of a fresh upstream call."""
        return len(self.canonical) - len(self.owner)

    def is_owner(self, idx: int) -> bool:
        canonical = self.canonical.get(idx)
        return canonical is not None and self.owner.get(canonical) == idx


async def build_dedup_plan(
    service: SpliceService, variants: list[str], genome_build: GenomeBuild
) -> DedupPlan:
    """Resolve each input to a canonical id; coordinates resolve locally."""
    plan = DedupPlan()
    for idx, raw in enumerate(variants):
        try:
            parsed = parse_variant_input(raw)
        except Exception:
            plan.passthrough.append(idx)
            continue
        if parsed.kind == "coordinate":
            canonical = parsed.value
        else:
            try:
                resolution = await service.resolve(raw, genome_build)
            except Exception:
                plan.passthrough.append(idx)
                continue
            if resolution.get("ambiguous") or not resolution.get("variant_id"):
                plan.passthrough.append(idx)
                continue
            canonical = resolution["variant_id"]
        plan.canonical[idx] = canonical
        plan.owner.setdefault(canonical, idx)
    return plan
