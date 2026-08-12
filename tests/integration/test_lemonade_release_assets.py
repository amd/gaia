# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Resolve the Lemonade installer URLs against the live upstream release.

The unit tests assert URL *shape* with a mocked HTTP layer, which proves we
built a string — not that GitHub will serve it. This test HEADs the real
asset for every platform/arch pair at the pinned ``LEMONADE_VERSION``, so an
upstream rename fails in CI instead of 404ing the first user whose install
actually downloads the asset.

Skips when GitHub is unreachable (offline dev box) or ``GAIA_SKIP_NETWORK_TESTS``
is set. CI runners always have network, so it runs there.
"""

import os
import urllib.error
import urllib.request

import pytest

from gaia.version import LEMONADE_VERSION
from tests.fixtures.lemonade_assets import SUPPORTED_TARGETS, make_installer


def _github_reachable() -> bool:
    request = urllib.request.Request(
        "https://github.com",
        method="HEAD",
        headers={"User-Agent": "GAIA-Installer/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            return True
    except urllib.error.HTTPError:
        return True  # Reached GitHub; the status code doesn't matter here.
    except (urllib.error.URLError, TimeoutError):
        return False


@pytest.fixture(scope="module")
def require_github_network():
    """Skip offline, but never silently pass in CI — a blip there is a real signal."""
    if os.environ.get("GAIA_SKIP_NETWORK_TESTS"):
        pytest.skip("GAIA_SKIP_NETWORK_TESTS is set")
    if not _github_reachable():
        if os.environ.get("CI"):
            pytest.fail("github.com unreachable in CI - cannot verify release assets")
        pytest.skip("github.com unreachable - skipping live asset check")


@pytest.mark.network
def test_every_supported_target_resolves(require_github_network):
    """Every installer URL GAIA can build must return HTTP 200 upstream."""
    failures = []
    for system, machine, minimal, expected_asset in SUPPORTED_TARGETS:
        installer = make_installer(system, machine, minimal)
        url = installer.get_download_url()
        if not url.endswith(expected_asset):
            failures.append(
                f"{system}/{machine}: built {url}, expected {expected_asset}"
            )
            continue
        try:
            installer.verify_download_url(timeout=30)
        except RuntimeError as e:
            failures.append(f"{system}/{machine}: {e}")

    assert not failures, (
        f"Lemonade v{LEMONADE_VERSION} installer assets did not resolve upstream. "
        "Upstream likely renamed an asset - check "
        f"https://github.com/lemonade-sdk/lemonade/releases/tag/v{LEMONADE_VERSION}\n"
        + "\n".join(failures)
    )
