"""Bind the canonical suite to SpliceAI's production HTTP client.

The fixture swaps only the client's transport.  Client creation, configured
event hooks, redirect policy, retry classification, and streamed cap remain
the production paths.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from unittest.mock import patch

import httpx
import pytest

from spliceailookup_link.api.base_client import BaseHTTPClient, _is_retryable
from spliceailookup_link.api.url_guard import (
    DisallowedURLError,
    ResponseTooLargeError,
    build_host_allowlist,
)

_ORIGIN = "https://allowed.example"


class _Adapter:
    @staticmethod
    def _run(coro: object) -> object:
        return asyncio.run(coro)  # type: ignore[arg-type]

    @staticmethod
    def _client(handler: Callable[[httpx.Request], httpx.Response], cap: int) -> BaseHTTPClient:
        real_async_client = httpx.AsyncClient

        def with_mock_transport(**kwargs: object) -> httpx.AsyncClient:
            return real_async_client(transport=httpx.MockTransport(handler), **kwargs)

        client = BaseHTTPClient(max_response_bytes=cap)
        client._allowed_hosts = build_host_allowlist(_ORIGIN)
        return client, patch(
            "spliceailookup_link.api.base_client.httpx.AsyncClient",
            side_effect=with_mock_transport,
        )

    @staticmethod
    def _assert_production_configuration(client: BaseHTTPClient) -> None:
        assert client._client is not None
        assert client._client.follow_redirects is True
        assert client._client.max_redirects == 5
        assert client._client.event_hooks["request"]

    def allow(self, url: str) -> object:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        async def request() -> object:
            client, async_client_patch = self._client(handler, cap=1024)
            with async_client_patch:
                try:
                    result = await client.get_json(url, {})
                    self._assert_production_configuration(client)
                    return result
                finally:
                    await client.close()

        return self._run(request())

    def request(self, url: str, redirects: list[str], max_redirects: int) -> None:
        seen = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal seen
            if seen < len(redirects):
                location = redirects[seen]
                seen += 1
                return httpx.Response(307, headers={"location": location})
            return httpx.Response(200, json={})

        async def send() -> None:
            client, async_client_patch = self._client(handler, cap=1024)
            with async_client_patch:
                try:
                    await client.get_json(url, {})
                    self._assert_production_configuration(client)
                finally:
                    await client.close()

        assert max_redirects == 5
        self._run(send())

    def read_decoded(self, chunks: Iterable[bytes], cap: int) -> None:
        payload = b"".join(chunks)

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=payload)

        async def read() -> None:
            client, async_client_patch = self._client(handler, cap=cap)
            with async_client_patch:
                try:
                    await client.get_json(f"{_ORIGIN}/", {})
                finally:
                    await client.close()

        self._run(read())

    def is_non_retryable(self, error: Exception) -> bool:
        return isinstance(error, (DisallowedURLError, ResponseTooLargeError)) and not _is_retryable(
            error
        )

    def public_message(self, error: Exception) -> str:
        return str(error)


@pytest.fixture
def http_policy_adapter() -> _Adapter:
    return _Adapter()
