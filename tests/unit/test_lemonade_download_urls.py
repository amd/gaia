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

    def test_head_rejection_falls_through_to_the_real_download(
        self, tmp_path, monkeypatch
    ):
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

        _pin(monkeypatch, installer, b"deb-bytes")
        with patch("urllib.request.urlopen", side_effect=_urlopen):
            dest = installer.download_installer(dest_dir=str(tmp_path))

        assert calls == ["HEAD", "GET"]
        assert dest.read_bytes() == b"deb-bytes"


class _Stream:
    """A GET response that serves *payload* but may claim another length."""

    status = 200

    def __init__(self, payload: bytes, claimed_length=None, chunk=4096):
        self._payload = payload
        self._chunk = chunk
        length = len(payload) if claimed_length is None else claimed_length
        self.headers = {"content-length": str(length)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size):
        size = min(size, self._chunk)
        chunk, self._payload = self._payload[:size], self._payload[size:]
        return chunk


def _serve(payload, claimed_length=None):
    def _urlopen(request, timeout=None):
        if request.get_method() == "HEAD":
            return _Stream(b"")
        return _Stream(payload, claimed_length)

    return patch("urllib.request.urlopen", side_effect=_urlopen)


def _release_key(installer) -> str:
    return installer.get_download_url().split("/releases/download/", 1)[1]


def _pin(monkeypatch, installer, payload):
    import hashlib

    from gaia.installer import lemonade_installer as mod

    # getattr: lets the same test show the unpinned behaviour of older builds.
    monkeypatch.setitem(
        getattr(mod, "INSTALLER_SHA256", {}),
        _release_key(installer),
        hashlib.sha256(payload).hexdigest(),
    )


class TestDownloadIntegrity:
    """A truncated, corrupt or shared download never reaches the installer."""

    def test_every_supported_asset_has_a_pinned_checksum(self):
        from gaia.installer.lemonade_installer import INSTALLER_SHA256

        for system, machine, minimal, _asset in SUPPORTED_TARGETS:
            key = _release_key(_installer(system, machine, minimal))
            assert key in INSTALLER_SHA256, f"no pinned SHA-256 for {key}"

    def test_truncated_download_fails_and_leaves_no_file(self, tmp_path, monkeypatch):
        installer = _installer("Windows", "AMD64")
        payload = b"m" * 10_000
        _pin(monkeypatch, installer, payload)
        with _serve(payload[:6_000], claimed_length=len(payload)):
            with pytest.raises(RuntimeError, match="6000 of 10000 bytes"):
                installer.download_installer(dest_dir=str(tmp_path))
        assert list(tmp_path.iterdir()) == []

    def test_checksum_mismatch_fails_and_leaves_no_file(self, tmp_path, monkeypatch):
        installer = _installer("Windows", "AMD64")
        _pin(monkeypatch, installer, b"the real installer")
        with _serve(b"a tampered installer"):
            with pytest.raises(RuntimeError, match="SHA-256"):
                installer.download_installer(dest_dir=str(tmp_path))
        assert list(tmp_path.iterdir()) == []

    def test_unpinned_version_refuses_before_downloading(self, tmp_path):
        from gaia.installer.lemonade_installer import LemonadeInstaller

        with (
            patch("platform.system", return_value="Windows"),
            patch("platform.machine", return_value="AMD64"),
        ):
            installer = LemonadeInstaller(target_version="1.2.3")
        with patch("urllib.request.urlopen") as urlopen:
            with pytest.raises(RuntimeError, match="No pinned SHA-256"):
                installer.download_installer(dest_dir=str(tmp_path))
        urlopen.assert_not_called()

    def test_concurrent_downloads_never_share_a_file(self, tmp_path, monkeypatch):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        installer = _installer("Windows", "AMD64")
        payload = bytes(range(256)) * 400
        _pin(monkeypatch, installer, payload)
        both_streaming = threading.Barrier(2, timeout=30)

        class _Lockstep(_Stream):
            def read(self, size):
                chunk = super().read(size)
                if chunk:
                    both_streaming.wait()
                return chunk

        def _urlopen(request, timeout=None):
            if request.get_method() == "HEAD":
                return _Stream(b"")
            return _Lockstep(payload, chunk=1024)

        def _download(_):
            return installer.download_installer(dest_dir=str(tmp_path))

        with patch("urllib.request.urlopen", side_effect=_urlopen):
            with ThreadPoolExecutor(2) as pool:
                paths = list(pool.map(_download, range(2)))

        assert paths[0] != paths[1]
        assert all(p.read_bytes() == payload for p in paths)
        assert all(p.suffix == ".msi" for p in paths)

    def test_interrupted_download_leaves_no_file(self, tmp_path, monkeypatch):
        installer = _installer("Windows", "AMD64")
        _pin(monkeypatch, installer, b"x" * 10_000)

        class _Interrupted(_Stream):
            def read(self, size):
                raise KeyboardInterrupt

        def _urlopen(request, timeout=None):
            if request.get_method() == "HEAD":
                return _Stream(b"")
            return _Interrupted(b"x" * 10_000)

        with patch("urllib.request.urlopen", side_effect=_urlopen):
            with pytest.raises(KeyboardInterrupt):
                installer.download_installer(dest_dir=str(tmp_path))
        assert list(tmp_path.iterdir()) == []

    def test_uninstall_fallback_names_the_unverifiable_installer(self):
        from gaia.installer.lemonade_installer import LemonadeInfo

        installer = _installer("Windows", "AMD64")
        with (
            patch("platform.system", return_value="Windows"),
            patch("platform.machine", return_value="AMD64"),
            patch.object(installer, "wait_for_msi_mutex", return_value=True),
            patch.object(installer, "find_product_code", return_value=None),
            patch.object(
                installer,
                "check_installation",
                return_value=LemonadeInfo(installed=True, version="v1.2.3"),
            ),
            patch("urllib.request.urlopen") as urlopen,
            patch("subprocess.run") as run,
        ):
            result = installer.uninstall()

        assert not result.success
        assert "No pinned SHA-256" in result.error
        assert "minimal MSI" in result.error and "full MSI" in result.error
        urlopen.assert_not_called()
        run.assert_not_called()
