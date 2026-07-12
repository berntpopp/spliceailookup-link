"""Base async HTTP client: bounded concurrency, jittered retry, error taxonomy.

Adapted from the gnomad-link client structure but for REST/JSON over httpx
(SpliceAI Lookup and Ensembl VEP are plain GET/JSON, not GraphQL). The fault
taxonomy mirrors the family so the MCP error layer can classify deterministically.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

import httpx

from spliceailookup_link.api.url_guard import (
    MAX_RESPONSE_BYTES,
    build_host_allowlist,
    make_url_guard,
    read_capped,
)
from spliceailookup_link.config import settings

logger = logging.getLogger(__name__)


def _resolved_allowed_hosts() -> frozenset[tuple[str, int]]:
    """Exact-host allowlist derived from the *resolved* upstream config (never hardcoded).

    Covers the 4 Cloud Run scoring hosts (SpliceAI/Pangolin x GRCh37/GRCh38) plus
    the 2 build-specific Ensembl REST hosts; an operator override of any of these
    URLs is honoured automatically.
    """
    return build_host_allowlist(
        settings.spliceai_url("GRCh37"),
        settings.spliceai_url("GRCh38"),
        settings.pangolin_url("GRCh37"),
        settings.pangolin_url("GRCh38"),
        settings.ENSEMBL_GRCH37_URL,
        settings.ENSEMBL_GRCH38_URL,
    )


# Transport status codes worth retrying (rate limit + transient upstream faults).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Deterministic client errors: the request shape is wrong and will never succeed.
_INPUT_ERROR_STATUS = frozenset({400, 404, 410, 422})
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0


class SpliceApiError(Exception):
    """Base exception for upstream API errors (generic -> upstream_unavailable, retryable)."""


class DataNotFoundError(SpliceApiError):
    """Upstream resolved the request but has no result (e.g. no overlapping transcript)."""


class UpstreamInputError(SpliceApiError):
    """Upstream rejected the request as malformed (deterministic, non-retryable)."""


class RateLimitedError(SpliceApiError):
    """Upstream rate-limited the request (HTTP 429) after retries (retryable)."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException, TimeoutError))


def _status_error_message(status: int) -> str:
    """Fixed, body-free message keyed on the (safe, low-cardinality) HTTP status.

    The upstream 4xx response BODY is deliberately NOT interpolated: a
    caller-influenced request can make the upstream (Ensembl / the scoring
    services) reflect hostile prose -- incl. control/zero-width/bidi/NUL code
    points -- into an ``{"error": ...}`` body, and echoing it verbatim would
    smuggle attacker-controlled text into a caller-visible message. The status is
    a bounded scalar a caller cannot use to carry prose, so it is safe to key on;
    the body is not surfaced and not logged (no-PII-in-logs invariant).
    """
    return f"Upstream rejected the request (HTTP {status})."


class BaseHTTPClient:
    """Shared async httpx client with concurrency bounding and retry."""

    def __init__(
        self,
        *,
        max_concurrency: int | None = None,
        timeout: int | None = None,
        max_response_bytes: int | None = None,
    ):
        self._timeout = settings.REQUEST_TIMEOUT if timeout is None else timeout
        limit = settings.MAX_CONCURRENCY if max_concurrency is None else max_concurrency
        self._semaphore = asyncio.Semaphore(max(1, limit))
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._max_response_bytes = (
            MAX_RESPONSE_BYTES if max_response_bytes is None else max_response_bytes
        )
        # Derived at client-build time from resolved config so an operator URL
        # override is honoured and no host literal is baked into the client.
        self._allowed_hosts = _resolved_allowed_hosts()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        # Long-running prediction budget: KEEP as-is (bytes, not
                        # time, are what F-17 bounds).
                        timeout=httpx.Timeout(self._timeout),
                        headers={
                            "Accept": "application/json",
                            "User-Agent": settings.USER_AGENT,
                        },
                        follow_redirects=True,
                        max_redirects=5,
                        event_hooks={"request": [make_url_guard(self._allowed_hosts)]},
                    )
        return self._client

    async def _acquire_slot(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=max(0.0, timeout))
        except TimeoutError as exc:
            raise RateLimitedError(
                f"Local concurrency limit saturated (max {settings.MAX_CONCURRENCY} "
                "concurrent upstream requests). Retry with exponential backoff or "
                "fan out fewer calls at once."
            ) from exc

    async def get_json(self, url: str, params: dict[str, Any]) -> Any:
        """GET `url` with `params`, returning parsed JSON (dict or list).

        Retries transient transport faults (timeouts, 5xx, 429) with jittered
        exponential backoff. A persistent 429 surfaces as RateLimitedError; other
        HTTP errors as SpliceApiError. The caller is responsible for inspecting an
        `error` field in a 200 body (this upstream reports failures that way).
        """
        client = await self._ensure_client()
        loop = asyncio.get_running_loop()
        queue_deadline = loop.time() + settings.QUEUE_WAIT_TIMEOUT
        delay = _BACKOFF_BASE_SECONDS
        last_exc: BaseException | None = None

        for attempt in range(settings.MAX_RETRIES + 1):
            await self._acquire_slot(timeout=max(0.0, queue_deadline - loop.time()))
            try:
                # Streamed, byte-capped read (fail-closed past the ceiling). The
                # url-guard event hook validates every hop incl. redirects; its
                # DisallowedURLError / ResponseTooLargeError are non-retryable and
                # propagate straight out of the loop (not caught below).
                body = await read_capped(
                    client, "GET", url, params=params, max_bytes=self._max_response_bytes
                )
                return json.loads(body)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code
                if status in _INPUT_ERROR_STATUS:
                    raise UpstreamInputError(_status_error_message(status)) from exc
                if status == 429 and attempt == settings.MAX_RETRIES:
                    raise RateLimitedError(f"Rate limited by upstream (HTTP 429): {url}") from exc
                if not _is_retryable(exc) or attempt == settings.MAX_RETRIES:
                    raise SpliceApiError(f"Upstream HTTP {status} for {url}") from exc
            except httpx.TooManyRedirects as exc:
                # httpx's own redirect exception is a RequestError and would
                # otherwise be retried. Redirect exhaustion is deterministic
                # policy failure, so turn it into the fixed non-retryable class.
                from spliceailookup_link.api.url_guard import DisallowedURLError

                raise DisallowedURLError("outbound request blocked by HTTP policy") from exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt == settings.MAX_RETRIES:
                    raise SpliceApiError(f"Upstream request failed: {exc!s}") from exc
            finally:
                self._semaphore.release()
            # Full jitter de-synchronises a concurrent burst's retries.
            await asyncio.sleep(random.uniform(0, min(delay, _BACKOFF_MAX_SECONDS)))  # noqa: S311
            delay = min(delay * 2, _BACKOFF_MAX_SECONDS)

        raise SpliceApiError(f"Retry loop exhausted for {url}: {last_exc!s}")  # pragma: no cover

    async def close(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.aclose()

    async def __aenter__(self) -> BaseHTTPClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
