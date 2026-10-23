# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Lemonade Server Installer

Handles detection, download, and installation of Lemonade Server
from GitHub releases for Windows, Linux, and macOS.
"""

import hashlib
import logging
import ntpath
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from gaia.llm.lemonade_launcher import get_installed_version, resolve_lemonade
from gaia.version import LEMONADE_VERSION, parse_version

log = logging.getLogger(__name__)

# Rich imports for console output
try:
    from rich.console import Console  # pylint: disable=unused-import

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    Console = None  # type: ignore

# GitHub release URL patterns
GITHUB_RELEASE_BASE = "https://github.com/lemonade-sdk/lemonade/releases/download"
GITHUB_RELEASE_TAG_BASE = "https://github.com/lemonade-sdk/lemonade/releases/tag"

# Upstream .deb assets carry a distro infix: lemonade-server_<ver>-debian13_<arch>.deb
LINUX_DEB_DISTRO_TAG = "debian13"

# SHA-256 of every installer asset GAIA downloads for LEMONADE_VERSION, keyed by
# its path under GITHUB_RELEASE_BASE, as the GitHub release API publishes them.
# The MSI names carry no version, so the key does. Bumping LEMONADE_VERSION
# without refreshing these makes download_installer() fail loudly;
# tests/integration/test_lemonade_embeddable_assets.py checks them against the
# live release.
INSTALLER_SHA256 = {
    "v2026.43.3/lemonade.msi": "95227ef9013678ec3f4d0a26c10bfde261373a4fd14cf6ec5fad6a81ed9dd71d",
    "v2026.43.3/lemonade-server-minimal.msi": "ae82d13d6f7dc057c57425c959d5746184fb834c569de871c573606ea5a8980a",
    "v2026.43.3/lemonade-server_2026.43.3-debian13_amd64.deb": "a9260c96f7a9d0d41ad1a599904e091b7abddb33d16d675abec5bab4faf57416",
    "v2026.43.3/lemonade-server_2026.43.3-debian13_arm64.deb": "57f784251207031c96c232bbbdbf14efb4628a0b70da4709d6e029f463a7b062",
    "v2026.43.3/Lemonade-2026.43.3-Darwin.pkg": "ed71f7ef595e499244bc9b0b50186c24b7d10ab125667db8ee562df60464070e",
}

# The Windows installers of every older release GAIA supports (back to
# LEMONADE_MIN_VERSION). Uninstalling an older Lemonade can fall back to
# downloading its MSI, which is verified like any other download. Append-only:
# a version bump moves the outgoing version's two MSI pins here, and pins any
# release it skips. tests/fixtures/lemonade/release_tags.txt lists them all.
LEGACY_MSI_SHA256 = {
    "v10.2.0/lemonade-server-minimal.msi": "d4c368d02c124df406c25c4e477cf6c4da7d5d635b8e5e7ba1a9acf80eba3899",
    "v10.2.0/lemonade.msi": "333d8b11efd3c023fe7c526c41f07bcf2100a94f89078e95f24ed710a9bcb5c7",
    "v10.3.0/lemonade-server-minimal.msi": "221b0f8867e55366b538f82485a13f381b3a677c988f8cc651c7c6ac09f87b20",
    "v10.3.0/lemonade.msi": "51bd15a6a622619d2786d708bce9c0fb274b0f1e037d1bd529831d0f5d6bd2de",
    "v10.4.0/lemonade-server-minimal.msi": "690a8a0a72ada5abb1dd80f023dee35ff40985c4aee1bf2ca33a9845cf4de000",
    "v10.4.0/lemonade.msi": "20f405484a64e7fb79e9f09d087460abe0ffe4f732a4f069b99c42a06951402b",
    "v10.5.0/lemonade-server-minimal.msi": "ccc364bcc892159573d487857dbc9904c7a419fc41ec5fe25b13ac0ea8c5514d",
    "v10.5.0/lemonade.msi": "36c52861703c00a226ca19404e6398d54a2917f623b00dee233ac2def53d58d6",
    "v10.5.1/lemonade-server-minimal.msi": "44e569b04fb31583e700b6b647b9d6447e71a42c00c7fa80bd8ee489b6a0b50d",
    "v10.5.1/lemonade.msi": "870af98831dbdb5b5f620895b5709bbc4fff528443007f4127c156980442bb44",
    "v10.6.0/lemonade-server-minimal.msi": "e3a3d6c426b543af5944614444754adda3fce5851698bde56279a837fd80559a",
    "v10.6.0/lemonade.msi": "8682d96db5d990385b892539c3f88c6eaded3e42ec6b8ab1779447e31d21ab6c",
    "v10.7.0/lemonade-server-minimal.msi": "7b54969af637b250f4185390beb9670a81b880127e5808b4e1e4e55aadf49164",
    "v10.7.0/lemonade.msi": "4b2f61d4fa8c501e278a40c77d4c847194af93f7bbfb9b1c38f04dd9bc2a8977",
    "v10.8.0/lemonade-server-minimal.msi": "e2b54f5a523f9da0fa01bd36cf11795680ece705b127b150ee6590ea0f1e46b7",
    "v10.8.0/lemonade.msi": "81118e6a97c2b537216cf49a0a4b74463b97ed02a41ea0aa05e2928446fba979",
    "v10.8.1/lemonade-server-minimal.msi": "741072f1ec1d1a9666d7ee0f0e42cc24d8c37c17469fbe5d78d63277ccc65a31",
    "v10.8.1/lemonade.msi": "73b8869ff49e818a54efa9587ef7dd14777a172f7b90325fe1845388a5c5f408",
    "v10.9.0/lemonade-server-minimal.msi": "4293f6732810566b63eef66d28b4b9ed4cf97bc08da9810c124bfe57bbf1dcfd",
    "v10.9.0/lemonade.msi": "047b4b69a6b02f4a4f9fe8db00af9a9cabc72198b48c8096cfbee1a602157e8b",
    "v10.10.0/lemonade-server-minimal.msi": "9699024ec430785cabbc623a0c55b2da8001c1c4776f5f1a0c66cddca0210eb3",
    "v10.10.0/lemonade.msi": "e3307e1077a56819044afe64c884b3346d87404059ce1b976b0c37353934347b",
    "v11.0.0/lemonade-server-minimal.msi": "771b9df062017b30af1bf2b804afc1f0c80a3499bbe7d60ea51393b73e38f521",
    "v11.0.0/lemonade.msi": "ecf07bda142f72e742d81e461e2fc3cbe7fa5f6e7348f5b6dc94ba918ec731bb",
    "v11.5.0/lemonade-server-minimal.msi": "4611119c862a33f7d9e8cefab16b7a2d4ebac000064ba5310e2b7bc6a9ee72f4",
    "v11.5.0/lemonade.msi": "29c44042033205b9e993c5d9111e4581465d57f364af4a556e0e7b835b1619bc",
    "v11.5.1/lemonade-server-minimal.msi": "5b241c166942c8a0511b199b8412b8dc105d7ef8fb32aac67878732573b32fea",
    "v11.5.1/lemonade.msi": "62380b92ec6ea2c5b01961d4ea11c41af83d2aa94f80b5835895efd5369f58aa",
    "v11.5.2/lemonade-server-minimal.msi": "2e6329f538ccaca30a81c4dac51f7e88c6c9864ad868a33f66c00634f65a8fe0",
    "v11.5.2/lemonade.msi": "62cccf94cf4bfd4abd99c2a24101901bb1078640e2973aee3594a9c8a9e578b2",
    "v11.6.0/lemonade-server-minimal.msi": "1fd3f77691d168c57e9552488dcb293e9f7e783f0ee00e3b9df44c8740a31549",
    "v11.6.0/lemonade.msi": "76f2dc7215515ebc5f9dc849ab37e467ff7abc8ef6af3577d5c8b25f35175612",
    "v11.7.0/lemonade-server-minimal.msi": "8b59d9e9a9faa7f3e6f84fb2497681d585cd661ef9037711d9ae570a1fc95927",
    "v11.7.0/lemonade.msi": "1b5941ca2885c839cf7b3359dd3e15ee28658fe4d23c825bf14c60f78c83639b",
    "v11.8.0/lemonade-server-minimal.msi": "eac1e709c12fa7a4ca03af5d9a4e992000f9d6ae6c5663b8be746f059f1f9f7b",
    "v11.8.0/lemonade.msi": "8466f9d423f26273026e0cec7e5aef574a4a2c82293d897b9d01ae5f4465aec1",
    "v11.8.1/lemonade-server-minimal.msi": "e9f993b541c7495d18b6ee77a683df8d533dba70442ae36cee4b02cf952c95a1",
    "v11.8.1/lemonade.msi": "1f0510a2dbe62b7456b80da635f2f123cdf8e25a08feeaa4863e4203c9aa2bb5",
    "v11.9.0/lemonade-server-minimal.msi": "d194c67b3f8a9bcc8e3ed1109c512191ec82130190fba61a0e5adac44efceb98",
    "v11.9.0/lemonade.msi": "f105e07e12c8c5fa2f54807532ed6fdda13708ca00a3619f24fcb3b7f08c4451",
    "v2026.39.1/lemonade-server-minimal.msi": "0a6bdc9e49ef819e17f29d4432fa1c78f363cb086d64ffd661b1553de16f09b7",
    "v2026.39.1/lemonade.msi": "8e778d01b6e5fe10dbee6bc017e58b1b9b35758b7507c1b188d99ba77906d838",
    "v2026.40.0/lemonade-server-minimal.msi": "95ad5b18eba1c1b42228ebceefcdd2eb949d2dde6bb5405141e2cbc169260d66",
    "v2026.40.0/lemonade.msi": "e10bc07d69e668338bf01e1a599b4f6e684bf7d217f14cb3744668291bd8bbf0",
    "v2026.41.1/lemonade-server-minimal.msi": "b545bb3fe0dcffc91f9635bbdbac7dff049c9bbeae1616da6321382a486782f4",
    "v2026.41.1/lemonade.msi": "cb8753fef41937baedd24dc949ab332d914f537dd597cb62efe54f5448825314",
    "v2026.42.1/lemonade-server-minimal.msi": "463e76b01de44d3ffac87bfc29405a933246e149ea435f5eac6aea4b944476f3",
    "v2026.42.1/lemonade.msi": "94ec399d71fe0d3fa4386aca440374f167c27d2dd0479055c8d8b3da869058d4",
}

# platform.machine() varies by OS/libc for the same hardware.
DEB_ARCH_BY_MACHINE = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
}

# Lemonade's only supported Linux channel (lemonade-sdk was removed from PyPI).
LEMONADE_PPA = "ppa:lemonade-team/stable"

# The archive host add-apt-repository points apt at for LEMONADE_PPA.
_PPA_INDEX_HOST = "ppa.launchpadcontent.net/lemonade-team"

# Raised by Launchpad's REST API when it cannot serve a PPA's signing key.
_LAUNCHPAD_KEY_ERROR = "GPGKeyTemporarilyNotFoundError"

_PPA_FETCH_FAILURE = re.compile(
    r"Failed to fetch \S*" + re.escape(_PPA_INDEX_HOST) + r"\S*[ \t]*(?P<reason>[^\n]*)"
)


def _launchpad_outage_message(what_failed: str) -> str:
    return (
        f"{what_failed}\n"
        "This is an outage on Launchpad's side — nothing is wrong with this machine "
        "or with your apt state. Wait a few minutes and re-run: gaia init\n"
        "Launchpad status: https://status.canonical.com/ — if it persists, see "
        "https://amd-gaia.ai/docs/reference/troubleshooting"
    )


def diagnose_launchpad_outage(output: str) -> Optional[str]:
    """Return an actionable message when Launchpad, not this machine, is at fault.

    ``add-apt-repository -y`` exits 0 even when the PPA index 503s, and
    ``apt-get update`` exits 0 when only some indexes fail. Without this the
    install runs on and dies at ``apt-get install`` with "Unable to locate
    package lemonade-server", which reads as a wrong package name.
    """
    if _LAUNCHPAD_KEY_ERROR in output:
        return _launchpad_outage_message(
            f"Launchpad could not return the signing key for {LEMONADE_PPA} "
            f"({_LAUNCHPAD_KEY_ERROR}), so the repository was not added."
        )

    match = _PPA_FETCH_FAILURE.search(output)
    if match:
        raw = match.group("reason").split("[IP:")[0]
        reason = " ".join(raw.split()) or "no reason given"
        return _launchpad_outage_message(
            f"apt could not fetch the {LEMONADE_PPA} package index from Launchpad "
            f"({reason}), so lemonade-server is not visible to apt."
        )
    return None


class LemonadeAssetError(RuntimeError):
    """A release asset could not be verified.

    ``definitive`` distinguishes "upstream says it's gone" (fatal) from
    "we couldn't ask" (a network or proxy failure the real download may survive).
    """

    def __init__(self, message: str, definitive: bool):
        super().__init__(message)
        self.definitive = definitive


@dataclass
class LemonadeInfo:
    """Information about Lemonade Server installation."""

    installed: bool
    version: Optional[str] = None
    path: Optional[str] = None
    error: Optional[str] = None

    @property
    def version_tuple(self) -> Optional[tuple]:
        """Comparable tuple for the INSTALLED version, or None.

        ``check_installation`` fills ``version`` from ``get_installed_version``,
        which already strips a CalVer dev suffix — but that invariant lives two
        files away and callers build ``LemonadeInfo`` directly, so this does not
        lean on it.
        """
        return parse_version(self.version)


@dataclass
class InstallResult:
    """Result of an installation attempt."""

    success: bool
    version: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    restart_required: bool = False


# msiexec exit codes for a successful install that still needs a reboot.
_MSI_SUCCESS_REBOOT_REQUIRED = (3010, 1641)


class InstallerIntegrityError(RuntimeError):
    """A downloaded installer is truncated or fails its pinned checksum."""


class UnpinnedInstallerError(RuntimeError):
    """No SHA-256 is pinned for this installer, so GAIA will not fetch it."""


class _SaveFailed(Exception):
    """Carries an OSError from writing a download past the network handlers."""

    def __init__(self, error: OSError):
        super().__init__(str(error))
        self.error = error


def discard_download(path: Path) -> None:
    """Delete an installer :meth:`LemonadeInstaller.download_installer` returned."""
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Could not delete the downloaded installer %s: %s", path, e)


# download_installer()'s tempfile names: an installer asset stem, "-", the eight
# characters mkstemp adds, and the asset's suffix.
_KEPT_INSTALLER_NAME = re.compile(
    r"(?:lemonade|lemonade-server-minimal|Lemonade-[0-9.]+-Darwin"
    r"|lemonade-server_[0-9.]+-debian13_(?:amd64|arm64))"
    r"-[a-z0-9_]{8}\.(?:msi|pkg|deb)"
)


# Long enough for another install to finish its download and sit at a password prompt.
KEPT_INSTALLER_MIN_AGE_S = 3600


def discard_kept_installers(directory: Path, older_than: float) -> None:
    """Delete installers that failed installs left in *directory*.

    A file goes only if it was last written before *older_than* (a
    ``time.time()`` value) and more than :data:`KEPT_INSTALLER_MIN_AGE_S` ago,
    so a download another install wrote within the last hour, whether still
    being written or waiting for its installer to run, is left alone.
    """
    cutoff = min(older_than, time.time() - KEPT_INSTALLER_MIN_AGE_S)
    try:
        entries = list(directory.iterdir())
    except OSError as e:
        log.warning("Could not list %s for old Lemonade installers: %s", directory, e)
        return
    for path in entries:
        if not _KEPT_INSTALLER_NAME.fullmatch(path.name):
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
        except FileNotFoundError:
            continue
        except OSError as e:
            log.warning("Could not check the old installer %s: %s", path, e)
            continue
        discard_download(path)


def _remove_by_hand(version: Optional[str]) -> str:
    """How to remove a Windows Lemonade Server when GAIA cannot."""
    page = (
        f"{GITHUB_RELEASE_TAG_BASE}/v{version}"
        if version
        else GITHUB_RELEASE_TAG_BASE.rsplit("/", 1)[0]
    )
    named = f"Lemonade v{version}" if version else "Lemonade"
    return (
        "Remove Lemonade Server from Windows Settings > Apps, or remove it "
        f"with the {named} installer from {page}."
    )


class LemonadeInstaller:
    """Handles Lemonade Server installation and management."""

    def __init__(
        self,
        target_version: str = LEMONADE_VERSION,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        minimal: bool = False,
        console: Optional[Any] = None,
    ):
        """
        Initialize the installer.

        Args:
            target_version: Target Lemonade version to install
            progress_callback: Optional callback for download progress (bytes_downloaded, total_bytes)
            minimal: Use minimal installer (smaller download, fewer features)
            console: Optional Rich Console for user-facing output (suppresses log messages)
        """
        self.target_version = target_version.lstrip("v")
        self.progress_callback = progress_callback
        self.minimal = minimal
        self.system = platform.system().lower()
        self.machine = platform.machine().lower()
        self.console = console

    def _print_status(self, message: str, style: str = "dim"):
        """Print a status message to console or log."""
        if self.console and RICH_AVAILABLE:
            self.console.print(f"   [{style}]{message}[/{style}]")
        elif not self.console:
            # Only log if no console provided (to avoid duplicate output)
            log.debug(message)

    def _announce(self, message: str) -> None:
        """Print a message the user must see, console or not."""
        if self.console and RICH_AVAILABLE:
            self.console.print(f"   [yellow]{message}[/yellow]")
        else:
            print(f"   {message}")

    def refresh_path_from_registry(self) -> None:
        """Add the PATH entries an MSI install just wrote to the registry.

        The process keeps its own PATH first — the active venv and whatever
        the launching shell added — and gains only the registry entries it
        lacks, with ``%VARS%`` expanded.
        """
        if self.system != "windows":
            return
        import winreg

        registry_entries = []
        for root, subkey in (
            (winreg.HKEY_CURRENT_USER, r"Environment"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ),
        ):
            try:
                with winreg.OpenKey(root, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, "Path")
            except OSError:
                continue  # this hive has no Path value
            registry_entries += winreg.ExpandEnvironmentStrings(value).split(";")

        entries = [p for p in os.environ.get("PATH", "").split(";") if p]
        seen = {ntpath.normcase(p.rstrip("\\/")) for p in entries}
        added = []
        for entry in (e.strip() for e in registry_entries):
            key_form = ntpath.normcase(entry.rstrip("\\/"))
            if entry and key_form not in seen:
                seen.add(key_form)
                added.append(entry)
        if added:
            os.environ["PATH"] = ";".join(entries + added)
            log.debug("Added %d PATH entries from the registry", len(added))

    def check_installation(self) -> LemonadeInfo:
        """
        Check if Lemonade Server is installed and get version info.

        Detection is delegated to :func:`gaia.llm.lemonade_launcher.resolve_lemonade`,
        which finds both modern tooling (``LemonadeServer.exe`` / ``lemonade``)
        and the legacy ``lemonade-server`` CLI.

        Returns:
            LemonadeInfo with installation status
        """
        try:
            # Refresh PATH from registry (in case MSI just updated it)
            self.refresh_path_from_registry()

            tooling = resolve_lemonade()

            if not tooling.found:
                return LemonadeInfo(
                    installed=False,
                    error=(
                        "Lemonade Server not found (no modern install at its "
                        "canonical path, no lemonade-server in PATH)"
                    ),
                )

            version = get_installed_version(tooling)

            if version is None:
                # Tooling exists but the version probe failed — still installed.
                return LemonadeInfo(
                    installed=True,
                    path=tooling.client_path,
                    error=(
                        f"Failed to get version from {tooling.client_path} "
                        f"({tooling.kind} tooling)"
                    ),
                )

            return LemonadeInfo(
                installed=True, version=version, path=tooling.client_path
            )

        except Exception as e:
            return LemonadeInfo(installed=False, error=str(e))

    def needs_install(self, info: LemonadeInfo) -> bool:
        """
        Check if installation or update is needed.

        Args:
            info: Current installation info

        Returns:
            True if install/update is needed
        """
        if not info.installed:
            return True

        if not info.version:
            return True

        # Compare versions
        current = info.version_tuple
        target = self._parse_version(self.target_version)

        if not current or not target:
            return True

        # Need install if current version is older
        return current < target

    def _parse_version(self, version: str) -> Optional[tuple]:
        """Parse version string into tuple. See :func:`gaia.version.parse_version`."""
        return parse_version(version)

    @property
    def release_page_url(self) -> str:
        """Upstream release page — the authoritative list of published assets."""
        return f"{GITHUB_RELEASE_TAG_BASE}/v{self.target_version}"

    def _deb_arch(self) -> str:
        """Map platform.machine() to the Debian arch used in upstream asset names."""
        arch = DEB_ARCH_BY_MACHINE.get(self.machine)
        if arch is None:
            raise RuntimeError(
                f"Architecture '{self.machine}' has no Lemonade Linux package. "
                f"Upstream v{self.target_version} publishes amd64 (x86_64) and "
                f"arm64 (aarch64) .deb assets only — see {self.release_page_url}."
            )
        return arch

    def get_download_url(self) -> str:
        """
        Get the download URL for the current platform and architecture.

        Returns:
            Download URL for the installer

        Raises:
            RuntimeError: If the platform or architecture is not supported
        """
        version = self.target_version

        if self.system == "windows":
            if self.minimal:
                # Minimal installer for lightweight setup
                return f"{GITHUB_RELEASE_BASE}/v{version}/lemonade-server-minimal.msi"
            else:
                # Full installer
                return f"{GITHUB_RELEASE_BASE}/v{version}/lemonade.msi"
        elif self.system == "linux":
            # Linux DEB - no minimal variant yet.
            # Note: v10.0.0+ changed naming from lemonade_ to lemonade-server_,
            # and the asset carries a distro infix (see LINUX_DEB_DISTRO_TAG).
            return (
                f"{GITHUB_RELEASE_BASE}/v{version}/lemonade-server_"
                f"{version}-{LINUX_DEB_DISTRO_TAG}_{self._deb_arch()}.deb"
            )
        elif self.system == "darwin":
            # The .pkg ships arm64-only binaries and installs cleanly on Intel,
            # so an unguarded install would only fail later at "Bad CPU type".
            if self.machine not in ("arm64", "aarch64"):
                raise RuntimeError(
                    f"Architecture '{self.machine}' has no Lemonade macOS package. "
                    f"Upstream v{self.target_version} publishes an Apple-Silicon-only "
                    f".pkg — see {self.release_page_url}.\n"
                    "On an Apple Silicon Mac this means Python is running under "
                    "Rosetta; re-run GAIA with a native arm64 Python."
                )
            return f"{GITHUB_RELEASE_BASE}/v{version}/Lemonade-{version}-Darwin.pkg"
        else:
            raise RuntimeError(
                f"Platform '{self.system}' is not supported. "
                "GAIA init only supports Windows, Linux, and macOS."
            )

    def get_installer_filename(self) -> str:
        """Get the installer filename for the current platform.

        Derived from the download URL so the two can never drift apart.
        """
        return self.get_download_url().rsplit("/", 1)[-1]

    def _missing_asset_error(self, url: str, detail: str) -> str:
        """Error for an asset upstream no longer serves — a rename or a pulled release."""
        return (
            f"Lemonade v{self.target_version} installer asset {detail}.\n"
            f"  URL:      {url}\n"
            f"  Platform: {self.system}/{self.machine}\n"
            "Upstream may have renamed or dropped this asset. Check the published "
            f"asset list at {self.release_page_url}. If it moved, report it at "
            "https://github.com/amd/gaia/issues — maintainers: update "
            "LEMONADE_VERSION (src/gaia/version.py) or the asset-name pattern in "
            "src/gaia/installer/lemonade_installer.py."
        )

    def _unreachable_asset_error(self, url: str, detail: str) -> str:
        """Error for a network/proxy failure — says nothing about the asset itself."""
        return (
            f"Could not reach the Lemonade download server ({detail}).\n"
            f"  URL: {url}\n"
            "Check your network connection or proxy settings and retry."
        )

    def verify_download_url(self, url: Optional[str] = None, timeout: int = 30) -> str:
        """Confirm the constructed asset URL resolves before attempting a download.

        Args:
            url: URL to check (defaults to the URL for this platform/arch)
            timeout: Seconds to wait for the HEAD request

        Returns:
            The verified URL

        Raises:
            LemonadeAssetError: If the URL does not return HTTP 200. ``definitive``
                is True only when upstream positively reports the asset gone
                (404/410); transient network or proxy failures set it False.
        """
        url = url or self.get_download_url()
        request = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": "GAIA-Installer/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", None) or response.getcode()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise LemonadeAssetError(
                    self._missing_asset_error(url, f"was not found (HTTP {e.code})"),
                    definitive=True,
                ) from e
            # A proxy or WAF can reject HEAD while serving the GET fine.
            raise LemonadeAssetError(
                self._unreachable_asset_error(url, f"HTTP {e.code} {e.reason}"),
                definitive=False,
            ) from e
        except urllib.error.URLError as e:
            raise LemonadeAssetError(
                self._unreachable_asset_error(url, str(e.reason)), definitive=False
            ) from e
        except TimeoutError as e:
            raise LemonadeAssetError(
                self._unreachable_asset_error(url, f"timed out after {timeout}s"),
                definitive=False,
            ) from e
        except ConnectionError as e:
            # http.client.RemoteDisconnected is raised directly by
            # HTTPConnection.getresponse() and is NOT wrapped in URLError - a
            # transient drop, not proof the asset is gone.
            raise LemonadeAssetError(
                self._unreachable_asset_error(url, str(e)), definitive=False
            ) from e

        if status != 200:
            raise LemonadeAssetError(
                self._unreachable_asset_error(url, f"HTTP {status}"), definitive=False
            )
        return url

    def download_installer(self, dest_dir: Optional[str] = None) -> Path:
        """
        Download the Lemonade installer to a file no other download shares.

        The file is checked against the length the server sent and the pinned
        SHA-256 in :data:`INSTALLER_SHA256` before it is returned, and removed
        if either check fails. The caller deletes it once it is done with it.

        Args:
            dest_dir: Destination directory (uses temp dir if not specified)

        Returns:
            Path to downloaded installer

        Raises:
            UnpinnedInstallerError: The version has no pinned checksum.
            InstallerIntegrityError: The download is truncated or fails its
                checksum.
            RuntimeError: The download fails.
            OSError: The file cannot be written, e.g. the disk is full.
        """
        url = self.get_download_url()
        release_key = url[len(GITHUB_RELEASE_BASE) + 1 :]
        expected_sha256 = INSTALLER_SHA256.get(release_key) or LEGACY_MSI_SHA256.get(
            release_key
        )
        if expected_sha256 is None:
            raise UnpinnedInstallerError(
                f"No pinned SHA-256 for the Lemonade installer {release_key}. "
                "GAIA only runs an installer it can verify; the pins in "
                "INSTALLER_SHA256 (src/gaia/installer/lemonade_installer.py) "
                f"cover Lemonade {LEMONADE_VERSION}. Add the digest from "
                f"{self.release_page_url} to use v{self.target_version}."
            )
        # Pre-flight the asset so an upstream rename fails here — with the URL and
        # where to find the real asset list — instead of mid-download.
        try:
            self.verify_download_url(url)
        except LemonadeAssetError as e:
            if e.definitive:
                raise
            log.warning("Could not pre-flight %s; downloading anyway: %s", url, e)
        filename = Path(self.get_installer_filename())

        # A name of its own: two installs at once never write one file, and a
        # file a previous run left locked is never in the way.
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{filename.stem}-",
            suffix=filename.suffix,
            dir=dest_dir or tempfile.gettempdir(),
        )
        os.close(fd)
        dest_path = Path(tmp_name)

        self._print_status(f"Downloading from {url}")

        # Deleted on every way out but success, Ctrl-C included.
        completed = False
        try:
            self._fetch_verified(url, dest_path, expected_sha256)
            completed = True
        finally:
            if not completed:
                discard_download(dest_path)
        self._print_status(f"Downloaded to {dest_path}")
        return dest_path

    def _fetch_verified(self, url: str, dest_path: Path, expected_sha256: str) -> None:
        """Stream *url* into *dest_path*, then check its length and SHA-256."""
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "GAIA-Installer/1.0"}
            )
            with urllib.request.urlopen(request, timeout=300) as response:
                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                chunk_size = 8192
                digest = hashlib.sha256()

                try:
                    f = open(dest_path, "wb")
                except OSError as e:
                    raise _SaveFailed(e) from e
                try:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        try:
                            f.write(chunk)
                        except OSError as e:
                            raise _SaveFailed(e) from e
                        digest.update(chunk)
                        downloaded += len(chunk)

                        if self.progress_callback:
                            self.progress_callback(downloaded, total_size)
                finally:
                    try:
                        f.close()  # Flushes, so a full disk can surface here too.
                    except OSError as e:
                        raise _SaveFailed(e) from e
        except _SaveFailed as e:
            # A full disk is not a network failure; let the caller name the folder.
            raise e.error from None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise LemonadeAssetError(
                    self._missing_asset_error(url, "was not found (HTTP 404)"),
                    definitive=True,
                ) from e
            raise RuntimeError(f"Download failed: HTTP {e.code} - {e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Download failed: {e.reason}") from e
        except (ConnectionError, TimeoutError) as e:
            # http.client.RemoteDisconnected escapes URLError - see do_open().
            raise RuntimeError(
                f"Download of {url} dropped mid-transfer: {e}. This is usually a "
                f"transient network failure - retry the install."
            ) from e
        except Exception as e:
            raise RuntimeError(f"Download failed: {e}") from e

        if total_size and downloaded != total_size:
            raise InstallerIntegrityError(
                f"Download of {url} ended after {downloaded} of {total_size} "
                "bytes. The connection dropped mid-transfer; retry the install."
            )
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise InstallerIntegrityError(
                f"The Lemonade installer from {url} failed its SHA-256 check: "
                f"expected {expected_sha256}, got {actual}. The download is "
                "corrupt or was altered in transit (a proxy rewriting it?). "
                f"Retry, or download it by hand from {self.release_page_url}."
            )

    def install(
        self, installer_path: Optional[Path] = None, silent: bool = True
    ) -> InstallResult:
        """Install Lemonade Server. On Linux, installer_path is unused (PPA flow)."""
        self._print_status(f"Installing Lemonade Server (system={self.system})")
        try:
            if self.system == "windows":
                if installer_path is None or not installer_path.exists():
                    return InstallResult(
                        success=False, error=f"Installer not found: {installer_path}"
                    )
                return self._install_windows(installer_path, silent)
            elif self.system == "linux":
                return self._install_via_ppa(non_interactive=silent)
            elif self.system == "darwin":
                if installer_path is None or not installer_path.exists():
                    return InstallResult(
                        success=False, error=f"Installer not found: {installer_path}"
                    )
                return self._install_macos(installer_path, non_interactive=silent)
            else:
                return InstallResult(
                    success=False, error=f"Platform '{self.system}' is not supported"
                )
        except Exception as e:
            return InstallResult(success=False, error=str(e))

    def wait_for_msi_mutex(self, timeout: int = 30) -> bool:
        """
        Wait for any running MSI installations to complete.

        Args:
            timeout: Maximum seconds to wait

        Returns:
            True if no MSI operations are running, False if timed out
        """
        if self.system != "windows":
            return True

        waited = 0
        while waited < timeout:
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq msiexec.exe", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if "msiexec.exe" not in result.stdout:
                    return True
                self._print_status(
                    f"Waiting for existing MSI operation to finish... ({waited}s)"
                )
                time.sleep(2)
                waited += 2
            except (OSError, subprocess.SubprocessError) as e:
                # msiexec reports a real conflict itself (exit 1618), so warn and go on.
                log.warning("Could not check for running msiexec processes: %s", e)
                self._announce(
                    f"Could not check for other Windows Installer operations ({e}). "
                    "Continuing; if the install fails with error 1618, wait for "
                    "the other installation to finish and re-run `gaia init`."
                )
                return True
        return False

    @staticmethod
    def _is_valid_product_code(value: str) -> bool:
        """Validate that a string looks like an MSI ProductCode GUID."""
        return bool(
            re.match(
                r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
                r"-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$",
                value,
            )
        )

    def find_product_code(self) -> Optional[str]:
        """
        Find the MSI ProductCode for Lemonade Server from the Windows registry.

        This is more reliable than downloading an MSI for uninstall, because
        msiexec /x {ProductCode} works regardless of which MSI variant
        (full vs minimal) was used for installation.

        Returns:
            ProductCode GUID string (e.g. "{XXXXXXXX-...}"), or None if not found
        """
        if self.system != "windows":
            return None
        try:
            import winreg

            uninstall_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
            for root in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    with winreg.OpenKey(root, uninstall_key) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            try:
                                subkey_name = winreg.EnumKey(key, i)
                                with winreg.OpenKey(key, subkey_name) as subkey:
                                    try:
                                        name, _ = winreg.QueryValueEx(
                                            subkey, "DisplayName"
                                        )
                                        if "lemonade server" in name.lower():
                                            if not self._is_valid_product_code(
                                                subkey_name
                                            ):
                                                log.debug(
                                                    f"Skipping non-GUID subkey: {subkey_name}"
                                                )
                                                continue
                                            log.debug(
                                                f"Found Lemonade product: '{name}' "
                                                f"with code {subkey_name}"
                                            )
                                            return subkey_name
                                    except (FileNotFoundError, OSError):
                                        continue
                            except (FileNotFoundError, OSError):
                                continue
                except (FileNotFoundError, OSError):
                    continue
        except Exception as e:
            log.debug(f"Failed to find product code: {e}")
        return None

    def _install_windows(self, installer_path: Path, silent: bool) -> InstallResult:
        """Install on Windows using msiexec."""
        try:
            # Wait for any running MSI operations before starting
            if not self.wait_for_msi_mutex(timeout=30):
                return InstallResult(
                    success=False,
                    error="Another MSI installation is in progress. "
                    "Please wait for it to finish or close Windows Installer.",
                )

            cmd = ["msiexec", "/i", str(installer_path)]

            if silent:
                cmd.extend(["/qn", "/norestart"])

            log_dir = Path.home() / ".cache" / "gaia" / "installer"
            log_dir.mkdir(parents=True, exist_ok=True)
            msi_log = log_dir / "msi_install.log"
            cmd.extend(["/l*v", str(msi_log)])  # Verbose logging to file

            log.debug(f"Running: {' '.join(cmd)}")

            if silent:
                self._print_status(
                    "Running silent MSI installer (should complete in ~10 seconds)..."
                )
            else:
                self._print_status("Running MSI installer...")

            # MSI should install in 10-15 seconds, timeout after 60 seconds (indicates stuck process)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,  # 60 second timeout (should complete in ~10s)
                check=False,
            )

            if result.returncode == 0:
                return InstallResult(
                    success=True,
                    version=self.target_version,
                    message=f"Installed Lemonade v{self.target_version}",
                )
            elif result.returncode in _MSI_SUCCESS_REBOOT_REQUIRED:
                return InstallResult(
                    success=True,
                    version=self.target_version,
                    message=(
                        f"Installed Lemonade v{self.target_version}. "
                        "Restart Windows to finish the installation."
                    ),
                    restart_required=True,
                )
            elif result.returncode == 1602:
                return InstallResult(
                    success=False, error="Installation was cancelled by user"
                )
            elif result.returncode == 1603:
                return InstallResult(
                    success=False,
                    error=f"Installation failed (error 1603). See the MSI log: {msi_log}",
                )
            elif result.returncode == 1618:
                return InstallResult(
                    success=False,
                    error="Another installation is in progress (error 1618). "
                    "Wait a moment and try again.",
                )
            else:
                return InstallResult(
                    success=False,
                    error=(
                        f"msiexec failed with code {result.returncode}: "
                        f"{result.stderr} See the MSI log: {msi_log}"
                    ),
                )

        except subprocess.TimeoutExpired:
            # Print MSI log to help diagnose the hang
            error_msg = "Installation timed out (expected ~10s, hung for 60s)"
            try:
                if msi_log.exists():
                    self._print_status(f"MSI log file: {msi_log}")
                    log_content = msi_log.read_text(encoding="utf-16", errors="ignore")
                    # Print last 100 lines of log
                    log_lines = log_content.split("\n")
                    relevant_lines = log_lines[-100:]
                    log.error("=== MSI Install Log (last 100 lines) ===")
                    for line in relevant_lines:
                        log.error(line)
                    log.error("=== End MSI Install Log ===")

                    # Also print to console
                    if self.console:
                        self.console.print(
                            "\n   [red]MSI Install Log (last 50 lines):[/red]"
                        )
                        for line in relevant_lines[-50:]:
                            if line.strip():
                                self.console.print(f"   [dim]{line}[/dim]")
            except Exception as e:
                log.debug(f"Could not read MSI log: {e}")

            return InstallResult(success=False, error=error_msg)
        except FileNotFoundError:
            return InstallResult(success=False, error="msiexec not found")
        except Exception as e:
            return InstallResult(success=False, error=str(e))

    def _install_macos(
        self, installer_path: Path, non_interactive: bool = False
    ) -> InstallResult:
        """Install on macOS with `installer -pkg <path> -target /`.

        The .pkg writes to /Applications, /usr/local/bin and /Library/Launch*,
        so it needs admin rights. The sudo requirement is announced before sudo
        is ever invoked — never a bare, unexplained password prompt.
        """
        manual_cmd = f"sudo installer -pkg {shlex.quote(str(installer_path))} -target /"
        is_root = hasattr(os, "geteuid") and os.geteuid() == 0
        sudo_prefix = [] if is_root else ["sudo"]

        try:
            if not is_root:
                cached = subprocess.run(
                    ["sudo", "-n", "true"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    stdin=subprocess.DEVNULL,
                    check=False,
                )
                if cached.returncode != 0:
                    if non_interactive:
                        return InstallResult(
                            success=False,
                            error=(
                                "Installing Lemonade on macOS requires administrator "
                                "rights, but this run is non-interactive and sudo "
                                "needs a password.\n"
                                "Run 'sudo -v' first and retry, or install manually:\n"
                                f"  {manual_cmd}"
                            ),
                        )
                    # Must reach the user even with no Rich console (gaia install
                    # --lemonade constructs the installer without one).
                    self._announce(
                        "Administrator rights are required to install Lemonade — "
                        "sudo will ask for your password."
                    )

            cmd = sudo_prefix + [
                "installer",
                "-pkg",
                str(installer_path),
                "-target",
                "/",
            ]
            self._print_status(f"Running: {' '.join(cmd)}")
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600, check=False
            )

            if result.returncode != 0:
                return InstallResult(
                    success=False,
                    error=(
                        f"installer failed with code {result.returncode}:\n"
                        f"{(result.stdout + result.stderr).strip()}\n"
                        f"Retry manually: {manual_cmd}"
                    ),
                )

            # `installer` can exit 0 while MDM or a blocked payload leaves nothing
            # behind, so trust the probe rather than the exit code.
            verify = self.check_installation()
            if not verify.installed:
                return InstallResult(
                    success=False,
                    error=(
                        "installer reported success but Lemonade was not found "
                        "afterwards (looked for lemond/lemonade in /usr/local/bin "
                        "and /Applications/lemonade-app.app).\n"
                        f"Probe error: {verify.error}\n"
                        f"Try installing manually: {manual_cmd}"
                    ),
                )

            installed_version = verify.version or self.target_version
            return InstallResult(
                success=True,
                version=installed_version,
                message=f"Installed Lemonade v{installed_version}",
            )

        except subprocess.TimeoutExpired:
            return InstallResult(
                success=False,
                error=(
                    "macOS installer timed out after 600s. "
                    f"Retry manually: {manual_cmd}"
                ),
            )
        except KeyboardInterrupt:
            # Ctrl-C at the sudo prompt is BaseException — without this it escapes
            # every caller's `except Exception` as a raw traceback.
            return InstallResult(
                success=False,
                error=(
                    "Installation cancelled at the administrator prompt. "
                    f"To install without sudo prompting, run 'sudo -v' first, "
                    f"or install manually: {manual_cmd}"
                ),
            )
        except FileNotFoundError as e:
            return InstallResult(
                success=False, error=f"Required command not found: {e}"
            )

    @staticmethod
    def _uninstall_macos() -> InstallResult:
        """macOS: the upstream .pkg ships no uninstaller, so say so and hand over steps.

        Paths and pkgutil identifiers come from the v11.8.1 .pkg BOMs. Upstream
        renamed the launchd labels and receipts com.lemonade.* -> ai.lemonadeserver.*
        after 11.5.0, so these track the pin.
        """
        return InstallResult(
            success=False,
            error=(
                "Automatic uninstall is not supported on macOS — the Lemonade .pkg "
                "ships no uninstaller. Remove it manually:\n"
                "  sudo launchctl bootout system "
                "/Library/LaunchDaemons/ai.lemonadeserver.server.plist\n"
                "  sudo rm -f /Library/LaunchDaemons/ai.lemonadeserver.server.plist "
                "/Library/LaunchAgents/ai.lemonadeserver.tray.plist\n"
                "  sudo rm -rf /Applications/lemonade-app.app\n"
                "  sudo rm -f /usr/local/bin/lemonade /usr/local/bin/lemond "
                "/usr/local/bin/lemonade-tray\n"
                "  pkgutil --pkgs | grep '^ai.lemonadeserver.server' | "
                "xargs -n1 sudo pkgutil --forget"
            ),
        )

    @staticmethod
    def _check_linux_version() -> Optional[str]:
        """
        Check if the Linux version meets minimum requirements.

        Lemonade Server .deb requires Ubuntu 24.04+ or Debian 13+ due to
        dependencies like libasound2t64 that don't exist on older releases.

        Returns:
            None if compatible, or an error message string if not.
        """
        try:
            # Parse /etc/os-release (systemd standard, present on all modern distros)
            with open("/etc/os-release", encoding="utf-8") as f:
                os_info = dict(line.strip().split("=", 1) for line in f if "=" in line)
            # Strip quotes from values
            os_info = {k: v.strip('"') for k, v in os_info.items()}

            distro = os_info.get("ID", "").lower()
            version = os_info.get("VERSION_ID", "")
            pretty_name = os_info.get("PRETTY_NAME", "Unknown Linux")

            # Check Ubuntu 24.04+
            if distro == "ubuntu" and version:
                if int(version.split(".")[0]) < 24:
                    return f"Requires Ubuntu 24.04+. Detected: {pretty_name}"

            # Check Debian 13+
            elif distro == "debian" and version:
                if int(version) < 13:
                    return f"Requires Debian 13+. Detected: {pretty_name}"

        except Exception as e:
            log.debug(f"Could not check Linux version: {e}")

        return None

    def _install_via_ppa(self, non_interactive: bool = False) -> InstallResult:
        """Install Lemonade Server on Linux via the Launchpad PPA.

        Linux >= v10.0.1 is distributed only through ppa:lemonade-team/stable.
        Requires Ubuntu 24.04+ (Debian and older Ubuntu are gated by
        _check_linux_version).
        """
        try:
            version_error = self._check_linux_version()
            if version_error:
                return InstallResult(success=False, error=version_error)

            # add-apt-repository ships pre-installed on Ubuntu 24.04+; if missing
            # the user is on a minimal image and needs a clear error, not silent apt churn.
            if shutil.which("add-apt-repository") is None:
                return InstallResult(
                    success=False,
                    error=(
                        "add-apt-repository not found (required for PPA install).\n"
                        "Install it first: sudo apt-get install software-properties-common\n"
                        "Then re-run: gaia init"
                    ),
                )

            is_root = hasattr(os, "geteuid") and os.geteuid() == 0
            sudo_prefix = [] if is_root else ["sudo"]

            # Pre-flight sudo when non-interactive: surface a clean error instead
            # of hanging at a hidden TTY password prompt.
            if not is_root and non_interactive:
                sudo_check = subprocess.run(
                    ["sudo", "-n", "true"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    stdin=subprocess.DEVNULL,
                    check=False,
                )
                if sudo_check.returncode != 0:
                    return InstallResult(
                        success=False,
                        error=(
                            "sudo requires a password but gaia init --yes is non-interactive.\n"
                            "Either run 'sudo -v' first to cache credentials, "
                            "configure passwordless sudo, or run gaia init as root."
                        ),
                    )
            elif not is_root:
                self._announce(
                    "Administrator rights are required to add the Lemonade PPA — "
                    "sudo will ask for your password."
                )

            env = os.environ.copy()
            if non_interactive:
                env["DEBIAN_FRONTEND"] = "noninteractive"

            def _run(step: str, cmd: list, timeout: int) -> subprocess.CompletedProcess:
                self._print_status(f"{step}: {' '.join(cmd)}")
                return subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    check=False,
                )

            result = _run(
                "Add PPA",
                sudo_prefix + ["add-apt-repository", "-y", LEMONADE_PPA],
                timeout=120,
            )
            if result.returncode != 0:
                outage = diagnose_launchpad_outage(result.stdout + result.stderr)
                if outage:
                    return InstallResult(success=False, error=outage)
                return InstallResult(
                    success=False,
                    error=(
                        f"Failed to add Lemonade PPA (system={self.system}, root={is_root}).\n"
                        f"add-apt-repository output:\n{(result.stdout + result.stderr).strip()}\n"
                        "See https://amd-gaia.ai/docs/reference/troubleshooting"
                    ),
                )

            update_result = _run(
                "Update apt cache", sudo_prefix + ["apt-get", "update"], timeout=300
            )
            # apt-get update exits 0 when only *some* indexes fail, so a Launchpad
            # outage is silent here and resurfaces as "Unable to locate package".
            outage = diagnose_launchpad_outage(
                update_result.stdout + update_result.stderr
            )
            if outage:
                return InstallResult(success=False, error=outage)
            if update_result.returncode != 0:
                # An unrelated third-party repo failing is not ours to block on;
                # the Lemonade index specifically resolved or we returned above.
                log.warning(
                    "apt-get update failed (rc=%s); continuing with possibly stale cache: %s",
                    update_result.returncode,
                    (update_result.stdout + update_result.stderr).strip()[-500:],
                )

            result = _run(
                "Install lemonade-server",
                sudo_prefix + ["apt-get", "install", "-y", "lemonade-server"],
                timeout=600,
            )
            if result.returncode != 0:
                return InstallResult(
                    success=False,
                    error=(
                        f"apt-get install lemonade-server failed (step=Install, root={is_root}):\n"
                        f"{(result.stdout + result.stderr).strip()}\n"
                        "Your apt state may be partial — run 'sudo dpkg --configure -a' to recover.\n"
                        "See https://amd-gaia.ai/docs/reference/troubleshooting"
                    ),
                )

            # Use the real installed version — PPA may ship a different version than target.
            verify = self.check_installation()
            installed_version = verify.version or "unknown"
            if verify.installed and verify.version != self.target_version:
                log.warning(
                    "PPA installed Lemonade %s but GAIA expected %s",
                    verify.version,
                    self.target_version,
                )

            return InstallResult(
                success=True,
                version=installed_version,
                message=f"Installed Lemonade v{installed_version} via PPA",
            )

        except subprocess.TimeoutExpired as e:
            return InstallResult(
                success=False,
                error=(
                    f"PPA install timed out during step (cmd={e.cmd}). "
                    "If this happened during apt-get install, run 'sudo dpkg --configure -a'."
                ),
            )
        except FileNotFoundError as e:
            return InstallResult(
                success=False, error=f"Required command not found: {e}"
            )

    def is_platform_supported(self) -> bool:
        """Check if the current platform is supported for installation."""
        return self.system in ("windows", "linux", "darwin")

    def get_platform_name(self) -> str:
        """Get a friendly name for the current platform."""
        names = {
            "windows": "Windows",
            "linux": "Linux",
            "darwin": "macOS",
        }
        return names.get(self.system, self.system.capitalize())

    def uninstall(self, silent: bool = True) -> InstallResult:
        """
        Uninstall Lemonade Server.

        Args:
            silent: Whether to run silent uninstallation (no UI)

        Returns:
            InstallResult with success status
        """
        self._print_status("Uninstalling Lemonade Server...")

        try:
            if self.system == "windows":
                return self._uninstall_windows(silent)
            elif self.system == "linux":
                return self._uninstall_linux()
            elif self.system == "darwin":
                return self._uninstall_macos()
            else:
                return InstallResult(
                    success=False, error=f"Platform '{self.system}' is not supported"
                )
        except Exception as e:
            return InstallResult(success=False, error=str(e))

    def _uninstall_windows(self, silent: bool) -> InstallResult:
        """Uninstall on Windows using msiexec.

        Uses registry-based ProductCode lookup first (most reliable), then
        falls back to downloading the matching MSI for uninstall.
        """
        try:
            # Wait for any running MSI operations
            if not self.wait_for_msi_mutex(timeout=30):
                return InstallResult(
                    success=False,
                    error="Another MSI installation is in progress. "
                    "Please wait for it to finish or close Windows Installer.",
                )

            # Strategy 1: Use ProductCode from registry (works regardless of
            # which MSI variant was used for install - full or minimal)
            product_code = self.find_product_code()
            if product_code:
                self._print_status(f"Found product code: {product_code}")
                cmd = ["msiexec", "/x", product_code]
                if silent:
                    cmd.extend(["/qn", "/norestart"])

                log.debug(f"Running: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )

                if result.returncode == 0:
                    return InstallResult(
                        success=True,
                        message="Lemonade Server uninstalled successfully",
                    )
                elif result.returncode == 1618:
                    return InstallResult(
                        success=False,
                        error="Another installation is in progress (error 1618). "
                        "Wait a moment and try again.",
                    )
                # If ProductCode approach fails, fall through to MSI download
                log.debug(
                    f"ProductCode uninstall failed (code {result.returncode}), "
                    "trying MSI download fallback"
                )

            # Strategy 2: Download matching MSI for uninstall (original approach)
            info = self.check_installation()
            if not info.installed or not info.version:
                return InstallResult(
                    success=False, error="Lemonade Server is not installed"
                )

            installed_version = info.version.lstrip("v")
            failed = f"Could not uninstall Lemonade Server v{installed_version}:"
            by_hand = _remove_by_hand(installed_version)
            rejected = []
            not_published = []
            not_downloaded = []

            # Try both minimal and full MSI variants
            for use_minimal in [True, False]:
                variant = "minimal" if use_minimal else "full"
                uninstall_installer = LemonadeInstaller(
                    target_version=installed_version,
                    minimal=use_minimal,
                    console=self.console,
                )
                url = uninstall_installer.get_download_url()
                self._print_status(
                    f"Downloading {variant} MSI v{installed_version} for uninstall..."
                )
                try:
                    msi_path = uninstall_installer.download_installer()
                except (UnpinnedInstallerError, InstallerIntegrityError) as e:
                    return InstallResult(
                        success=False,
                        error=f"{failed} GAIA did not run the {variant} MSI because "
                        f"it could not verify it. {e} {by_hand}",
                    )
                except LemonadeAssetError as e:
                    log.warning("Upstream does not publish %s: %s", url, e)
                    not_published.append(url)
                    continue
                except RuntimeError as e:
                    log.warning("Could not download %s: %s", url, e)
                    not_downloaded.append(f"{url}: {e}")
                    continue
                except OSError as e:
                    return InstallResult(
                        success=False,
                        error=f"{failed} GAIA could not save the {variant} MSI in "
                        f"{tempfile.gettempdir()}: {e}. Free disk space or fix that "
                        f"folder's permissions and retry. {by_hand}",
                    )

                cmd = ["msiexec", "/x", str(msi_path)]
                if silent:
                    cmd.extend(["/qn", "/norestart"])

                log.debug(f"Running: {' '.join(cmd)}")
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=300,
                        check=False,
                    )
                finally:
                    discard_download(msi_path)

                if result.returncode == 0:
                    return InstallResult(
                        success=True,
                        message="Lemonade Server uninstalled successfully",
                    )
                elif result.returncode == 1605:
                    # Wrong MSI variant — try the other one
                    log.debug(
                        f"{variant} MSI didn't match installed product, "
                        "trying other variant"
                    )
                    rejected.append(variant)
                    continue
                elif result.returncode == 1618:
                    return InstallResult(
                        success=False,
                        error="Another installation is in progress (error 1618). "
                        "Wait a moment and try again.",
                    )
                else:
                    return InstallResult(
                        success=False,
                        error=f"msiexec failed with code {result.returncode}: {result.stderr}",
                    )

            reasons = []
            if rejected:
                which = "either" if len(rejected) == 2 else f"the {rejected[0]}"
                reasons.append(f"Windows Installer did not accept {which} MSI.")
            if not_published:
                reasons.append(
                    f"The release no longer publishes {' or '.join(not_published)}."
                )
            if not_downloaded:
                reasons.append(
                    "GAIA could not download the MSI needed to uninstall it "
                    f"({'; '.join(not_downloaded)}). Check your network "
                    "connection or proxy settings and retry."
                )
            return InstallResult(
                success=False, error=f"{failed} {' '.join(reasons)} {by_hand}"
            )

        except subprocess.TimeoutExpired as e:
            # Not retried with the other MSI: this one may still be running.
            return InstallResult(
                success=False,
                error=f"Uninstall timed out: msiexec did not finish within "
                f"{e.timeout:.0f} seconds. Windows Installer may still be "
                "finishing, so wait a minute before retrying (retrying sooner "
                f"fails with error 1618). {_remove_by_hand(None)}",
            )
        except FileNotFoundError:
            return InstallResult(success=False, error="msiexec not found")
        except Exception as e:
            return InstallResult(success=False, error=str(e))

    def _uninstall_linux(self) -> InstallResult:
        """Uninstall on Linux using apt."""
        try:
            # Check if we have root access
            is_root = False
            if hasattr(os, "geteuid"):
                is_root = os.geteuid() == 0

            sudo_prefix = [] if is_root else ["sudo"]
            cmd = sudo_prefix + ["apt", "remove", "-y", "lemonade-server"]

            log.debug(f"Running: {' '.join(cmd)}")

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300, check=False
            )

            if result.returncode == 0:
                return InstallResult(
                    success=True,
                    message="Lemonade Server uninstalled successfully",
                )
            else:
                return InstallResult(
                    success=False,
                    error=f"apt remove failed: {result.stderr}",
                )

        except subprocess.TimeoutExpired:
            return InstallResult(success=False, error="Uninstall timed out")
        except FileNotFoundError as e:
            return InstallResult(
                success=False, error=f"Required command not found: {e}"
            )
        except Exception as e:
            return InstallResult(success=False, error=str(e))
