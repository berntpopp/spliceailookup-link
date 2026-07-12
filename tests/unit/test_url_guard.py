"""Adversarial tests for the F-17 outbound-URL guard + response byte-cap.

Recipe B (event-hook form): every request hop -- including auto-followed
redirects -- must be https, target an EXACT allowlisted host derived from
resolved config, and carry no userinfo; responses are byte-capped fail-closed.
The long-running prediction timeout (httpx.Timeout(90)) MUST stay untouched.
Research use only; not clinical decision support.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from spliceailookup_link.api import ScoringClient
from spliceailookup_link.api.base_client import _is_retryable
from spliceailookup_link.api.url_guard import (
    MAX_RESPONSE_BYTES,
    DisallowedURLError,
    ResponseTooLargeError,
    build_host_allowlist,
    make_url_guard,
    read_capped,
)
from spliceailookup_link.config import settings
from tests.fixtures.api_responses import SPLICEAI_TRAPPC9

_SAI38 = "https://spliceai-38-xwkwwwxdwq-uc.a.run.app/spliceai/"


# --- Allowlist derivation (from resolved config, never hardcoded) -----------


def test_allowlist_derived_from_resolved_config() -> None:
    allow = build_host_allowlist(
        settings.spliceai_url("GRCh37"),
        settings.spliceai_url("GRCh38"),
        settings.pangolin_url("GRCh37"),
        settings.pangolin_url("GRCh38"),
        settings.ENSEMBL_GRCH37_URL,
        settings.ENSEMBL_GRCH38_URL,
    )
    assert allow == frozenset(
        {
            "spliceai-37-xwkwwwxdwq-uc.a.run.app",
            "spliceai-38-xwkwwwxdwq-uc.a.run.app",
            "pangolin-37-xwkwwwxdwq-uc.a.run.app",
            "pangolin-38-xwkwwwxdwq-uc.a.run.app",
            "rest.ensembl.org",
            "grch37.rest.ensembl.org",
        }
    )
    # Exact-host only: no substring/suffix admits a look-alike host.
    assert "evil-spliceai-38-xwkwwwxdwq-uc.a.run.app" not in allow
    assert "spliceai-38-xwkwwwxdwq-uc.a.run.app.evil.test" not in allow


# --- Guard exceptions are NON-RETRYABLE -------------------------------------


def test_guard_exceptions_are_not_retryable() -> None:
    assert _is_retryable(DisallowedURLError("x")) is False
    assert _is_retryable(ResponseTooLargeError("x")) is False


# --- Adversarial redirect / scheme / userinfo (through the real client) -----


@respx.mock
async def test_cross_host_redirect_raises() -> None:
    # Allowlisted host 302s to a foreign host; the hook must fire on the hop.
    route = respx.get(_SAI38).mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    client = ScoringClient()
    with pytest.raises(DisallowedURLError):
        await client.score(
            model="spliceai", build="GRCh38", variant="8-140300616-T-G", distance=50, mask=0
        )
    # Not retried: exactly one hop to the allowlisted host, then fail-closed.
    assert route.call_count == 1
    await client.close()


@respx.mock
async def test_https_downgrade_redirect_raises() -> None:
    respx.get(_SAI38).mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "http://spliceai-38-xwkwwwxdwq-uc.a.run.app/spliceai/x"},
        )
    )
    client = ScoringClient()
    with pytest.raises(DisallowedURLError):
        await client.score(
            model="spliceai", build="GRCh38", variant="8-140300616-T-G", distance=50, mask=0
        )
    await client.close()


@respx.mock
async def test_userinfo_redirect_raises() -> None:
    respx.get(_SAI38).mock(
        return_value=httpx.Response(
            302,
            headers={
                "Location": "https://user:pass@spliceai-38-xwkwwwxdwq-uc.a.run.app/spliceai/x"
            },
        )
    )
    client = ScoringClient()
    with pytest.raises(DisallowedURLError):
        await client.score(
            model="spliceai", build="GRCh38", variant="8-140300616-T-G", distance=50, mask=0
        )
    await client.close()


async def test_guard_rejects_empty_colon_at_userinfo() -> None:
    # The empty ``:@`` form has username==password=="" but httpx exposes it as a
    # non-empty ``userinfo`` (``b':'``); a username-or-password check would miss
    # it. The guard rejects ANY non-empty userinfo, while a clean URL passes.
    guard = make_url_guard(build_host_allowlist("https://rest.ensembl.org"))
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", "https://:@rest.ensembl.org/x"))
    await guard(httpx.Request("GET", "https://rest.ensembl.org/x"))


# --- Response byte-cap fails closed (raises, never truncates) ---------------


@respx.mock
async def test_oversized_response_raises() -> None:
    respx.get(_SAI38).mock(return_value=httpx.Response(200, json=SPLICEAI_TRAPPC9))
    # Inject a tiny cap so the real fixture body trips it deterministically/fast.
    client = ScoringClient(max_response_bytes=8)
    with pytest.raises(ResponseTooLargeError):
        await client.score(
            model="spliceai", build="GRCh38", variant="8-140300616-T-G", distance=50, mask=0
        )
    await client.close()


@respx.mock
async def test_read_capped_fails_closed_before_decode() -> None:
    respx.get("https://rest.ensembl.org/probe").mock(
        return_value=httpx.Response(200, content=b"0123456789ABCDEF")
    )
    guard = make_url_guard(build_host_allowlist("https://rest.ensembl.org"))
    async with httpx.AsyncClient(event_hooks={"request": [guard]}) as raw:
        with pytest.raises(ResponseTooLargeError):
            await read_capped(raw, "GET", "https://rest.ensembl.org/probe", max_bytes=8)


def test_response_cap_default_is_16_mib() -> None:
    assert MAX_RESPONSE_BYTES == 16 * 1024 * 1024


# --- Happy path unchanged + prediction timeout preserved --------------------


@respx.mock
async def test_happy_path_unchanged() -> None:
    respx.get(_SAI38).mock(return_value=httpx.Response(200, json=SPLICEAI_TRAPPC9))
    client = ScoringClient()
    result = await client.score(
        model="spliceai", build="GRCh38", variant="8-140300616-T-G", distance=500, mask=0
    )
    assert result["scores"][0]["g_name"] == "TRAPPC9"
    await client.close()


async def test_prediction_timeout_is_left_untouched() -> None:
    client = ScoringClient()
    httpx_client = await client._ensure_client()
    # The 90s long-running prediction budget must be preserved verbatim.
    assert client._timeout == settings.REQUEST_TIMEOUT == 90
    assert httpx_client.timeout == httpx.Timeout(90)
    await client.close()
