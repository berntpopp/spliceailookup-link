"""Outbound-URL guard + streamed response byte-cap for the httpx clients.

F-17 hardening (Recipe B, event-hook form). Every outgoing request hop --
including auto-followed redirects -- is validated against an EXACT host
allowlist derived from resolved config (never hardcoded), must use ``https``,
and must carry no userinfo. Responses are read through a streamed byte-cap that
FAILS CLOSED (raises) past the ceiling rather than truncating -- a truncated
JSON body is unparseable, so silent truncation is never safe.

Both guard exceptions are plain ``Exception`` subclasses (NOT
``httpx.TransportError``/``httpx.TimeoutException``), so the retry loop in
``base_client`` treats them as NON-RETRYABLE: they are deterministic policy
failures, not transient transport faults.

This guard bounds BYTES, never time: the long-running prediction timeout and
service soft-deadlines are enforced elsewhere and are deliberately untouched.

Research use only; not clinical decision support.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import SplitResult, urlsplit

import httpx

# 16 MiB ceiling on the raw response BYTES. A SpliceAI/Pangolin score or Ensembl
# VEP JSON body this large is anomalous; the cap bounds memory/parse cost and is
# applied to bytes-on-the-wire, never to elapsed time.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class DisallowedURLError(Exception):
    """An outbound request/redirect targeted a non-allowlisted URL. NON-RETRYABLE."""


class ResponseTooLargeError(Exception):
    """An upstream response exceeded the byte ceiling. NON-RETRYABLE (fail-closed)."""


_POLICY_ERROR = "outbound request blocked by HTTP policy"
_SIZE_ERROR = "upstream response exceeds HTTP policy byte limit"


def _normalized_origin(url: str | SplitResult) -> tuple[str, int]:
    """Return the configured URL's normalized HTTPS origin or reject it safely."""
    parsed = urlsplit(url) if isinstance(url, str) else url
    try:
        port = parsed.port if parsed.port is not None else 443
    except ValueError as exc:
        raise DisallowedURLError(_POLICY_ERROR) from exc
    if parsed.scheme != "https" or parsed.username is not None or not parsed.hostname:
        raise DisallowedURLError(_POLICY_ERROR)
    return parsed.hostname.lower(), port


def build_host_allowlist(*base_urls: str) -> frozenset[tuple[str, int]]:
    """Return the normalized exact-origin allowlist from resolved base URLs.

    Hosts are taken from the caller's already-resolved configuration values, so
    an operator override of any upstream URL is honoured automatically and no
    host literal is hardcoded here.
    """
    origins: set[tuple[str, int]] = set()
    for url in base_urls:
        origins.add(_normalized_origin(url))
    return frozenset(origins)


def make_url_guard(
    allowed_hosts: frozenset[tuple[str, int]],
) -> Callable[[httpx.Request], Awaitable[None]]:
    """Build an httpx request event-hook validating every hop against the allowlist.

    Fires on the initial request and on every auto-followed redirect. Rejects a
    non-https scheme, any userinfo, and any host outside the EXACT allowlist
    (no suffix/substring match).
    """

    async def _guard(request: httpx.Request) -> None:
        url = request.url
        if url.scheme != "https":
            raise DisallowedURLError(_POLICY_ERROR)
        if url.userinfo:
            raise DisallowedURLError(_POLICY_ERROR)
        host = (url.host or "").lower()
        port = url.port if url.port is not None else 443
        if (host, port) not in allowed_hosts:
            raise DisallowedURLError(_POLICY_ERROR)

    return _guard


async def read_capped(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_bytes: int,
    **kwargs: Any,
) -> bytes:
    """Stream a response, aborting (fail-closed) once it exceeds ``max_bytes``.

    ``raise_for_status`` runs inside the stream so the existing HTTP-status retry
    classification in ``base_client`` is preserved; the byte cap raises
    ``ResponseTooLargeError`` before any decode so a truncated body never reaches
    a JSON parser.
    """
    async with client.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise ResponseTooLargeError(_SIZE_ERROR)
            chunks.append(chunk)
    return b"".join(chunks)
