# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Resolve the Lemonade installer URLs against the live upstream release.

The unit tests assert URL *shape* with a mocked HTTP layer, which proves we
built a string — not that GitHub will serve it. This test HEADs the real
asset for every platform/arch pair at the pinned ``LEMONADE_VERSION``, so an
upstream rename fails in CI instead of 404ing the first user whose install
actually downloads the asset.

Skips when GitHub is unreachable (offline dev box), on a transient transport
failure (connection reset, timeout, 5xx — a network blip, not proof of an
upstream rename), or when ``GAIA_SKIP_NETWORK_TESTS`` is set. A genuine DNS
resolution failure for github.com still fails in CI, since CI runners always
have a resolver — that is a real infrastructure signal, not flakiness.
"""

import os
import socket
import time
import urllib.error
import urllib.request

import pytest

from gaia.installer.lemonade_installer import LemonadeAssetError
from gaia.version import LEMONADE_VERSION
from tests.fixtures.lemonade_assets import SUPPORTED_TARGETS, make_installer

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 2


class _GitHubUnreachable(Exception):
    """Transient — GitHub could not be reached, but DNS resolved fine.

    Covers connection resets (including ``http.client.RemoteDisconnected``,
    which is a ``ConnectionError`` subclass and is NOT wrapped in
    ``URLError`` — see ``urllib.request.AbstractHTTPHandler.do_open``),
    timeouts, and non-DNS ``URLError``s.
    """


def _check_github_reachable(timeout: int = 10) -> None:
    """Raise on failure to reach github.com.

    Raises:
        socket.gaierror: DNS resolution failed for github.com itself — a
            real signal, not a blip.
        _GitHubUnreachable: A transport-level failure that a retry may fix.
    """
    request = urllib.request.Request(
        "https://github.com",
        method="HEAD",
        headers={"User-Agent": "GAIA-Installer/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return
    except urllib.error.HTTPError:
        return  # Reached GitHub; the status code doesn't matter here.
    except urllib.error.URLError as e:
        if isinstance(e.reason, socket.gaierror):
            raise e.reason from e
        raise _GitHubUnreachable(str(e.reason)) from e
    except (TimeoutError, ConnectionError) as e:
        raise _GitHubUnreachable(str(e)) from e


def _github_reachable_with_retry() -> None:
    """Retry transient failures with backoff; let a DNS failure surface immediately."""
    last_error: _GitHubUnreachable | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            _check_github_reachable()
            return
        except _GitHubUnreachable as e:
            last_error = e
            if attempt < _RETRY_ATTEMPTS - 1:
                time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise last_error


@pytest.fixture(scope="module")
def require_github_network():
    """Skip offline or on a flaky connection; a real DNS failure still fails in CI."""
    if os.environ.get("GAIA_SKIP_NETWORK_TESTS"):
        pytest.skip("GAIA_SKIP_NETWORK_TESTS is set")
    try:
        _github_reachable_with_retry()
    except socket.gaierror as e:
        if os.environ.get("CI"):
            pytest.fail(f"github.com did not resolve in CI - DNS failure: {e}")
        pytest.skip(f"github.com did not resolve - offline dev box: {e}")
    except _GitHubUnreachable as e:
        pytest.skip(
            f"github.com unreachable after {_RETRY_ATTEMPTS} attempts "
            f"(transient network failure, not a confirmed outage): {e}"
        )


@pytest.mark.network
def test_every_supported_target_resolves(require_github_network):
    """Every installer URL GAIA can build must return HTTP 200 upstream."""
    definitive_failures = []
    transient_failures = []
    for system, machine, minimal, expected_asset in SUPPORTED_TARGETS:
        installer = make_installer(system, machine, minimal)
        url = installer.get_download_url()
        if not url.endswith(expected_asset):
            definitive_failures.append(
                f"{system}/{machine}: built {url}, expected {expected_asset}"
            )
            continue
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                installer.verify_download_url(timeout=30)
                break
            except LemonadeAssetError as e:
                if e.definitive:
                    definitive_failures.append(f"{system}/{machine}: {e}")
                    break
                if attempt < _RETRY_ATTEMPTS - 1:
                    time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                transient_failures.append(f"{system}/{machine}: {e}")

    assert not definitive_failures, (
        f"Lemonade v{LEMONADE_VERSION} installer assets did not resolve upstream. "
        "Upstream likely renamed an asset - check "
        f"https://github.com/lemonade-sdk/lemonade/releases/tag/v{LEMONADE_VERSION}\n"
        + "\n".join(definitive_failures)
    )
    if transient_failures:
        pytest.skip(
            f"GitHub asset checks failed transiently after {_RETRY_ATTEMPTS} "
            "attempts each (connection reset/timeout/5xx), not a confirmed "
            "upstream rename:\n" + "\n".join(transient_failures)
        )
