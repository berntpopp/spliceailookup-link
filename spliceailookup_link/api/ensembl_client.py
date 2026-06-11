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
