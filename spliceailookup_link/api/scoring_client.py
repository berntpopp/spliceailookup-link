"""Client for the SpliceAI and Pangolin scoring endpoints (Broad Cloud Run).

Both models share the same GET query shape and a common quirk: failures come
back as HTTP 200 with an `error` string in the JSON body, so success must be
verified by inspecting the payload, not just the HTTP status.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from spliceailookup_link.api.base_client import (
    BaseHTTPClient,
    DataNotFoundError,
    UpstreamInputError,
)
from spliceailookup_link.config import GenomeBuild, hg_for_build, settings

logger = logging.getLogger(__name__)

Model = Literal["spliceai", "pangolin"]

# Substrings the upstream uses to signal a deterministic, non-retryable parse
# failure (bad variant format / impossible coordinate).
_PARSE_ERROR_SIGNALS = ("unable to parse", "could not parse", "invalid variant")
# Substrings signalling a well-formed variant that simply has no scores
# (outside any annotated transcript for the chosen gene set).
_NO_SCORE_SIGNALS = ("did not return any scores", "no scores", "does not overlap")


class ScoringClient(BaseHTTPClient):
    """Calls the SpliceAI / Pangolin Cloud Run services."""

    def _url(self, model: Model, build: GenomeBuild) -> str:
        return settings.spliceai_url(build) if model == "spliceai" else settings.pangolin_url(build)

    async def score(
        self,
        *,
        model: Model,
        build: GenomeBuild,
        variant: str,
        distance: int,
        mask: int,
        gene_set: str = "basic",
        raw: str | None = None,
        variant_consequence: str | None = None,
    ) -> dict[str, Any]:
        """Fetch scores for one variant. Raises a typed error on an upstream `error` field."""
        params: dict[str, Any] = {
            "hg": hg_for_build(build),
            "distance": distance,
            "mask": mask,
            "bc": gene_set,
            "variant": variant,
        }
        if raw:
            params["raw"] = raw
        if variant_consequence:
            params["variant_consequence"] = variant_consequence

        payload: dict[str, Any] = await self.get_json(self._url(model, build), params)
        error = payload.get("error") if isinstance(payload, dict) else None
        if error:
            # The upstream reports failures as HTTP 200 with an ``error`` STRING in
            # the body. That body is caller-influenceable (a hostile variant can
            # make the service reflect arbitrary prose + control/zero-width/bidi/NUL
            # code points), so it is classified locally (read-only) but NEVER echoed
            # into the raised exception: fixed, body-free messages are raised instead
            # and the raw body is not logged (no-PII-in-logs invariant).
            lowered = str(error).lower()
            if any(sig in lowered for sig in _PARSE_ERROR_SIGNALS):
                raise UpstreamInputError("The upstream could not parse the requested variant.")
            if any(sig in lowered for sig in _NO_SCORE_SIGNALS):
                raise DataNotFoundError(
                    "The upstream returned no scores for the requested variant."
                )
            # Unknown error string from the model service: treat as not_found so the
            # caller reformulates rather than hammering the rate-limited API.
            raise DataNotFoundError("The upstream returned an error for the requested variant.")
        return payload
