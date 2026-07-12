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
from urllib.parse import urlsplit

import httpx

# 16 MiB ceiling on the raw response BYTES. A SpliceAI/Pangolin score or Ensembl
# VEP JSON body this large is anomalous; the cap bounds memory/parse cost and is
# applied to bytes-on-the-wire, never to elapsed time.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class DisallowedURLError(Exception):
    """An outbound request/redirect targeted a non-allowlisted URL. NON-RETRYABLE."""


class ResponseTooLargeError(Exception):
    """An upstream response exceeded the byte ceiling. NON-RETRYABLE (fail-closed)."""


def build_host_allowlist(*base_urls: str) -> frozenset[str]:
    """Return the lowercased exact-host allowlist derived from resolved base URLs.

    Hosts are taken from the caller's already-resolved configuration values, so
    an operator override of any upstream URL is honoured automatically and no
    host literal is hardcoded here.
    """
    hosts: set[str] = set()
    for url in base_urls:
        host = urlsplit(url).hostname
        if host:
            hosts.add(host.lower())
    return frozenset(hosts)


def make_url_guard(
    allowed_hosts: frozenset[str],
) -> Callable[[httpx.Request], Awaitable[None]]:
    """Build an httpx request event-hook validating every hop against the allowlist.

    Fires on the initial request and on every auto-followed redirect. Rejects a
    non-https scheme, any userinfo, and any host outside the EXACT allowlist
    (no suffix/substring match).
    """

    async def _guard(request: httpx.Request) -> None:
        url = request.url
        if url.scheme != "https":
            raise DisallowedURLError(f"non-https scheme not permitted: {url.scheme}")
        if url.userinfo:
            raise DisallowedURLError("userinfo in URL not permitted")
        host = (url.host or "").lower()
        if host not in allowed_hosts:
            raise DisallowedURLError(f"host not in allowlist: {host}")

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
                raise ResponseTooLargeError(f"upstream response exceeded {max_bytes} bytes")
            chunks.append(chunk)
    return b"".join(chunks)
