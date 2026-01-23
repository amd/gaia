# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Lemonade Server Installer

Handles detection, download, and installation of Lemonade Server
from GitHub releases for Windows and Linux platforms.
"""

import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from gaia.version import LEMONADE_VERSION

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


@dataclass
class LemonadeInfo:
    """Information about Lemonade Server installation."""

    installed: bool
    version: Optional[str] = None
    path: Optional[str] = None
    error: Optional[str] = None

    @property
    def version_tuple(self) -> Optional[tuple]:
        """Parse version string into tuple for comparison."""
        if not self.version:
            return None
        try:
            # Handle versions like "9.1.4" or "v9.1.4"
            ver = self.version.lstrip("v")
            parts = ver.split(".")
            return tuple(int(p) for p in parts[:3])
        except (ValueError, IndexError):
            return None


@dataclass
class InstallResult:
    """Result of an installation attempt."""

    success: bool
    version: Optional[str] = None
    message: str = ""
    error: Optional[str] = None


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
        self.console = console

    def _print_status(self, message: str, style: str = "dim"):
        """Print a status message to console or log."""
        if self.console and RICH_AVAILABLE:
            self.console.print(f"   [{style}]{message}[/{style}]")
        elif not self.console:
            # Only log if no console provided (to avoid duplicate output)
            log.debug(message)

    def refresh_path_from_registry(self) -> None:
        """Refresh PATH from Windows registry after MSI install."""
        if self.system != "windows":
            return
        try:
            import winreg

            user_path = ""
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                    user_path, _ = winreg.QueryValueEx(key, "Path")
            except (FileNotFoundError, OSError):
                pass

            system_path = ""
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ) as key:
                    system_path, _ = winreg.QueryValueEx(key, "Path")
            except (FileNotFoundError, OSError):
                pass

            if user_path or system_path:
                new_path = (
                    f"{user_path};{system_path}"
                    if user_path and system_path
                    else (user_path or system_path)
                )
                os.environ["PATH"] = new_path
                log.debug("Refreshed PATH from registry")
        except Exception as e:
            log.debug(f"Failed to refresh PATH: {e}")

    def check_installation(self) -> LemonadeInfo:
        """
        Check if Lemonade Server is installed and get version info.

        Returns:
            LemonadeInfo with installation status
        """
        try:
            # Refresh PATH from registry (in case MSI just updated it)
            self.refresh_path_from_registry()

            # Try to find lemonade-server executable
            lemonade_path = shutil.which("lemonade-server")

            if not lemonade_path:
                return LemonadeInfo(
                    installed=False, error="lemonade-server not found in PATH"
                )

            # Get version
            result = subprocess.run(
                ["lemonade-server", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            if result.returncode != 0:
                return LemonadeInfo(
                    installed=True,
                    path=lemonade_path,
                    error=f"Failed to get version: {result.stderr}",
                )

            # Parse version from output
            # Expected format: "lemonade-server 9.1.4" or just "9.1.4"
            version_output = result.stdout.strip()
            version_match = re.search(r"(\d+\.\d+\.\d+)", version_output)

            if version_match:
                version = version_match.group(1)
            else:
                version = version_output

            return LemonadeInfo(installed=True, version=version, path=lemonade_path)

        except FileNotFoundError:
            return LemonadeInfo(
                installed=False, error="lemonade-server not found in PATH"
            )
        except subprocess.TimeoutExpired:
            return LemonadeInfo(
                installed=False, error="Timeout checking lemonade-server version"
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
        """Parse version string into tuple."""
        try:
            ver = version.lstrip("v")
            parts = ver.split(".")
            return tuple(int(p) for p in parts[:3])
        except (ValueError, IndexError):
            return None

    def get_download_url(self) -> str:
        """
        Get the download URL for the current platform.

        Returns:
            Download URL for the installer

        Raises:
            RuntimeError: If platform is not supported
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
            # Linux DEB - filename includes version (no minimal variant yet)
            return f"{GITHUB_RELEASE_BASE}/v{version}/lemonade_{version}_amd64.deb"
        else:
            raise RuntimeError(
                f"Platform '{self.system}' is not supported. "
                "GAIA init only supports Windows and Linux."
            )

    def get_installer_filename(self) -> str:
        """Get the installer filename for the current platform."""
        if self.system == "windows":
            if self.minimal:
                return "lemonade-server-minimal.msi"
            else:
                return "lemonade.msi"
        elif self.system == "linux":
            return f"lemonade_{self.target_version}_amd64.deb"
        else:
            raise RuntimeError(f"Platform '{self.system}' is not supported.")

    def download_installer(self, dest_dir: Optional[str] = None) -> Path:
        """
        Download the Lemonade installer.

        Args:
            dest_dir: Destination directory (uses temp dir if not specified)

        Returns:
            Path to downloaded installer

        Raises:
            RuntimeError: If download fails
        """
        url = self.get_download_url()
        filename = self.get_installer_filename()

        if dest_dir:
            dest_path = Path(dest_dir) / filename
        else:
            dest_path = Path(tempfile.gettempdir()) / filename

        self._print_status(f"Downloading from {url}")

        try:
            # Remove existing file if it exists (may be locked from previous attempt)
            if dest_path.exists():
                try:
                    dest_path.unlink()
                    log.debug(f"Removed existing installer at {dest_path}")
                except PermissionError:
                    # File is locked, use a unique filename instead
                    unique_name = f"lemonade_{uuid.uuid4().hex[:8]}.msi"
                    dest_path = Path(tempfile.gettempdir()) / unique_name
                    log.debug(f"Using unique filename: {dest_path}")

            # Create request with User-Agent header
            request = urllib.request.Request(
                url, headers={"User-Agent": "GAIA-Installer/1.0"}
            )

            # Download with progress reporting
            with urllib.request.urlopen(request, timeout=300) as response:
                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                chunk_size = 8192

                with open(dest_path, "wb") as f:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)

                        if self.progress_callback:
                            self.progress_callback(downloaded, total_size)

            self._print_status(f"Downloaded to {dest_path}")
            return dest_path

        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError(
                    f"Lemonade v{self.target_version} not found. "
                    "Please check https://github.com/lemonade-sdk/lemonade/releases "
                    "for available versions."
                )
            raise RuntimeError(f"Download failed: HTTP {e.code} - {e.reason}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Download failed: {e.reason}")
        except Exception as e:
            raise RuntimeError(f"Download failed: {e}")

    def install(self, installer_path: Path, silent: bool = True) -> InstallResult:
        """
        Install Lemonade Server from the downloaded installer.

        Args:
            installer_path: Path to the installer file
            silent: Whether to run silent installation (no UI)

        Returns:
            InstallResult with success status

        Raises:
            RuntimeError: If installation fails
        """
        if not installer_path.exists():
            return InstallResult(
                success=False, error=f"Installer not found: {installer_path}"
            )

        self._print_status(f"Installing from {installer_path}")

        try:
            if self.system == "windows":
                return self._install_windows(installer_path, silent)
            elif self.system == "linux":
                return self._install_linux(installer_path)
            else:
                return InstallResult(
                    success=False, error=f"Platform '{self.system}' is not supported"
                )
        except Exception as e:
            return InstallResult(success=False, error=str(e))

    def _install_windows(self, installer_path: Path, silent: bool) -> InstallResult:
        """Install on Windows using msiexec."""
        try:
            cmd = ["msiexec", "/i", str(installer_path)]

            if silent:
                cmd.extend(["/qn", "/norestart"])

            log.debug(f"Running: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                check=False,
            )

            if result.returncode == 0:
                return InstallResult(
                    success=True,
                    version=self.target_version,
                    message=f"Installed Lemonade v{self.target_version}",
                )
            elif result.returncode == 1602:
                return InstallResult(
                    success=False, error="Installation was cancelled by user"
                )
            elif result.returncode == 1603:
                return InstallResult(
                    success=False,
                    error="Installation failed. Check Windows Event Log for details.",
                )
            else:
                return InstallResult(
                    success=False,
                    error=f"msiexec failed with code {result.returncode}: {result.stderr}",
                )

        except subprocess.TimeoutExpired:
            return InstallResult(success=False, error="Installation timed out")
        except FileNotFoundError:
            return InstallResult(success=False, error="msiexec not found")
        except Exception as e:
            return InstallResult(success=False, error=str(e))

    def _install_linux(self, installer_path: Path) -> InstallResult:
        """Install on Linux using dpkg."""
        try:
            # Check if we have root access (geteuid only available on Unix)
            is_root = False
            if hasattr(os, "geteuid"):
                is_root = os.geteuid() == 0

            if not is_root:
                # Try with sudo
                cmd = ["sudo", "dpkg", "-i", str(installer_path)]
            else:
                cmd = ["dpkg", "-i", str(installer_path)]

            log.debug(f"Running: {' '.join(cmd)}")

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300, check=False
            )

            if result.returncode == 0:
                return InstallResult(
                    success=True,
                    version=self.target_version,
                    message=f"Installed Lemonade v{self.target_version}",
                )
            else:
                # dpkg might fail due to missing dependencies
                # Try to fix with apt
                if "dependency" in result.stderr.lower():
                    fix_cmd = ["sudo", "apt-get", "install", "-f", "-y"]
                    fix_result = subprocess.run(
                        fix_cmd,
                        capture_output=True,
                        text=True,
                        timeout=300,
                        check=False,
                    )
                    if fix_result.returncode == 0:
                        return InstallResult(
                            success=True,
                            version=self.target_version,
                            message=f"Installed Lemonade v{self.target_version} (fixed dependencies)",
                        )

                return InstallResult(
                    success=False,
                    error=f"dpkg failed: {result.stderr}",
                )

        except subprocess.TimeoutExpired:
            return InstallResult(success=False, error="Installation timed out")
        except FileNotFoundError as e:
            return InstallResult(
                success=False, error=f"Required command not found: {e}"
            )
        except Exception as e:
            return InstallResult(success=False, error=str(e))

    def is_platform_supported(self) -> bool:
        """Check if the current platform is supported for installation."""
        return self.system in ("windows", "linux")

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
            else:
                return InstallResult(
                    success=False, error=f"Platform '{self.system}' is not supported"
                )
        except Exception as e:
            return InstallResult(success=False, error=str(e))

    def _uninstall_windows(self, silent: bool) -> InstallResult:
        """Uninstall on Windows using msiexec."""
        try:
            # Get currently installed version - we need matching MSI to uninstall
            info = self.check_installation()
            if not info.installed or not info.version:
                return InstallResult(
                    success=False, error="Lemonade Server is not installed"
                )

            installed_version = info.version.lstrip("v")

            # Create installer for the installed version (not target version)
            # Pass console to child installer for consistent output
            uninstall_installer = LemonadeInstaller(
                target_version=installed_version, console=self.console
            )

            # Download the MSI matching the installed version
            self._print_status(f"Downloading MSI v{installed_version} for uninstall...")
            msi_path = uninstall_installer.download_installer()

            cmd = ["msiexec", "/x", str(msi_path)]

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
            elif result.returncode == 1605:
                return InstallResult(
                    success=False, error="Lemonade Server is not installed"
                )
            else:
                return InstallResult(
                    success=False,
                    error=f"msiexec failed with code {result.returncode}: {result.stderr}",
                )

        except subprocess.TimeoutExpired:
            return InstallResult(success=False, error="Uninstall timed out")
        except FileNotFoundError:
            return InstallResult(success=False, error="msiexec not found")
        except Exception as e:
            return InstallResult(success=False, error=str(e))

    def _uninstall_linux(self) -> InstallResult:
        """Uninstall on Linux using dpkg."""
        try:
            # Check if we have root access
            is_root = False
            if hasattr(os, "geteuid"):
                is_root = os.geteuid() == 0

            if not is_root:
                cmd = ["sudo", "dpkg", "-r", "lemonade"]
            else:
                cmd = ["dpkg", "-r", "lemonade"]

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
                    error=f"dpkg failed: {result.stderr}",
                )

        except subprocess.TimeoutExpired:
            return InstallResult(success=False, error="Uninstall timed out")
        except FileNotFoundError as e:
            return InstallResult(
                success=False, error=f"Required command not found: {e}"
            )
        except Exception as e:
            return InstallResult(success=False, error=str(e))
