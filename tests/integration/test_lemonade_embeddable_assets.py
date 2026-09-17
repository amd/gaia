# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Check the pinned embeddable checksums against the live upstream release.

GAIA verifies the embeddable Lemonade download against SHA-256 digests pinned
in ``gaia.llm.lemonade_embedded.EMBEDDABLE_SHA256``. Pins are only worth
anything if they match what GitHub actually serves, and a ``LEMONADE_VERSION``
bump that forgets to refresh them would otherwise fail on a user's machine
mid-install rather than in CI.

The unit tests assert the pins are *present* and well-formed with no network.
This one asks GitHub what the digests really are.

Skips when GitHub is unreachable (offline dev box) or ``GAIA_SKIP_NETWORK_TESTS``
is set. CI runners always have network, so it runs there.
"""

import json
import os
import urllib.error
import urllib.request

import pytest

from gaia.llm.lemonade_embedded import _ASSET_TEMPLATES, EMBEDDABLE_SHA256
from gaia.version import LEMONADE_VERSION

RELEASE_API = (
    "https://api.github.com/repos/lemonade-sdk/lemonade/releases/tags/v{version}"
)


def _github_reachable() -> bool:
    """Whether api.github.com answers at all."""
    request = urllib.request.Request(
        "https://api.github.com",
        method="HEAD",
        headers={"User-Agent": "GAIA/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            return True
    except urllib.error.HTTPError:
        return True  # Reached GitHub; the status code doesn't matter here.
    except (urllib.error.URLError, TimeoutError):
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        bool(os.environ.get("GAIA_SKIP_NETWORK_TESTS")),
        reason="GAIA_SKIP_NETWORK_TESTS is set",
    ),
]


@pytest.fixture(scope="module")
def published_digests():
    """Map asset name -> sha256 hex digest, as published for LEMONADE_VERSION."""
    if not _github_reachable():
        pytest.skip("GitHub is unreachable")

    url = RELEASE_API.format(version=LEMONADE_VERSION)
    request = urllib.request.Request(url, headers={"User-Agent": "GAIA/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            release = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            pytest.skip("GitHub API rate limit reached")
        raise

    digests = {}
    for asset in release.get("assets", []):
        digest = asset.get("digest") or ""
        if digest.startswith("sha256:"):
            digests[asset["name"]] = digest.split(":", 1)[1]
    return digests


@pytest.mark.parametrize("template", sorted(_ASSET_TEMPLATES.values()))
def test_pinned_digest_matches_the_published_asset(published_digests, template):
    """Every embeddable asset GAIA can download is pinned to the real digest."""
    name = template.format(version=LEMONADE_VERSION)

    assert name in published_digests, (
        f"Release v{LEMONADE_VERSION} publishes no asset named '{name}' (or no "
        f"digest for it). Published: {sorted(published_digests)}"
    )
    pinned = EMBEDDABLE_SHA256.get(name)
    assert pinned is not None, (
        f"No pinned SHA-256 for '{name}'. Add "
        f"'{name}': '{published_digests[name]}' to EMBEDDABLE_SHA256."
    )
    assert pinned == published_digests[name], (
        f"Pinned digest for '{name}' is stale. Update EMBEDDABLE_SHA256 to "
        f"'{published_digests[name]}'."
    )
