# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Check the pinned Lemonade checksums against the live upstream release.

GAIA verifies the embeddable Lemonade download against SHA-256 digests pinned
in ``gaia.llm.lemonade_embedded.EMBEDDABLE_SHA256``. Pins are only worth
anything if they match what GitHub actually serves, and a ``LEMONADE_VERSION``
bump that forgets to refresh them would otherwise fail on a user's machine
mid-install rather than in CI.

The unit tests assert the pins are *present* and well-formed with no network.
This one asks GitHub what the digests really are.

Skips when GitHub is unreachable (offline dev box), rate-limits the API, or
``GAIA_SKIP_NETWORK_TESTS`` is set. With ``GAIA_REQUIRE_RELEASE_API`` set (the
CI run on a Lemonade bump PR), the first two fail instead of skipping.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from gaia.installer.lemonade_installer import INSTALLER_SHA256, LEGACY_MSI_SHA256
from gaia.llm.lemonade_embedded import _ASSET_TEMPLATES, EMBEDDABLE_SHA256
from gaia.version import LEMONADE_MIN_VERSION, LEMONADE_VERSION, parse_version

RELEASE_API = (
    "https://api.github.com/repos/lemonade-sdk/lemonade/releases/tags/v{version}"
)
RELEASE_TAGS = (
    Path(__file__).resolve().parents[1] / "fixtures" / "lemonade" / "release_tags.txt"
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
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        # ConnectionError covers http.client.RemoteDisconnected, which escapes
        # URLError - see urllib.request.AbstractHTTPHandler.do_open.
        return False


def _cannot_check(reason: str) -> None:
    """Skip, unless this run exists to check the pins against upstream."""
    if os.environ.get("GAIA_REQUIRE_RELEASE_API"):
        pytest.fail(
            f"{reason}, and GAIA_REQUIRE_RELEASE_API is set: this run must check "
            "the Lemonade pins against the live release API. Re-run the job."
        )
    pytest.skip(reason)


def _get_json(url: str):
    """GET a GitHub API URL, treating a rate limit (403 or 429) as unable to check."""
    headers = {"User-Agent": "GAIA/1.0"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            _cannot_check(f"GitHub API rate limit reached (HTTP {e.code})")
        raise


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
        _cannot_check("GitHub is unreachable")
    release = _get_json(RELEASE_API.format(version=LEMONADE_VERSION))

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


@pytest.mark.parametrize("key", sorted(INSTALLER_SHA256))
def test_pinned_installer_digest_matches_the_published_asset(published_digests, key):
    """Every installer download_installer() can fetch is pinned to the real digest."""
    tag, name = key.split("/", 1)
    assert tag == f"v{LEMONADE_VERSION}", (
        f"INSTALLER_SHA256 pins {key}, but GAIA installs v{LEMONADE_VERSION}. "
        "Re-pin every installer asset for the new version."
    )
    assert name in published_digests, (
        f"Release v{LEMONADE_VERSION} publishes no asset named '{name}' (or no "
        f"digest for it). Published: {sorted(published_digests)}"
    )
    assert INSTALLER_SHA256[key] == published_digests[name], (
        f"Pinned digest for '{key}' is stale. Update INSTALLER_SHA256 to "
        f"'{published_digests[name]}'."
    )


@pytest.fixture(scope="module")
def every_release():
    """Every release lemonade-sdk/lemonade has published, as the API returns it."""
    if not _github_reachable():
        _cannot_check("GitHub is unreachable")
    every = []
    page = 1
    while True:
        releases = _get_json(
            "https://api.github.com/repos/lemonade-sdk/lemonade/releases"
            f"?per_page=100&page={page}"
        )
        if not releases:
            return every
        every.extend(releases)
        page += 1


@pytest.fixture(scope="module")
def every_release_digest(every_release):
    """Map ``v<tag>/<asset>`` -> sha256 hex for every published release."""
    digests = {}
    for release in every_release:
        for asset in release.get("assets", []):
            digest = asset.get("digest") or ""
            if digest.startswith("sha256:"):
                key = f"{release['tag_name']}/{asset['name']}"
                digests[key] = digest.split(":", 1)[1]
    return digests


def test_release_tag_list_names_every_supported_release(every_release):
    """The unit tests demand MSI pins for each tag this list names."""
    listed = {
        line.strip()
        for line in RELEASE_TAGS.read_text(encoding="utf-8").splitlines()
        if line.strip() and line[0] != "#"
    }
    oldest = parse_version(LEMONADE_MIN_VERSION)
    newest = parse_version(LEMONADE_VERSION)
    supported = {
        release["tag_name"]
        for release in every_release
        if not release["draft"]
        and not release["prerelease"]
        and release["tag_name"].startswith("v")
        and oldest <= (parse_version(release["tag_name"]) or ()) <= newest
    }
    missing = sorted(supported - listed, key=parse_version)
    assert not missing, (
        f"{RELEASE_TAGS} is missing {missing}. Add each tag, and pin both of its "
        "MSIs in LEGACY_MSI_SHA256 (src/gaia/installer/lemonade_installer.py)."
    )


@pytest.mark.parametrize("key", sorted(LEGACY_MSI_SHA256))
def test_legacy_msi_pin_matches_the_published_asset(every_release_digest, key):
    """Uninstalling an older Lemonade verifies its MSI against these pins."""
    assert key in every_release_digest, f"No published digest for {key}."
    assert LEGACY_MSI_SHA256[key] == every_release_digest[key], (
        f"LEGACY_MSI_SHA256 pins {key} to a digest the release does not publish "
        f"('{every_release_digest[key]}')."
    )


@pytest.fixture
def rate_limited_api(request):
    """A local API stand-in that answers every request with ``request.param``."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    status = request.param

    class _Limited(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Limited)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/releases"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("rate_limited_api", [403, 429], indirect=True)
def test_a_rate_limit_skips_locally(rate_limited_api, monkeypatch):
    monkeypatch.delenv("GAIA_REQUIRE_RELEASE_API", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    with pytest.raises(pytest.skip.Exception, match="rate limit"):
        _get_json(rate_limited_api)


@pytest.mark.parametrize("rate_limited_api", [403, 429], indirect=True)
def test_a_rate_limit_fails_a_bump_pr(rate_limited_api, monkeypatch):
    monkeypatch.setenv("GAIA_REQUIRE_RELEASE_API", "1")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    with pytest.raises(pytest.fail.Exception, match="GAIA_REQUIRE_RELEASE_API"):
        _get_json(rate_limited_api)
