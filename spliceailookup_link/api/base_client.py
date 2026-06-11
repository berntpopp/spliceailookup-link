"""Base async HTTP client: bounded concurrency, jittered retry, error taxonomy.

Adapted from the gnomad-link client structure but for REST/JSON over httpx
(SpliceAI Lookup and Ensembl VEP are plain GET/JSON, not GraphQL). The fault
taxonomy mirrors the family so the MCP error layer can classify deterministically.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from spliceailookup_link.config import settings

logger = logging.getLogger(__name__)

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


def _extract_error_message(response: httpx.Response, status: int) -> str:
    """Best-effort human-readable message from a 4xx body (Ensembl uses {"error": ...})."""
    try:
        body = response.json()
        if isinstance(body, dict) and body.get("error"):
            return str(body["error"])
    except Exception:  # noqa: S110 - body may not be JSON; fall through to generic message
        pass
    return f"Upstream rejected the request (HTTP {status})."


class BaseHTTPClient:
    """Shared async httpx client with concurrency bounding and retry."""

    def __init__(self, *, max_concurrency: int | None = None, timeout: int | None = None):
        self._timeout = settings.REQUEST_TIMEOUT if timeout is None else timeout
        limit = settings.MAX_CONCURRENCY if max_concurrency is None else max_concurrency
        self._semaphore = asyncio.Semaphore(max(1, limit))
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=httpx.Timeout(self._timeout),
                        headers={
                            "Accept": "application/json",
                            "User-Agent": settings.USER_AGENT,
                        },
                        follow_redirects=True,
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
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code
                if status in _INPUT_ERROR_STATUS:
                    raise UpstreamInputError(_extract_error_message(exc.response, status)) from exc
                if status == 429 and attempt == settings.MAX_RETRIES:
                    raise RateLimitedError(f"Rate limited by upstream (HTTP 429): {url}") from exc
                if not _is_retryable(exc) or attempt == settings.MAX_RETRIES:
                    raise SpliceApiError(f"Upstream HTTP {status} for {url}") from exc
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
