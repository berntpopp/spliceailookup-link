"""Business logic: variant resolution + SpliceAI/Pangolin scoring with caching.

Returns raw upstream payloads (plus a normalized resolution result); the MCP
tool layer is responsible for LLM-facing shaping. Scoring is deterministic per
(model, build, variant, distance, mask, gene_set), so results are cached with a
long TTL to spare the rate-limited upstream.
"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from async_lru import alru_cache

from spliceailookup_link.api import (
    DataNotFoundError,
    EnsemblVepClient,
    ScoringClient,
    SpliceApiError,
)
from spliceailookup_link.config import GenomeBuild
from spliceailookup_link.services.telemetry import CallTelemetry
from spliceailookup_link.variant import VariantInput, parse_variant_input

logger = logging.getLogger(__name__)


class SpliceService:
    """Facade over the scoring and Ensembl clients with in-process caching."""

    def __init__(
        self,
        *,
        scoring_client: ScoringClient | None = None,
        ensembl_client: EnsemblVepClient | None = None,
        cache_size: int = 1024,
        cache_ttl_minutes: int = 1440,
    ):
        self._scoring = scoring_client or ScoringClient()
        self._ensembl = ensembl_client or EnsemblVepClient()
        ttl_seconds = max(1, cache_ttl_minutes) * 60
        self._ttl_seconds = ttl_seconds
        self._cache_size = cache_size
        # Wrap the leaf upstream calls so identical (variant, params) tuples hit
        # the cache instead of the slow, rate-limited Cloud Run services.
        self._score_cached = alru_cache(maxsize=cache_size, ttl=ttl_seconds)(self._score_uncached)
        self._resolve_cached = alru_cache(maxsize=cache_size, ttl=ttl_seconds)(
            self._resolve_uncached
        )
        self._refbase_cached = alru_cache(maxsize=cache_size, ttl=ttl_seconds)(
            self._refbase_uncached
        )
        self._overlap_cached = alru_cache(maxsize=cache_size, ttl=ttl_seconds)(
            self._overlap_uncached
        )
        self._nearest_cached = alru_cache(maxsize=cache_size, ttl=ttl_seconds)(
            self._nearest_uncached
        )
        # Keys already computed once; used to report cache hit/miss telemetry.
        self._scored_keys: set[tuple[Any, ...]] = set()
        self._scored_at: dict[tuple[Any, ...], float] = {}

    # ---------------- scoring ----------------

    async def _score_uncached(
        self,
        model: str,
        build: GenomeBuild,
        variant_id: str,
        distance: int,
        mask: int,
        gene_set: str,
        raw: str | None,
        consequence: str | None,
    ) -> dict[str, Any]:
        return await self._scoring.score(
            model=model,  # type: ignore[arg-type]
            build=build,
            variant=variant_id,
            distance=distance,
            mask=mask,
            gene_set=gene_set,
            raw=raw,
            variant_consequence=consequence,
        )

    async def score(
        self,
        *,
        model: str,
        build: GenomeBuild,
        variant_id: str,
        distance: int,
        mask: int,
        gene_set: str = "basic",
        raw: str | None = None,
        consequence: str | None = None,
    ) -> tuple[dict[str, Any], CallTelemetry]:
        """Return (raw payload, telemetry) for one variant; cached by params."""
        key = (model, build, variant_id, distance, mask, gene_set, raw, consequence)
        cached = key in self._scored_keys
        start = perf_counter()
        payload = await self._score_cached(
            model, build, variant_id, distance, mask, gene_set, raw, consequence
        )
        elapsed_ms = int((perf_counter() - start) * 1000)
        now = perf_counter()
        if cached:
            scored_at = self._scored_at.get(key)
            age_s = int(now - scored_at) if scored_at is not None else None
        else:
            self._scored_keys.add(key)
            self._scored_at[key] = now
            age_s = None
            if len(self._scored_at) > self._cache_size:
                oldest = next(iter(self._scored_at))
                self._scored_at.pop(oldest, None)
                self._scored_keys.discard(oldest)
        return payload, CallTelemetry(
            cache="hit" if cached else "miss",
            upstream_elapsed_ms=None if cached else elapsed_ms,
            cache_age_s=age_s,
            cache_ttl_s=self._ttl_seconds,
        )

    async def warmup(self, build: GenomeBuild, mask: int = 0) -> dict[str, Any]:
        """Wake the upstream Cloud Run containers with a known-good sentinel call.

        Warms only the (basic gene_set, given mask) path per model; Cloud Run scales
        per-instance, so other param combinations or concurrent calls may still
        cold-start, and warmth decays after minutes of idle.
        """
        sentinel = "8-140300616-T-G"
        detail: dict[str, Any] = {}
        for model in ("spliceai", "pangolin"):
            start = perf_counter()
            status = "ok"
            try:
                await self._scoring.score(
                    model=model,  # type: ignore[arg-type]
                    build=build,
                    variant=sentinel,
                    distance=50,
                    mask=mask,
                    gene_set="basic",
                    raw=None,
                    variant_consequence=None,
                )
            except DataNotFoundError:
                status = "ok"  # a response (even not-found) means the container is warm
            except SpliceApiError:
                status = "unavailable"
            detail[model] = {"status": status, "elapsed_ms": int((perf_counter() - start) * 1000)}
        return detail

    # ---------------- resolution ----------------

    async def _resolve_uncached(self, value: str, kind: str, build: GenomeBuild) -> dict[str, Any]:
        if kind == "hgvs":
            return await self._ensembl.resolve_hgvs(value, build)
        return await self._ensembl.resolve_id(value, build)

    async def resolve(self, text: str, build: GenomeBuild) -> dict[str, Any]:
        """Resolve any supported input to a normalized result dict.

        For coordinate inputs no upstream call is made. For HGVS/rsID inputs the
        Ensembl VEP service supplies vcf_string + most_severe_consequence.
        Returns: {variant_id, genome_build, input_kind, source, gene?, consequence?, raw_input}.
        """
        parsed: VariantInput = parse_variant_input(text)
        if parsed.kind == "coordinate":
            return {
                "variant_id": parsed.value,
                "genome_build": build,
                "input_kind": "coordinate",
                "source": "direct",
                "raw_input": text,
            }
        record = await self._resolve_cached(parsed.value, parsed.kind, build)
        return _normalize_vep_record(record, parsed, build, text)

    async def _refbase_uncached(
        self, chrom: str, pos: int, length: int, build: GenomeBuild
    ) -> str | None:
        return await self._ensembl.reference_base(chrom, pos, length, build)

    async def reference_base(
        self, chrom: str, pos: int, length: int, build: GenomeBuild
    ) -> str | None:
        """Cached reference-base lookup (used by the failure-path diagnostic)."""
        return await self._refbase_cached(chrom, pos, length, build)

    async def _overlap_uncached(
        self, chrom: str, pos: int, build: GenomeBuild, window: int
    ) -> int | None:
        return await self._ensembl.overlapping_transcripts(chrom, pos, build, window)

    async def overlapping_transcripts(
        self, chrom: str, pos: int, build: GenomeBuild, window: int
    ) -> int | None:
        """Cached transcript-overlap count for the not_found fast-fail pre-check."""
        return await self._overlap_cached(chrom, pos, build, window)

    async def _nearest_uncached(
        self, chrom: str, pos: int, build: GenomeBuild, max_window: int
    ) -> dict[str, Any] | None:
        return await self._ensembl.nearest_transcript(chrom, pos, build, max_window)

    async def nearest_transcript(
        self, chrom: str, pos: int, build: GenomeBuild, max_window: int = 100_000
    ) -> dict[str, Any] | None:
        """Cached nearest-transcript lookup for the not_found enhancement."""
        return await self._nearest_cached(chrom, pos, build, max_window)

    # ---------------- lifecycle ----------------

    async def close(self) -> None:
        await self._scoring.close()
        await self._ensembl.close()


def _strip_chr(value: str) -> str:
    return value[3:] if value.lower().startswith("chr") else value


def _normalize_vep_record(
    record: dict[str, Any], parsed: VariantInput, build: GenomeBuild, raw_input: str
) -> dict[str, Any]:
    vcf_string = record.get("vcf_string")
    # VEP returns vcf_string as a list when an rsID maps to multiple ALT alleles.
    raw_ids = vcf_string if isinstance(vcf_string, list) else [vcf_string]
    candidates: list[str] = []
    for item in raw_ids:
        if item:
            cid = _strip_chr(str(item))
            if cid not in candidates:
                candidates.append(cid)
    gene_names = record.get("transcript_consequences") or []
    gene_symbol = next((tc["gene_symbol"] for tc in gene_names if tc.get("gene_symbol")), None)
    result: dict[str, Any] = {
        "variant_id": candidates[0],
        "genome_build": build,
        "input_kind": parsed.kind,
        "source": "ensembl_vep",
        "resolved_from": parsed.value,
        "assembly_name": record.get("assembly_name"),
        "gene_symbol": gene_symbol,
        "consequence": record.get("most_severe_consequence"),
        "raw_input": raw_input,
    }
    if len(candidates) > 1:
        result["ambiguous"] = True
        result["variant_ids"] = candidates
        result["note"] = (
            f"{parsed.value} maps to {len(candidates)} alleles at this locus; "
            "pick one variant_id before predicting."
        )
    return result
