# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Shape tests for the Lemonade installer download URLs.

These are hermetic: they assert the URL/filename built for every (platform,
arch) pair we claim to support, and that unsupported ones fail loudly. A
mocked HTTP layer only proves we *called* something, so the companion test
``tests/integration/test_lemonade_release_assets.py`` resolves the same URLs
against the live upstream release — that is the one that catches an asset
rename (the .deb gained a ``debian13`` infix and the old name now 404s).
"""

import urllib.error
from unittest.mock import patch

import pytest

from gaia.installer.lemonade_installer import LemonadeAssetError
from gaia.version import LEMONADE_VERSION
from tests.fixtures.lemonade_assets import SUPPORTED_TARGETS
from tests.fixtures.lemonade_assets import make_installer as _installer


class TestDownloadUrlShape:
    """The URL/filename we build for each supported target."""

    @pytest.mark.parametrize(
        "system,machine,minimal,expected_asset",
        SUPPORTED_TARGETS,
        ids=lambda v: str(v),
    )
    def test_url_ends_with_expected_asset(
        self, system, machine, minimal, expected_asset
    ):
        installer = _installer(system, machine, minimal)
        url = installer.get_download_url()
        assert url == (
            "https://github.com/lemonade-sdk/lemonade/releases/download/"
            f"v{LEMONADE_VERSION}/{expected_asset}"
        )

    @pytest.mark.parametrize(
        "system,machine,minimal,expected_asset",
        SUPPORTED_TARGETS,
        ids=lambda v: str(v),
    )
    def test_filename_matches_url_basename(
        self, system, machine, minimal, expected_asset
    ):
        installer = _installer(system, machine, minimal)
        assert installer.get_installer_filename() == expected_asset

    def test_linux_deb_carries_distro_infix(self):
        """Regression guard: the infix-less name 404s upstream."""
        url = _installer("Linux", "x86_64").get_download_url()
        assert "-debian13_amd64.deb" in url
        assert f"lemonade-server_{LEMONADE_VERSION}_amd64.deb" not in url

    def test_macos_yields_pkg(self):
        url = _installer("Darwin", "arm64").get_download_url()
        assert url.endswith(".pkg")

    def test_intel_mac_raises_rather_than_installing_arm64_binaries(self):
        """The .pkg payload is arm64-only and installs happily on Intel."""
        installer = _installer("Darwin", "x86_64")
        with pytest.raises(RuntimeError) as exc:
            installer.get_download_url()
        message = str(exc.value)
        assert "x86_64" in message
        assert "Apple-Silicon-only" in message

    def test_unsupported_arch_raises_naming_the_arch(self):
        installer = _installer("Linux", "armv7l")
        with pytest.raises(RuntimeError) as exc:
            installer.get_download_url()
        message = str(exc.value)
        assert "armv7l" in message
        assert "amd64" in message and "arm64" in message
        assert f"releases/tag/v{LEMONADE_VERSION}" in message

    def test_unsupported_platform_raises(self):
        installer = _installer("FreeBSD", "x86_64")
        with pytest.raises(RuntimeError, match="not supported"):
            installer.get_download_url()


class TestVerifyDownloadUrl:
    """verify_download_url turns an upstream rename into an actionable error."""

    def test_404_raises_with_url_version_and_asset_list(self):
        installer = _installer("Linux", "x86_64")
        url = installer.get_download_url()
        error = urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        with patch("urllib.request.urlopen", side_effect=error):
            with pytest.raises(LemonadeAssetError) as exc:
                installer.verify_download_url()

        message = str(exc.value)
        assert exc.value.definitive is True
        assert url in message
        assert LEMONADE_VERSION in message
        assert f"releases/tag/v{LEMONADE_VERSION}" in message

    def test_unreachable_host_blames_the_network_not_a_rename(self):
        installer = _installer("Darwin", "arm64")
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("nodename nor servname provided"),
        ):
            with pytest.raises(LemonadeAssetError) as exc:
                installer.verify_download_url()

        message = str(exc.value)
        assert exc.value.definitive is False
        assert installer.get_download_url() in message
        assert "network" in message or "proxy" in message
        assert "renamed" not in message

    def test_timeout_is_wrapped_not_leaked(self):
        installer = _installer("Linux", "x86_64")
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            with pytest.raises(LemonadeAssetError) as exc:
                installer.verify_download_url(timeout=1)
        assert exc.value.definitive is False

    def test_missing_asset_writes_nothing_to_disk(self, tmp_path):
        """The 404 must stop the download before the file is created."""
        installer = _installer("Linux", "x86_64")
        url = installer.get_download_url()
        error = urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        with patch("urllib.request.urlopen", side_effect=error):
            with pytest.raises(LemonadeAssetError):
                installer.download_installer(dest_dir=str(tmp_path))

        assert list(tmp_path.iterdir()) == []

    def test_head_rejection_falls_through_to_the_real_download(self, tmp_path):
        """A proxy that 403s HEAD must not block a GET that would succeed."""
        installer = _installer("Linux", "x86_64")
        url = installer.get_download_url()
        payload = b"deb-bytes"

        class _Response:
            headers = {"content-length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, _size):
                nonlocal payload
                chunk, payload = payload, b""
                return chunk

        calls = []

        def _urlopen(request, timeout=None):
            calls.append(request.get_method())
            if request.get_method() == "HEAD":
                raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
            return _Response()

        with patch("urllib.request.urlopen", side_effect=_urlopen):
            dest = installer.download_installer(dest_dir=str(tmp_path))

        assert calls == ["HEAD", "GET"]
        assert dest.read_bytes() == b"deb-bytes"
