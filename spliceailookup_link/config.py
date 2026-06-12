"""Configuration settings for the spliceailookup-link server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GenomeBuild = Literal["GRCh37", "GRCh38"]
_BUILD_TO_HG = {"GRCh37": "37", "GRCh38": "38"}


@dataclass
class ServerConfig:
    """Transport-level server configuration."""

    transport: Literal["unified", "http", "stdio"] = "unified"
    host: str = "127.0.0.1"
    port: int = 8603
    mcp_path: str = "/mcp"
    enable_docs: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> ServerConfig:
        return cls(
            transport=settings.MCP_TRANSPORT,
            host=settings.MCP_HOST,
            port=settings.MCP_PORT,
            mcp_path=settings.MCP_PATH,
            log_level=settings.LOG_LEVEL,
        )


class Settings(BaseSettings):
    """Application settings (env prefix SPLICEAILOOKUP_LINK_)."""

    # Upstream scoring API host templates. {hg} -> 37 or 38.
    SPLICEAI_URL_TEMPLATE: str = "https://spliceai-{hg}-xwkwwwxdwq-uc.a.run.app/spliceai/"
    PANGOLIN_URL_TEMPLATE: str = "https://pangolin-{hg}-xwkwwwxdwq-uc.a.run.app/pangolin/"

    # Ensembl VEP REST hosts (build-specific) for HGVS / rsID resolution.
    ENSEMBL_GRCH38_URL: str = "https://rest.ensembl.org"
    ENSEMBL_GRCH37_URL: str = "https://grch37.rest.ensembl.org"

    # Request handling. Upstream is "interactive use only, several requests per
    # user per minute"; individual calls can take 30s+ (comprehensive gene set
    # and large distances are slowest). Keep concurrency low and timeouts wide.
    REQUEST_TIMEOUT: int = 90
    MAX_CONCURRENCY: int = 2
    QUEUE_WAIT_TIMEOUT: int = 30
    MAX_RETRIES: int = 3

    # predict_splicing_batch retries a per-item retryable failure (rate_limited /
    # upstream_unavailable) once within the batch; this caps the jittered backoff.
    # Tests set 0 for determinism.
    BATCH_RETRY_BACKOFF_SECONDS: float = 1.0

    # Foreground prediction soft deadline (seconds). A comprehensive gene_set with a
    # large max_distance can exceed the client's MCP timeout; this returns a
    # structured upstream_unavailable before the client gives up. Set 0 to disable.
    # Background Tasks (ctx.is_background_task) bypass this deadline.
    PREDICT_SOFT_DEADLINE_SECONDS: int = 55

    # In-process cache. Scores are deterministic per (model, build, variant,
    # distance, mask, gene_set), so a long TTL is safe and dramatically reduces
    # load on the rate-limited upstream.
    CACHE_SIZE: int = 1024
    CACHE_TTL_MINUTES: int = 1440

    # A response is "warm" if it was a cache hit or the upstream answered faster
    # than this (cold Cloud Run starts are ~13s+; warm calls are sub-second).
    # Surfaced as _meta.served_warm so a client can choose blocking vs background.
    WARM_THRESHOLD_MS: int = 5000

    # Validate the coordinate REF against the Ensembl reference base BEFORE the
    # slow scoring dispatch (fast ref_mismatch instead of a ~17s not_found).
    # Disable only if Ensembl sequence lookups are unavailable in an environment.
    PREFLIGHT_REF_CHECK_ENABLED: bool = True

    # Fast-fail not_found: before the slow scoring dispatch, ask Ensembl whether any
    # transcript overlaps [pos-max_distance, pos+max_distance]. A conclusive zero means
    # neither gene_set can score the variant, so return not_found in <0.5s instead of a
    # ~20s cold round-trip. Conservative: any inconclusive/non-zero result falls through
    # to real scoring (never invents a not_found). Disable if Ensembl overlap is unavailable.
    PREFLIGHT_OVERLAP_CHECK_ENABLED: bool = True

    # Background-task (FastMCP Tasks / Docket) backend. memory:// is in-process and
    # correct for the single-process unified host; set redis://... for multi-worker.
    DOCKET_URL: str = "memory://"

    # Transport
    MCP_TRANSPORT: Literal["unified", "http", "stdio"] = "unified"
    MCP_HOST: str = "127.0.0.1"
    MCP_PORT: int = 8603
    MCP_PATH: str = "/mcp"

    # Logging
    LOG_LEVEL: str = "INFO"
    MCP_LOG_LEVEL: str = "INFO"
    STDIO_LOG_LEVEL: str = "WARNING"

    # Server
    CORS_ORIGINS: str = "*"
    USER_AGENT: str = (
        "spliceailookup-link/0.1 (research MCP; +https://github.com/berntpopp/spliceailookup-link)"
    )

    model_config = SettingsConfigDict(
        env_prefix="SPLICEAILOOKUP_LINK_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("MCP_PATH")
    @classmethod
    def _validate_mcp_path(cls, v: str) -> str:
        return v if v.startswith("/") else f"/{v}"

    def spliceai_url(self, build: GenomeBuild) -> str:
        return self.SPLICEAI_URL_TEMPLATE.format(hg=_BUILD_TO_HG[build])

    def pangolin_url(self, build: GenomeBuild) -> str:
        return self.PANGOLIN_URL_TEMPLATE.format(hg=_BUILD_TO_HG[build])

    def ensembl_url(self, build: GenomeBuild) -> str:
        return self.ENSEMBL_GRCH37_URL if build == "GRCh37" else self.ENSEMBL_GRCH38_URL

    @property
    def cors_origins_list(self) -> list[str]:
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()


def hg_for_build(build: GenomeBuild) -> str:
    """Return the upstream `hg` parameter value ('37' or '38') for a build name."""
    return _BUILD_TO_HG[build]
