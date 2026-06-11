"""HTTP client layer for spliceailookup-link.

Re-exports the shared error taxonomy so the MCP error layer can classify faults
deterministically (mirrors the gnomad-link `api` package convention).
"""

from spliceailookup_link.api.base_client import (
    BaseHTTPClient,
    DataNotFoundError,
    RateLimitedError,
    SpliceApiError,
    UpstreamInputError,
)
from spliceailookup_link.api.ensembl_client import EnsemblVepClient
from spliceailookup_link.api.scoring_client import ScoringClient

__all__ = [
    "BaseHTTPClient",
    "DataNotFoundError",
    "EnsemblVepClient",
    "RateLimitedError",
    "ScoringClient",
    "SpliceApiError",
    "UpstreamInputError",
]
