# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Live HTTPS checks for WebClient's IP-pinning adapter.

These need a real server: the IP-pinning adapter can look correct in every
unit test and still fail every handshake, because only a live TLS peer proves
the ClientHello it sent was acceptable. Before the SNI fix, 9 of 11 real hosts
failed through WebClient while succeeding through plain `requests`.

Lives in tests/integration/ because tests/unit/conftest.py blocks sockets.
"""

import socket

import pytest
import requests

from gaia.web.client import WebClient

# CDN-fronted hosts that REQUIRE SNI to complete a handshake. Each of these
# failed through WebClient before the fix (wrong certificate or handshake
# refusal) while plain `requests` succeeded.
SNI_FRONTED_HOSTS = [
    "https://example.com",
    "https://github.com/amd/gaia",
    "https://pypi.org/simple/",
    "https://amd-gaia.ai",
]


def _network_available() -> bool:
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
        return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def web_client():
    if not _network_available():
        pytest.skip("no network access")
    client = WebClient(rate_limit=0.01)
    yield client
    client.close()


@pytest.mark.parametrize("url", SNI_FRONTED_HOSTS)
def test_fetches_sni_fronted_host(web_client, url):
    """WebClient completes a TLS handshake with a CDN-fronted host."""
    try:
        response = web_client.get(url, timeout=30)
    except requests.exceptions.SSLError as exc:
        pytest.fail(f"TLS handshake failed for {url} — SNI regression? {exc}")
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        pytest.skip(f"{url} unreachable: {exc}")

    # 403 still proves the handshake succeeded (bot-blocking page).
    assert response.status_code in (200, 403), response.status_code


def test_matches_plain_requests(web_client):
    """WebClient must not fail where plain `requests` succeeds.

    This is the shape of the original measurement: the pinning adapter should
    cost nothing in reachability.
    """
    regressions = []
    for url in SNI_FRONTED_HOSTS:
        try:
            web_client.get(url, timeout=30)
        except requests.exceptions.RequestException as exc:
            try:
                requests.get(
                    url,
                    timeout=30,
                    headers={"User-Agent": WebClient.DEFAULT_USER_AGENT},
                )
            except requests.exceptions.RequestException:
                continue  # Unreachable for both — not our regression.
            regressions.append(f"{url}: {type(exc).__name__}: {exc}")

    assert (
        not regressions
    ), "WebClient failed where plain requests succeeded:\n" + "\n".join(regressions)


def _assert_rejected(web_client, url):
    """The fetch must fail with SSLError, or skip if the host is unreachable.

    "badssl.com is down" and "certificate verification regressed" must not
    look the same: only the second should turn a lane red.
    """
    try:
        response = web_client.get(url, timeout=30)
    except requests.exceptions.SSLError:
        return
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        pytest.skip(f"{url} unreachable: {exc}")
    pytest.fail(f"{url} was accepted (HTTP {response.status_code}) instead of rejected")


def test_certificate_name_is_still_verified(web_client):
    """Pinning the IP must not disable certificate-name verification."""
    _assert_rejected(web_client, "https://wrong.host.badssl.com/")


def test_expired_certificate_is_still_rejected(web_client):
    """Chain verification is untouched by the SNI change."""
    _assert_rejected(web_client, "https://expired.badssl.com/")
