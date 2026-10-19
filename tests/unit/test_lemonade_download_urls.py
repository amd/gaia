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

    def test_every_pin_is_a_sha256_for_a_supported_release(self):
        import re

        from gaia.installer.lemonade_installer import (
            INSTALLER_SHA256,
            LEGACY_MSI_SHA256,
        )
        from gaia.version import LEMONADE_MIN_VERSION, parse_version

        for key, digest in {**INSTALLER_SHA256, **LEGACY_MSI_SHA256}.items():
            assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{key}: {digest!r}"
        for key in LEGACY_MSI_SHA256:
            tag, name = key.split("/", 1)
            version = parse_version(tag.lstrip("v"))
            assert parse_version(LEMONADE_MIN_VERSION) <= version
            assert version < parse_version(LEMONADE_VERSION), key
            assert name in ("lemonade.msi", "lemonade-server-minimal.msi"), key

    def test_an_older_supported_release_downloads_against_its_legacy_pin(
        self, tmp_path, monkeypatch
    ):
        from gaia.installer import lemonade_installer as mod

        with (
            patch("platform.system", return_value="Windows"),
            patch("platform.machine", return_value="AMD64"),
        ):
            installer = mod.LemonadeInstaller(target_version="10.9.0")
        payload = b"old msi"
        monkeypatch.setitem(
            mod.LEGACY_MSI_SHA256,
            "v10.9.0/lemonade.msi",
            __import__("hashlib").sha256(payload).hexdigest(),
        )
        with _serve(payload):
            dest = installer.download_installer(dest_dir=str(tmp_path))
        assert dest.read_bytes() == payload


class TestPartialDownloadIsRemoved:
    """No exit from a failed download leaves a file behind, Ctrl-C included."""

    def _failing(self, error):
        class _Breaks(_Stream):
            def read(self, size):
                raise error

        def _urlopen(request, timeout=None):
            if request.get_method() == "HEAD":
                return _Stream(b"")
            if isinstance(error, urllib.error.URLError):
                raise error
            return _Breaks(b"")

        return patch("urllib.request.urlopen", side_effect=_urlopen)

    @pytest.mark.parametrize(
        "error,expected",
        [
            (
                urllib.error.HTTPError("u", 404, "Not Found", {}, None),
                LemonadeAssetError,
            ),
            (urllib.error.URLError("no route to host"), RuntimeError),
            (ConnectionResetError("reset by peer"), RuntimeError),
            (ValueError("something else"), RuntimeError),
            (KeyboardInterrupt(), KeyboardInterrupt),
        ],
        ids=["404", "urlerror", "dropped", "generic", "ctrl-c"],
    )
    def test_failure_leaves_no_file(self, tmp_path, monkeypatch, error, expected):
        installer = _installer("Windows", "AMD64")
        _pin(monkeypatch, installer, b"x")
        with self._failing(error):
            with pytest.raises(expected):
                installer.download_installer(dest_dir=str(tmp_path))
        assert list(tmp_path.iterdir()) == []


class TestDownloadedInstallerLifecycle:
    """The CLI and uninstall delete what they downloaded, except to retry."""

    def _run_cli_install(self, tmp_path, monkeypatch, success):
        from gaia.installer import lemonade_installer as mod

        downloaded = tmp_path / "Lemonade-Darwin-abc.pkg"

        class _Fake:
            system = "darwin"

            def check_installation(self):
                return mod.LemonadeInfo(installed=success)

            def needs_install(self, info):
                return True

            def download_installer(self):
                downloaded.write_bytes(b"pkg")
                return downloaded

            def install(self, path, silent=False):
                if success:
                    return mod.InstallResult(success=True, version="1")
                return mod.InstallResult(
                    success=False, error=f"Retry manually: sudo installer -pkg {path}"
                )

        monkeypatch.setattr(mod, "LemonadeInstaller", _Fake)
        monkeypatch.setattr(
            "sys.argv", ["gaia", "install", "--lemonade", "--yes"], raising=True
        )
        from gaia.cli import main

        with pytest.raises(SystemExit) as exit_:
            main()
        return downloaded, exit_.value.code

    def test_cli_deletes_the_installer_after_a_successful_install(
        self, tmp_path, monkeypatch
    ):
        downloaded, code = self._run_cli_install(tmp_path, monkeypatch, success=True)
        assert code == 0
        assert not downloaded.exists()

    def test_cli_keeps_the_installer_its_retry_hint_names(
        self, tmp_path, monkeypatch, capsys
    ):
        downloaded, code = self._run_cli_install(tmp_path, monkeypatch, success=False)
        assert code == 1
        assert downloaded.exists()
        out = capsys.readouterr().out
        assert f"sudo installer -pkg {downloaded}" in out
        assert f"kept at {downloaded}" in out

    def _uninstaller(self, monkeypatch, version="10.9.0"):
        from gaia.installer import lemonade_installer as mod

        installer = _installer("Windows", "AMD64")
        monkeypatch.setattr(installer, "wait_for_msi_mutex", lambda timeout: True)
        monkeypatch.setattr(installer, "find_product_code", lambda: None)
        monkeypatch.setattr(
            installer,
            "check_installation",
            lambda: mod.LemonadeInfo(installed=True, version=version),
        )
        return mod, installer

    def test_uninstall_deletes_the_msi_it_downloaded(self, tmp_path, monkeypatch):
        mod, installer = self._uninstaller(monkeypatch)
        msi = tmp_path / "lemonade-server-minimal-abc.msi"

        def fake_download(self):
            msi.write_bytes(b"msi")
            return msi

        ran = []
        monkeypatch.setattr(mod.LemonadeInstaller, "download_installer", fake_download)
        monkeypatch.setattr(
            mod.subprocess,
            "run",
            lambda cmd, **kw: ran.append(cmd)
            or __import__("types").SimpleNamespace(returncode=0, stderr=""),
        )
        result = installer._uninstall_windows(silent=True)
        assert result.success, result.error
        assert ran and ran[0][:2] == ["msiexec", "/x"]
        assert not msi.exists()

    def test_uninstall_of_an_unpinned_version_names_the_version_and_the_way_out(
        self, monkeypatch
    ):
        mod, installer = self._uninstaller(monkeypatch, version="9.9.9")
        monkeypatch.setattr(
            mod.subprocess, "run", lambda *a, **k: pytest.fail("ran an installer")
        )
        with patch("urllib.request.urlopen") as urlopen:
            result = installer._uninstall_windows(silent=True)
        urlopen.assert_not_called()
        assert not result.success
        assert "v9.9.9" in result.error
        assert "No pinned SHA-256" in result.error
        assert "Windows Settings > Apps" in result.error

    def test_uninstall_refuses_an_msi_that_fails_its_checksum(
        self, tmp_path, monkeypatch
    ):
        mod, installer = self._uninstaller(monkeypatch)
        monkeypatch.setattr(
            mod.subprocess, "run", lambda *a, **k: pytest.fail("ran an installer")
        )
        monkeypatch.setattr(mod.tempfile, "gettempdir", lambda: str(tmp_path))
        with _serve(b"not the real msi"):
            result = installer._uninstall_windows(silent=True)
        assert not result.success
        assert "SHA-256" in result.error
        assert "v10.9.0" in result.error
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
        assert "v1.2.3" in result.error
        urlopen.assert_not_called()
        run.assert_not_called()
