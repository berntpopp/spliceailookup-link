"""Ensembl VEP REST client for resolving HGVS / rsIDs into chrom-pos-ref-alt.

Mirrors what the SpliceAI Lookup frontend does: POST/GET the VEP endpoint with
`vcf_string=1` and read back the `vcf_string` (= CHROM-POS-REF-ALT) plus
`most_severe_consequence`, which is forwarded to the scoring API as
`variant_consequence`.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from spliceailookup_link.api.base_client import (
    BaseHTTPClient,
    DataNotFoundError,
    SpliceApiError,
    UpstreamInputError,
)
from spliceailookup_link.config import GenomeBuild, settings

logger = logging.getLogger(__name__)


class EnsemblVepClient(BaseHTTPClient):
    """Resolve variant notations via Ensembl VEP."""

    async def _vep(self, path: str, build: GenomeBuild) -> dict[str, Any]:
        url = f"{settings.ensembl_url(build)}{path}"
        payload = await self.get_json(url, {"vcf_string": 1, "content-type": "application/json"})
        record: dict[str, Any]
        if isinstance(payload, list):
            if not payload:
                raise DataNotFoundError("Ensembl VEP returned no annotation for this input.")
            record = payload[0]
        elif isinstance(payload, dict):
            if payload.get("error"):
                raise UpstreamInputError(str(payload["error"]))
            record = payload
        else:  # pragma: no cover - defensive
            raise DataNotFoundError("Unexpected Ensembl VEP response shape.")
        if not record.get("vcf_string"):
            raise DataNotFoundError(
                "Ensembl VEP could not produce genomic coordinates (vcf_string) for this input."
            )
        return record

    async def resolve_hgvs(self, hgvs: str, build: GenomeBuild) -> dict[str, Any]:
        """Resolve a transcript/genomic HGVS string (e.g. NM_000123.4:c.10A>T)."""
        return await self._vep(f"/vep/human/hgvs/{quote(hgvs, safe='')}", build)

    async def resolve_id(self, variant_id: str, build: GenomeBuild) -> dict[str, Any]:
        """Resolve a known-variant identifier (e.g. rsID like rs12345)."""
        return await self._vep(f"/vep/human/id/{quote(variant_id, safe='')}", build)

    async def reference_base(
        self, chrom: str, pos: int, length: int, build: GenomeBuild
    ) -> str | None:
        """Return the uppercase reference base(s) at chrom:pos..pos+length-1, or None.

        Uses Ensembl REST sequence/region on the build-specific host. Returns None
        on any upstream fault or empty sequence so callers can treat the check as
        inconclusive and fall back, never regressing behavior.
        """
        c = chrom.removeprefix("chr").removeprefix("CHR").upper()
        end = pos + max(1, length) - 1
        url = f"{settings.ensembl_url(build)}/sequence/region/human/{c}:{pos}..{end}"
        try:
            payload = await self.get_json(url, {"content-type": "application/json"})
        except SpliceApiError:
            return None
        seq = payload.get("seq") if isinstance(payload, dict) else None
        return seq.upper() if isinstance(seq, str) and seq else None

    async def overlapping_transcripts(
        self, chrom: str, pos: int, build: GenomeBuild, window: int
    ) -> int | None:
        """Return the count of transcripts overlapping [pos-window, pos+window], or None.

        Uses Ensembl REST overlap/region on the build-specific host. Returns None on any
        upstream fault / unexpected shape so the caller treats it as inconclusive and
        falls back to real scoring (never a false fast-fail).
        """
        c = chrom.removeprefix("chr").removeprefix("CHR").upper()
        start = max(1, pos - window)
        end = pos + window
        url = f"{settings.ensembl_url(build)}/overlap/region/human/{c}:{start}..{end}"
        try:
            payload = await self.get_json(
                url, {"feature": "transcript", "content-type": "application/json"}
            )
        except SpliceApiError:
            return None
        return len(payload) if isinstance(payload, list) else None

    async def nearest_transcript(
        self, chrom: str, pos: int, build: GenomeBuild, max_window: int = 100_000
    ) -> dict[str, Any] | None:
        """Closest transcript to chrom:pos within max_window, or None.

        Returns {distance_nt, gene, transcript_id}; distance_nt is 0 when pos is
        inside a transcript. None on any fault / no transcript within the window
        (never invents data), so a not_found stays a not_found.
        """
        c = chrom.removeprefix("chr").removeprefix("CHR").upper()
        start = max(1, pos - max_window)
        end = pos + max_window
        url = f"{settings.ensembl_url(build)}/overlap/region/human/{c}:{start}..{end}"
        try:
            payload = await self.get_json(
                url, {"feature": "transcript", "content-type": "application/json"}
            )
        except SpliceApiError:
            return None
        if not isinstance(payload, list) or not payload:
            return None
        best: dict[str, Any] | None = None
        best_dist: int | None = None
        for tx in payload:
            t_start, t_end = tx.get("start"), tx.get("end")
            if not isinstance(t_start, int) or not isinstance(t_end, int):
                continue
            dist = 0 if t_start <= pos <= t_end else min(abs(pos - t_start), abs(pos - t_end))
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best = {
                    "distance_nt": dist,
                    "gene": tx.get("external_name") or tx.get("Parent") or tx.get("id"),
                    "transcript_id": tx.get("id"),
                }
        return best
