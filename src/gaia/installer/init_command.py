# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA Init Command

Main entry point for `gaia init` command that:
1. Checks if Lemonade Server is installed and version matches
2. Downloads and installs Lemonade from GitHub releases if needed
3. Starts Lemonade server
4. Downloads required models for the selected profile
5. Verifies setup is working
"""

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

# Rich imports for better CLI formatting
try:
    from rich.console import Console
    from rich.panel import Panel

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from gaia.agents.base.console import AgentConsole
from gaia.installer.lemonade_installer import LemonadeInfo, LemonadeInstaller
from gaia.version import LEMONADE_VERSION

log = logging.getLogger(__name__)

# Profile definitions mapping to agent profiles
# Note: These define which agent profile to use for each init profile
INIT_PROFILES = {
    "minimal": {
        "description": "Fast setup with lightweight model",
        "agent": "minimal",
        "models": ["Qwen3-0.6B-GGUF"],
        "approx_size": "~400 MB",
        "min_lemonade_version": "9.0.4",
        "min_context_size": 4096,
    },
    "sd": {
        "description": "Image generation with multi-modal AI (LLM + SD + VLM)",
        "agent": "sd",
        "models": [
            "SDXL-Turbo",  # Image generation (6.5GB)
            "Qwen3-8B-GGUF",  # Agentic reasoning + prompt enhancement (5.0GB)
            "Qwen3-VL-4B-Instruct-GGUF",  # Vision analysis + stories (3.2GB)
        ],
        "approx_size": "~15 GB",
        "min_lemonade_version": "9.2.0",  # SDXL-Turbo requires v9.2.0+
        "min_context_size": 16384,  # SD agent needs 16K for multi-step planning
    },
    "chat": {
        "description": "Interactive chat with RAG and vision support",
        "agent": "chat",
        "models": None,  # Use agent profile defaults
        "approx_size": "~25 GB",
        "min_lemonade_version": "9.0.4",
        "min_context_size": 32768,
    },
    "code": {
        "description": "Autonomous coding assistant",
        "agent": "code",
        "models": None,
        "approx_size": "~18 GB",
        "min_lemonade_version": "9.0.4",
        "min_context_size": 32768,
    },
    "rag": {
        "description": "Document Q&A with retrieval",
        "agent": "rag",
        "models": None,
        "approx_size": "~25 GB",
        "min_lemonade_version": "9.0.4",
        "min_context_size": 32768,
    },
    "vlm": {
        "description": "Vision pipeline for document and image extraction",
        "agent": "vlm",
        "models": ["Qwen3-VL-4B-Instruct-GGUF"],
        "approx_size": "~3 GB",
        "min_lemonade_version": "9.0.4",
        "min_context_size": 8192,
    },
    "all": {
        "description": "All models for all agents",
        "agent": "all",
        "models": None,
        "approx_size": "~26 GB",
        "min_lemonade_version": "9.2.0",  # Includes SD, so needs v9.2.0+
        "min_context_size": 32768,  # Max requirement across all agents
    },
}


@dataclass
class InitProgress:
    """Progress information for the init command."""

    step: int
    total_steps: int
    step_name: str
    message: str


class InitCommand:
    """
    Main handler for the `gaia init` command.

    Orchestrates the full initialization workflow:
    1. Check/install Lemonade Server
    2. Start server if needed
    3. Download models for profile
    4. Verify setup
    """

    def __init__(
        self,
        profile: str = "chat",
        skip_models: bool = False,
        skip_lemonade: bool = False,
        force_reinstall: bool = False,
        force_models: bool = False,
        yes: bool = False,
        verbose: bool = False,
        remote: bool = False,
        progress_callback: Optional[Callable[[InitProgress], None]] = None,
    ):
        """
        Initialize the init command.

        Args:
            profile: Profile to initialize (minimal, chat, code, rag, all)
            skip_models: Skip model downloads
            skip_lemonade: Skip Lemonade installation check (for CI)
            force_reinstall: Force reinstall even if compatible version exists
            force_models: Force re-download models even if already available
            yes: Skip confirmation prompts
            verbose: Enable verbose output
            remote: Lemonade is on a remote machine (skip local start, still check version)
            progress_callback: Optional callback for progress updates
        """
        self.profile = profile.lower()
        self.skip_models = skip_models
        self.skip_lemonade = skip_lemonade
        self.force_reinstall = force_reinstall
        self.force_models = force_models
        self.yes = yes
        self.verbose = verbose
        self.remote = remote
        self.progress_callback = progress_callback

        # Auto-detect remote mode from LEMONADE_BASE_URL environment variable
        self._lemonade_base_url = os.environ.get("LEMONADE_BASE_URL")
        if self._lemonade_base_url is not None and not self.remote:
            from urllib.parse import urlparse

            parsed = urlparse(self._lemonade_base_url)
            hostname = parsed.hostname or "localhost"
            if hostname not in ("localhost", "127.0.0.1", "::1"):
                self.remote = True
                log.info(
                    f"Auto-detected remote mode from LEMONADE_BASE_URL={self._lemonade_base_url}"
                )

        # Validate profile
        if self.profile not in INIT_PROFILES:
            valid = ", ".join(INIT_PROFILES.keys())
            raise ValueError(f"Invalid profile '{profile}'. Valid profiles: {valid}")

        # Initialize Rich console if available (before installer for console pass-through)
        self.console = Console() if RICH_AVAILABLE else None

        # Initialize AgentConsole for formatted output
        self.agent_console = AgentConsole()

        # Use minimal installer for minimal profile OR when using --yes (silent mode)
        # Minimal installer is faster and more reliable for CI
        use_minimal = self.profile == "minimal" or yes

        self.installer = LemonadeInstaller(
            target_version=LEMONADE_VERSION,
            progress_callback=self._download_progress if verbose else None,
            minimal=use_minimal,
            console=self.console,
        )

        # Context verification state (set during model loading)
        self._ctx_verified = None
        self._ctx_warning = None

    def _print(self, message: str, end: str = "\n"):
        """Print message to stdout."""
        if RICH_AVAILABLE and self.console:
            if end == "":
                self.console.print(message, end="")
            else:
                self.console.print(message)
        else:
            print(message, end=end, flush=True)

    def _print_header(self):
        """Print initialization header."""
        if RICH_AVAILABLE and self.console:
            self.console.print()
            self.console.print(
                Panel(
                    "[bold cyan]GAIA Initialization[/bold cyan]",
                    border_style="cyan",
                    padding=(0, 2),
                )
            )
            self.console.print()
        else:
            self._print("")
            self._print("=" * 60)
            self._print("  GAIA Initialization")
            self._print("=" * 60)
            self._print("")

    def _print_step(self, step: int, total: int, message: str):
        """Print step header."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"[bold blue]Step {step}/{total}:[/bold blue] {message}")
        else:
            self._print(f"Step {step}/{total}: {message}")

    def _print_success(self, message: str):
        """Print success message."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"   [green]✓[/green] {message}")
        else:
            self._print(f"   ✓ {message}")

    def _print_warning(self, message: str):
        """Print warning message."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"   [yellow]⚠️  {message}[/yellow]")
        else:
            self._print(f"   ⚠️  {message}")

    def _print_error(self, message: str):
        """Print error message."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"   [red]❌ {message}[/red]")
        else:
            self._print(f"   ❌ {message}")

    def _prompt_yes_no(self, prompt: str, default: bool = True) -> bool:
        """
        Prompt user for yes/no confirmation.

        Args:
            prompt: Question to ask
            default: Default answer if user presses enter

        Returns:
            True for yes, False for no
        """
        if self.yes:
            return True

        if default:
            suffix = "[bold green]Y[/bold green]/n" if RICH_AVAILABLE else "[Y/n]"
        else:
            suffix = "y/[bold green]N[/bold green]" if RICH_AVAILABLE else "[y/N]"

        try:
            if RICH_AVAILABLE and self.console:
                self.console.print(f"   {prompt} [{suffix}]: ", end="")
                response = input().strip().lower()
            else:
                response = input(f"   {prompt} {suffix}: ").strip().lower()

            if not response:
                return default
            return response in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            self._print("")
            return False

    def _refresh_path_environment(self):
        """
        Refresh PATH environment variable from Windows registry.

        This allows the current Python process to find executables
        that were just installed by MSI, without requiring a terminal restart.
        """
        if sys.platform != "win32":
            # On Linux, standard paths (/usr/bin, /usr/local/bin) are already in PATH
            return

        try:
            import winreg

            # Read user PATH from registry
            user_path = ""
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                    user_path, _ = winreg.QueryValueEx(key, "Path")
            except (FileNotFoundError, OSError):
                pass

            # Read system PATH from registry
            system_path = ""
            try:
                with winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ) as key:
                    system_path, _ = winreg.QueryValueEx(key, "Path")
            except (FileNotFoundError, OSError):
                pass

            # Merge registry paths with current PATH (don't replace entirely)
            if user_path or system_path:
                current_path = os.environ.get("PATH", "")
                registry_path = (
                    f"{user_path};{system_path}"
                    if user_path and system_path
                    else (user_path or system_path)
                )
                # Expand environment variables like %SystemRoot%, %USERPROFILE%, etc.
                registry_path = os.path.expandvars(registry_path)
                # Prepend registry paths to preserve current session paths
                os.environ["PATH"] = f"{registry_path};{current_path}"
                log.debug("Merged and expanded registry PATH with current environment")

        except Exception as e:
            log.debug(f"Failed to refresh PATH: {e}")

    def _download_progress(self, downloaded: int, total: int):
        """Callback for download progress."""
        if total > 0:
            percent = (downloaded / total) * 100
            bar_width = 20
            filled = int(bar_width * downloaded / total)
            bar = "=" * filled + "-" * (bar_width - filled)
            size_str = f"{downloaded / 1024 / 1024:.1f} MB"
            if total > 0:
                size_str += f"/{total / 1024 / 1024:.1f} MB"
            self._print(f"\r   [{bar}] {percent:.0f}% ({size_str})", end="")

    def run(self) -> int:
        """
        Execute the initialization workflow.

        Returns:
            Exit code (0 for success, non-zero for failure)
        """
        self._print_header()

        total_steps = 4 if not self.skip_models else 3

        try:
            # Step 1: Check/Install Lemonade (skip for remote servers or CI)
            if self.remote:
                self._print_step(1, total_steps, "Checking remote Lemonade Server...")
                if self._lemonade_base_url:
                    self._print_success(
                        f"Using remote Lemonade Server at {self._lemonade_base_url}"
                    )
                else:
                    self._print_success("Using remote Lemonade Server")
            elif self.skip_lemonade:
                self._print_step(
                    1, total_steps, "Skipping Lemonade installation check..."
                )
                # Still show version info for transparency
                info = self.installer.check_installation()
                if info.installed and info.version:
                    self._print_success(
                        f"Using pre-installed Lemonade Server v{info.version}"
                    )
                else:
                    self._print_success("Using pre-installed Lemonade Server")
            else:
                self._print_step(
                    1, total_steps, "Checking Lemonade Server installation..."
                )
                if not self._ensure_lemonade_installed():
                    return 1

            # Step 2: Check server
            step_num = 2
            self._print("")
            self._print_step(step_num, total_steps, "Checking Lemonade Server...")
            if not self._ensure_server_running():
                return 1

            # Step 3: Download models (unless skipped)
            if not self.skip_models:
                step_num = 3
                self._print("")
                self._print_step(
                    step_num,
                    total_steps,
                    f"Downloading models for '{self.profile}' profile...",
                )
                if not self._download_models():
                    return 1

            # Step 4: Verify setup
            step_num = total_steps
            self._print("")
            self._print_step(step_num, total_steps, "Verifying setup...")
            if not self._verify_setup():
                return 1

            # Success!
            self._print_completion()
            return 0

        except KeyboardInterrupt:
            self._print("")
            self._print("Initialization cancelled by user.")
            return 130
        except Exception as e:
            self._print_error(f"Unexpected error: {e}")
            if self.verbose:
                import traceback

                traceback.print_exc()
            return 1

    def _ensure_lemonade_installed(self) -> bool:
        """
        Check Lemonade installation and install if needed.

        Returns:
            True if Lemonade is ready, False on failure
        """
        # Check platform support
        if not self.installer.is_platform_supported():
            platform_name = self.installer.get_platform_name()
            self._print_error(
                f"Platform '{platform_name}' is not supported for automatic installation."
            )
            self._print("   GAIA init only supports Windows and Linux.")
            self._print(
                "   Please install Lemonade Server manually from: https://www.lemonade-server.ai"
            )
            return False

        info = self.installer.check_installation()

        if info.installed and info.version:
            self._print_success(f"Lemonade Server found: v{info.version}")
            # Show the path where it was found (only in verbose mode)
            if self.verbose and info.path:
                self.console.print(f"   [dim]Path: {info.path}[/dim]")

            # Check version match
            if not self._check_version_compatibility(info):
                return False

            if self.force_reinstall:
                self._print("   Force reinstall requested.")
                return self._install_lemonade()

            self._print_success("Version is compatible")
            return True

        elif info.installed:
            self._print_warning("Lemonade Server found but version unknown")
            if info.error:
                self._print(f"   Error: {info.error}")

            if not self._prompt_yes_no(
                f"Install/update Lemonade v{LEMONADE_VERSION}?", default=True
            ):
                self._print("   Skipping update. Will verify server connectivity.")
                # Continue to next step - server health check will verify connectivity
                return True

            return self._install_lemonade()

        else:
            self._print("   Lemonade Server not found")
            self._print("")

            if not self._prompt_yes_no(
                f"Install Lemonade v{LEMONADE_VERSION}?", default=True
            ):
                self._print("")
                self._print("   Skipping local installation.")
                self._print(
                    "   To install manually, visit: https://www.lemonade-server.ai"
                )
                self._print(
                    "   Or set LEMONADE_BASE_URL environment variable for a remote server."
                )
                # Continue to next step - server health check will verify connectivity
                return True

            return self._install_lemonade()

    @staticmethod
    def _parse_version(version: str) -> Optional[tuple]:
        """Parse version string into tuple."""
        try:
            ver = version.lstrip("v")
            parts = ver.split(".")
            return tuple(int(p) for p in parts[:3])
        except (ValueError, IndexError):
            return None

    def _check_version_compatibility(self, info: LemonadeInfo) -> bool:
        """
        Check if installed version is compatible and upgrade if needed.

        Args:
            info: Lemonade installation info

        Returns:
            True if compatible or upgrade successful, False otherwise
        """
        current = info.version_tuple
        target = self._parse_version(LEMONADE_VERSION)

        if not current or not target:
            return True

        # Check for version mismatch
        if current != target:
            current_ver = info.version
            target_ver = LEMONADE_VERSION

            self._print("")
            self._print_warning("Version mismatch detected!")
            if RICH_AVAILABLE and self.console:
                self.console.print(
                    f"      [dim]Installed:[/dim] [red]v{current_ver}[/red]"
                )
                self.console.print(
                    f"      [dim]Expected:[/dim]  [green]v{target_ver}[/green]"
                )
            else:
                self._print(f"      Installed: v{current_ver}")
                self._print(f"      Expected:  v{target_ver}")
            self._print("")

            if current < target:
                if RICH_AVAILABLE and self.console:
                    self.console.print(
                        "   [dim]Your version is older than expected.[/dim]"
                    )
                    self.console.print(
                        "   [dim]Some features may not work correctly.[/dim]"
                    )
                else:
                    self._print("   Your version is older than expected.")
                    self._print("   Some features may not work correctly.")
            else:
                if RICH_AVAILABLE and self.console:
                    self.console.print(
                        "   [dim]Your version is newer than expected.[/dim]"
                    )
                    self.console.print(
                        "   [dim]This may cause compatibility issues.[/dim]"
                    )
                else:
                    self._print("   Your version is newer than expected.")
                    self._print("   This may cause compatibility issues.")
            self._print("")

            # Check if upgrade is required based on profile's minimum version
            profile_config = INIT_PROFILES[self.profile]
            min_version_required = profile_config.get("min_lemonade_version", "9.0.0")
            from packaging import version as pkg_version

            needs_upgrade = pkg_version.parse(current_ver) < pkg_version.parse(
                min_version_required
            )

            # In CI mode (--yes), auto-upgrade if needed for this profile
            if self.yes and not self.force_reinstall:
                if needs_upgrade:
                    self._print("")
                    if RICH_AVAILABLE and self.console:
                        self.console.print(
                            f"   [yellow]⚠️  Profile '{self.profile}' requires Lemonade v{min_version_required}+[/yellow]"
                        )
                        self.console.print(
                            f"   [bold cyan]Upgrading:[/bold cyan] v{current_ver} → v{target_ver}"
                        )
                    else:
                        self._print_warning(
                            f"Profile '{self.profile}' requires Lemonade v{min_version_required}+"
                        )
                        self._print(
                            f"   Upgrading from v{current_ver} to v{target_ver}..."
                        )
                    return self._upgrade_lemonade(current_ver)
                else:
                    self._print_success(
                        f"Version v{current_ver} is sufficient for profile '{self.profile}'"
                    )
                    return True

            # Prompt user to upgrade
            if not self._prompt_yes_no(
                f"Upgrade to v{target_ver}? (will uninstall current version)",
                default=False,  # Default to no for safety
            ):
                self._print_warning("Continuing with current version")
                return True

            return self._upgrade_lemonade(current_ver)

        return True

    def _upgrade_lemonade(self, old_version: str) -> bool:
        """
        Uninstall old version and install the target version.

        Args:
            old_version: The currently installed version string

        Returns:
            True on success, False on failure
        """
        self._print("")
        if RICH_AVAILABLE and self.console:
            self.console.print(
                f"   [bold]Uninstalling[/bold] Lemonade [red]v{old_version}[/red]..."
            )
        else:
            self._print(f"   Uninstalling Lemonade v{old_version}...")

        # Uninstall old version
        try:
            result = self.installer.uninstall(silent=True)
            if result.success:
                self._print_success("Uninstalled old version")
            else:
                self._print_error(f"Failed to uninstall: {result.error}")
                self._print_warning("Attempting to install new version anyway...")
        except Exception as e:
            self._print_error(f"Uninstall error: {e}")
            self._print_warning("Attempting to install new version anyway...")

        # Install new version
        return self._install_lemonade()

    def _install_lemonade(self) -> bool:
        """
        Download and install Lemonade Server.

        Returns:
            True on success, False on failure
        """
        self._print("")
        if RICH_AVAILABLE and self.console:
            self.console.print(
                f"   [bold]Downloading[/bold] Lemonade [cyan]v{LEMONADE_VERSION}[/cyan]..."
            )
        else:
            self._print(f"   Downloading Lemonade v{LEMONADE_VERSION}...")

        try:
            # Download installer
            installer_path = self.installer.download_installer()
            self._print("")
            self._print_success("Download complete")

            # Install (silent in CI with --yes, interactive otherwise for desktop icon)
            self.console.print("   [bold]Installing...[/bold]")
            if not self.yes:
                self.console.print()
                self.console.print(
                    "   [yellow]⚠️  The installer window will appear - please complete the installation[/yellow]"
                )
                self.console.print()
            result = self.installer.install(installer_path, silent=self.yes)

            if result.success:
                self._print_success(f"Installed Lemonade v{result.version}")

                # Refresh PATH so current session can find lemonade-server
                if self.verbose:
                    self.console.print("   [dim]Refreshing PATH environment...[/dim]")
                self._refresh_path_environment()

                # Verify installation by checking version
                if self.verbose:
                    self.console.print("   [dim]Verifying installation...[/dim]")
                verify_info = self.installer.check_installation()

                if verify_info.installed and verify_info.version:
                    self._print_success(
                        f"Verified: lemonade-server v{verify_info.version}"
                    )
                    if self.verbose and verify_info.path:
                        self.console.print(f"   [dim]Path: {verify_info.path}[/dim]")

                return True
            else:
                self._print_error(f"Installation failed: {result.error}")

                if "Administrator" in str(result.error) or "sudo" in str(result.error):
                    self._print("")
                    if RICH_AVAILABLE and self.console:
                        self.console.print(
                            "   [yellow]Try running as Administrator (Windows) or with sudo (Linux)[/yellow]"
                        )
                    else:
                        self._print(
                            "   Try running as Administrator (Windows) or with sudo (Linux)"
                        )

                return False

        except Exception as e:
            self._print_error(f"Failed to install: {e}")
            return False

    def _find_lemonade_server(self) -> Optional[str]:
        """
        Find the lemonade-server executable.

        Uses the installer's PATH refresh to pick up recent MSI changes.
        Falls back to common installation paths if not found in PATH.

        Returns:
            Path to lemonade-server executable, or None if not found
        """
        import shutil

        # Use installer's PATH refresh (reads from Windows registry)
        self.installer.refresh_path_from_registry()

        # Try to find in updated PATH
        lemonade_path = shutil.which("lemonade-server")
        if lemonade_path:
            return lemonade_path

        # Fallback: check common installation paths (Windows)
        if sys.platform == "win32":
            common_paths = [
                # Per-user install (most common for MSI)
                os.path.expandvars(
                    r"%LOCALAPPDATA%\Programs\Lemonade Server\lemonade-server.exe"
                ),
                os.path.expandvars(
                    r"%LOCALAPPDATA%\Lemonade Server\lemonade-server.exe"
                ),
                # System-wide install
                r"C:\Program Files\Lemonade Server\lemonade-server.exe",
                r"C:\Program Files (x86)\Lemonade Server\lemonade-server.exe",
                # Potential alternative paths
                os.path.expandvars(
                    r"%USERPROFILE%\lemonade-server\lemonade-server.exe"
                ),
            ]

            for path in common_paths:
                if os.path.isfile(path):
                    if self.verbose:
                        log.debug(f"Found lemonade-server at fallback path: {path}")
                    return path

        # Fallback: check common installation paths (Linux)
        elif sys.platform.startswith("linux"):
            common_paths = [
                "/snap/bin/lemonade-server",
                "/usr/local/bin/lemonade-server",
                "/usr/bin/lemonade-server",
                os.path.expanduser("~/.local/bin/lemonade-server"),
            ]

            for path in common_paths:
                if os.path.isfile(path):
                    if self.verbose:
                        log.debug(f"Found lemonade-server at fallback path: {path}")
                    return path

        return None

    def _ensure_server_running(self) -> bool:
        """
        Ensure Lemonade server is running with health check verification.

        In remote mode, only checks if server is reachable - does not prompt
        user to start it (assumes it's managed externally).

        Returns:
            True if server is running and healthy, False on failure
        """
        try:
            # Import here to avoid circular imports
            from gaia.llm.lemonade_client import LemonadeClient

            client = LemonadeClient(verbose=self.verbose)

            # Check if already running using health_check
            try:
                health = client.health_check()
                if health:
                    self._print_success("Server is already running")
                    # Verify health status
                    if isinstance(health, dict):
                        status = health.get("status", "unknown")
                        if status == "ok":
                            self._print_success("Server health: OK")
                        else:
                            self._print_warning(f"Server status: {status}")
                    return True
            except Exception as e:
                # Log the health check error for debugging
                log.debug(f"Health check failed: {e}")
                # Server not running

            # In remote mode, don't prompt to start - just report error
            if self.remote:
                self._print_error("Remote Lemonade Server is not reachable")
                self.console.print()
                self.console.print(
                    "   [dim]Ensure the remote Lemonade Server is running and accessible.[/dim]"
                )
                self.console.print(
                    "   [dim]Check LEMONADE_BASE_URL environment variable if using a custom URL.[/dim]"
                )
                return False

            # Server not running - start it automatically in CI mode, or prompt user
            if self.yes:
                # In CI mode, just inform and auto-start (not an error)
                self._print("   Lemonade Server is not running")
                self.console.print()
                self.console.print(
                    "   [dim]Auto-starting Lemonade Server (CI mode)...[/dim]"
                )

                try:
                    # Find lemonade-server executable
                    # Check env var first (set by install-lemonade action in CI)
                    lemonade_path = os.environ.get("LEMONADE_SERVER_PATH")
                    if not lemonade_path:
                        # Use our enhanced finder (checks PATH + fallback locations)
                        lemonade_path = self._find_lemonade_server()

                    if not lemonade_path:
                        raise FileNotFoundError("lemonade-server not found in PATH")

                    # Start server in background
                    if sys.platform == "win32":
                        # Windows: use subprocess.Popen with no window
                        subprocess.Popen(
                            [lemonade_path, "serve", "--no-tray"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=(
                                subprocess.CREATE_NO_WINDOW
                                if hasattr(subprocess, "CREATE_NO_WINDOW")
                                else 0
                            ),
                        )
                    else:
                        # Linux/Mac: background process
                        subprocess.Popen(
                            [lemonade_path, "serve"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )

                    # Wait for server to start
                    import time

                    max_wait = 30
                    waited = 0
                    while waited < max_wait:
                        time.sleep(2)
                        waited += 2
                        try:
                            health = client.health_check()
                            if (
                                health
                                and isinstance(health, dict)
                                and health.get("status") == "ok"
                            ):
                                self._print_success(
                                    f"Server started and ready (waited {waited}s)"
                                )
                                return True
                        except Exception:
                            pass

                    self._print_error(f"Server failed to start after {max_wait}s")
                    return False

                except Exception as e:
                    self._print_error(f"Failed to start server: {e}")
                    return False
            else:
                # Interactive mode - prompt user to start manually
                self._print_error("Lemonade Server is not running")
                self.console.print()
            self.console.print("   [bold]Please start Lemonade Server:[/bold]")
            if sys.platform == "win32":
                self.console.print(
                    "   [dim]• Double-click the Lemonade icon in your system tray, or[/dim]"
                )
                self.console.print(
                    "   [dim]• Search for 'Lemonade' in Start Menu and launch it[/dim]"
                )
            else:
                # Find the actual binary path to give the user a working command
                lemonade_path = self._find_lemonade_server()
                if lemonade_path:
                    self.console.print(
                        f"   [dim]• Run:[/dim] [cyan]{lemonade_path} serve &[/cyan]"
                    )
                else:
                    self.console.print(
                        "   [dim]• Run:[/dim] [cyan]lemonade-server serve &[/cyan]"
                    )
                self.console.print(
                    "   [dim]• If command not found, open a new terminal or run:[/dim] [cyan]hash -r[/cyan]"
                )
            self.console.print()

            # Wait for user to start the server
            try:
                self.console.print(
                    "   [bold]Press Enter when server is started...[/bold]", end=""
                )
                input()
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                self._print_error("Initialization cancelled")
                return False

            self.console.print()

            # Check if server is now running
            try:
                health = client.health_check()
                if health and isinstance(health, dict) and health.get("status") == "ok":
                    self._print_success("Server is now running")
                    self._print_success("Server health: OK")
                    return True
                else:
                    self._print_error("Server still not responding")
                    return False
            except Exception:
                self._print_error("Server still not responding")
                return False

        except ImportError as e:
            self._print_error(f"Lemonade SDK not installed: {e}")
            if RICH_AVAILABLE and self.console:
                self.console.print(
                    "   [dim]Run:[/dim] [cyan]pip install lemonade-sdk[/cyan]"
                )
            else:
                self._print("   Run: pip install lemonade-sdk")
            return False
        except Exception as e:
            self._print_error(f"Failed to check/start server: {e}")
            return False

    def _verify_model(self, client, model_id: str) -> tuple:
        """
        Verify a model is available (downloaded) on the server.

        Note: We only check if the model exists in the server's model list.
        Running inference to verify would require loading each model, which is
        slow and can cause server issues. If a model is corrupted, the error
        will surface when the user tries to use it.

        Args:
            client: LemonadeClient instance
            model_id: Model ID to verify

        Returns:
            Tuple of (success: bool, error_type: str or None)
        """
        try:
            # Check if model is in the available models list
            if client.check_model_available(model_id):
                return (True, None)
            return (False, "not_found")
        except Exception as e:
            log.debug(f"Model verification failed for {model_id}: {e}")
            return (False, "server_error")

    def _download_models(self) -> bool:
        """
        Download models for the selected profile.

        Delegates to LemonadeClient.ensure_model_downloaded() which handles
        checking availability, downloading via API, and waiting for completion.
        Works for both local and remote Lemonade servers.

        Returns:
            True if all models downloaded, False on failure
        """
        try:
            from gaia.llm.lemonade_client import LemonadeClient

            client = LemonadeClient(verbose=self.verbose)

            # Get profile config
            profile_config = INIT_PROFILES[self.profile]

            # Get models to download
            if profile_config["models"]:
                model_ids = list(profile_config["models"])
            else:
                model_ids = client.get_required_models(profile_config["agent"])

            # Include default CPU model for profiles that need gaia llm
            # SD profile has its own LLM (Qwen3-8B) and doesn't need the 0.5B model
            if self.profile != "sd":
                from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

                if DEFAULT_MODEL_NAME not in model_ids:
                    model_ids = list(model_ids) + [DEFAULT_MODEL_NAME]

            if not model_ids:
                self._print_success("No models required for this profile")
                return True

            # Show which models will be ensured
            if RICH_AVAILABLE and self.console:
                self.console.print(
                    f"   [bold]Ensuring {len(model_ids)} model(s) are downloaded:[/bold]"
                )
                for model_id in model_ids:
                    self.console.print(f"   [cyan]•[/cyan] {model_id}")
            else:
                self._print(f"   Ensuring {len(model_ids)} model(s) are downloaded:")
                for model_id in model_ids:
                    self._print(f"   • {model_id}")
            self._print("")

            if not self._prompt_yes_no("Continue?", default=True):
                self._print("   Skipping model downloads")
                return True

            # Force re-download: delete models first
            if self.force_models:
                for model_id in model_ids:
                    if client.check_model_available(model_id):
                        if RICH_AVAILABLE and self.console:
                            self.console.print(
                                f"   [dim]Deleting (force re-download)[/dim] [cyan]{model_id}[/cyan]..."
                            )
                        else:
                            self._print(
                                f"   Deleting (force re-download) {model_id}..."
                            )
                        try:
                            client.delete_model(model_id)
                            self._print_success(f"Deleted {model_id}")
                        except Exception as e:
                            self._print_error(f"Failed to delete {model_id}: {e}")

            # Download each model via LemonadeClient API
            success = True
            for model_id in model_ids:
                self._print("")
                self.agent_console.print(
                    f"   [bold cyan]Downloading:[/bold cyan] {model_id}"
                )
                if client.ensure_model_downloaded(model_id):
                    self._print_success(f"Downloaded {model_id}")
                else:
                    self._print_error(f"Failed to download {model_id}")
                    success = False

            return success

        except Exception as e:
            self._print_error(f"Error downloading models: {e}")
            return False

    def _test_model_inference(self, client, model_id: str) -> tuple:
        """
        Test a model with a small inference request.

        Args:
            client: LemonadeClient instance
            model_id: Model ID to test

        Returns:
            Tuple of (success: bool, error_message: str or None)
        """
        try:
            # Check if profile requires specific context size for this model
            profile_config = INIT_PROFILES.get(self.profile, {})
            min_ctx = profile_config.get("min_context_size")

            # Load the model (with context size if required)
            is_llm = not (
                "embed" in model_id.lower()
                or any(sd in model_id.upper() for sd in ["SDXL", "SD-", "SD1", "SD2"])
            )

            if is_llm and min_ctx:
                # Force unload if already loaded to ensure recipe_options are saved
                if client.check_model_loaded(model_id):
                    client.unload_model()

                # Load with explicit context size and save it
                client.load_model(
                    model_id,
                    auto_download=False,
                    prompt=False,
                    ctx_size=min_ctx,
                    save_options=True,
                )

                # Verify context size was set correctly by reading it back
                try:
                    # Get full model list with recipe_options
                    models_list = client.list_models()
                    model_info = next(
                        (
                            m
                            for m in models_list.get("data", [])
                            if m.get("id") == model_id
                        ),
                        None,
                    )

                    if not model_info:
                        return (False, "Model info not found")

                    actual_ctx = model_info.get("recipe_options", {}).get("ctx_size")

                    if actual_ctx and actual_ctx >= min_ctx:
                        # Success - context verified
                        # Store for success message, and flag if larger than expected
                        self._ctx_verified = actual_ctx
                        if actual_ctx > min_ctx:
                            self._ctx_warning = (
                                f"(configured: {actual_ctx}, required: {min_ctx})"
                            )
                    elif actual_ctx:
                        # Context was set but is too small
                        return (False, f"Context {actual_ctx} < {min_ctx} required")
                    else:
                        # Context not in recipe_options - should not happen after forced unload/reload
                        # Mark as unverified but don't fail the test
                        self._ctx_verified = None  # Explicitly mark as unverified
                except Exception as e:
                    return (False, f"Context check failed: {str(e)[:50]}")
            else:
                # Load without context size (SD models, embedding models, or no requirement)
                client.load_model(model_id, auto_download=False, prompt=False)

            # Check model type
            is_embedding_model = "embed" in model_id.lower()
            is_sd_model = any(
                sd in model_id.upper() for sd in ["SDXL", "SD-", "SD1", "SD2"]
            )

            if is_sd_model:
                # Test SD model with image generation
                response = client.generate_image(
                    prompt="test",
                    model=model_id,
                    steps=1,  # Minimal steps for quick test
                    size="512x512",
                )
                # Check if we got a valid image in b64_json format
                if (
                    response
                    and response.get("data")
                    and response["data"][0].get("b64_json")
                ):
                    return (True, None)
                return (False, "No image generated")
            elif is_embedding_model:
                # Test embedding model with a simple text
                response = client.embeddings(
                    input_texts=["test"],
                    model=model_id,
                )
                # Check if we got valid embeddings
                if response and response.get("data"):
                    embedding = response["data"][0].get("embedding", [])
                    if embedding and len(embedding) > 0:
                        return (True, None)
                    return (False, "Empty embedding")
                return (False, "Invalid response format")
            else:
                # Test LLM with a minimal chat request
                response = client.chat_completions(
                    model=model_id,
                    messages=[{"role": "user", "content": "Say 'ok'"}],
                    max_tokens=10,
                    temperature=0,
                )
                # Check if we got a valid response
                if response and response.get("choices"):
                    content = (
                        response["choices"][0].get("message", {}).get("content", "")
                    )
                    if content:
                        return (True, None)
                    return (False, "Empty response")
                return (False, "Invalid response format")

        except Exception as e:
            error_msg = str(e)
            # Truncate long error messages
            if len(error_msg) > 100:
                error_msg = error_msg[:100] + "..."
            return (False, error_msg)

    def _verify_setup(self) -> bool:
        """
        Verify the setup is working by testing each model with a small request.

        Returns:
            True if verification passes, False on failure
        """
        try:
            from gaia.llm.lemonade_client import LemonadeClient

            client = LemonadeClient(verbose=self.verbose)

            # Check server health
            try:
                health = client.health_check()
                if health:
                    self._print_success("Server health: OK")
                else:
                    self._print_error("Server not responding")
                    return False
            except Exception:
                self._print_error("Server not responding")
                return False

            # Ensure proper context size for this profile
            profile_config = INIT_PROFILES[self.profile]
            min_ctx = profile_config.get("min_context_size")
            if min_ctx:
                from gaia.llm.lemonade_manager import LemonadeManager

                self.console.print()
                self.console.print(
                    f"   [dim]Ensuring {min_ctx} token context for {self.profile} profile...[/dim]"
                )
                success = LemonadeManager.ensure_ready(
                    min_context_size=min_ctx, quiet=True
                )
                if success:
                    self._print_success(f"Context size verified: {min_ctx} tokens")
                else:
                    self._print_error(f"Failed to configure {min_ctx} token context")
                    self._print_error(
                        f"Try: lemonade-server serve --ctx-size {min_ctx}"
                    )
                    return False

            # Get models to verify
            profile_config = INIT_PROFILES[self.profile]
            if profile_config["models"]:
                model_ids = profile_config["models"]
            else:
                model_ids = client.get_required_models(profile_config["agent"])

            # Include default CPU model for profiles that need gaia llm
            # SD profile has its own LLM and doesn't need the 0.5B model
            if self.profile != "sd":
                from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

                if DEFAULT_MODEL_NAME not in model_ids:
                    model_ids = list(model_ids) + [DEFAULT_MODEL_NAME]

            if not model_ids or self.skip_models:
                return True

            # Prompt to run model verification (can be slow)
            self.console.print()
            self.console.print(
                "   [dim]Model verification loads each model and runs a small inference test.[/dim]"
            )
            self.console.print(
                "   [dim]This may take a few minutes but ensures models work correctly.[/dim]"
            )
            self.console.print()

            if not self._prompt_yes_no("Run model verification?", default=True):
                self._print_success("Skipping model verification")
                return True

            # Test each model with a small inference request
            self.console.print()
            self.console.print("   [bold]Testing models with inference:[/bold]")

            models_passed = 0
            models_failed = []
            interrupted = False

            try:
                for model_id in model_ids:
                    # Check if model is available first
                    if not client.check_model_available(model_id):
                        self.console.print(
                            f"   [yellow]⏭️[/yellow]  [cyan]{model_id}[/cyan] [dim]- not downloaded[/dim]"
                        )
                        continue

                    # Test the model
                    success, error = self._test_model_inference(client, model_id)
                    if success:
                        # Check if context was verified
                        ctx_msg = ""
                        if hasattr(self, "_ctx_verified"):
                            if self._ctx_verified:
                                # Context successfully verified
                                ctx_msg = f" [dim](ctx: {self._ctx_verified})[/dim]"

                                # Warn if context is larger than required
                                if self._ctx_warning:
                                    ctx_msg = f" [yellow]{self._ctx_warning}[/yellow]"
                                    self._ctx_warning = None
                            elif self._ctx_verified is None:
                                # Context could not be verified
                                ctx_msg = " [yellow]⚠️ Context unverified![/yellow]"

                            delattr(self, "_ctx_verified")  # Reset for next model

                        self.console.print(
                            f"   [green]✓[/green]  [cyan]{model_id}[/cyan] [dim]- OK[/dim]{ctx_msg}"
                        )
                        models_passed += 1
                    else:
                        self.console.print(
                            f"   [red]❌[/red] [cyan]{model_id}[/cyan] [dim]- {error}[/dim]"
                        )
                        models_failed.append((model_id, error))

            except KeyboardInterrupt:
                self.console.print()
                self._print_warning("Verification interrupted")
                interrupted = True

            # Summary
            total = len(model_ids)
            self.console.print()
            if interrupted:
                self._print_success(
                    f"Verified {models_passed} model(s) before interruption"
                )
            elif models_failed:
                self._print_warning(f"Models verified: {models_passed}/{total} passed")
                self.console.print()
                self.console.print(
                    "   [bold]Failed models may be corrupted. To fix:[/bold]"
                )
                self.console.print(
                    "   [dim]Option 1 - Delete all models and re-download:[/dim]"
                )
                self.console.print("     [cyan]gaia uninstall --models --yes[/cyan]")
                self.console.print(
                    f"     [cyan]gaia init --profile {self.profile} --yes[/cyan]"
                )
                self.console.print()
                self.console.print(
                    "   [dim]Option 2 - Manually delete failed models:[/dim]"
                )

                # Show path for each failed model
                hf_cache = os.path.expanduser("~/.cache/huggingface/hub")
                from pathlib import Path

                for model_id, error in models_failed:
                    # Find actual model directory (may have org prefix like ggml-org/model-name)
                    # Search for directories containing the model name
                    model_name_part = model_id.split("/")[-1]  # Get last part if has /
                    matching_dirs = list(
                        Path(hf_cache).glob(f"models--*{model_name_part}*")
                    )

                    if matching_dirs:
                        model_path = str(matching_dirs[0])
                        self.console.print(
                            f"     [cyan]{model_id}[/cyan]: [dim]{model_path}[/dim]"
                        )
                        if sys.platform == "win32":
                            self.console.print(
                                f'       [yellow]rmdir /s /q[/yellow] [cyan]"{model_path}"[/cyan]'
                            )
                        else:
                            self.console.print(
                                f'       [yellow]rm -rf[/yellow] [cyan]"{model_path}"[/cyan]'
                            )
                    else:
                        # Fallback if directory not found
                        self.console.print(
                            f"     [cyan]{model_id}[/cyan]: [dim]Not found in cache[/dim]"
                        )

                self.console.print()
                self.console.print(
                    f"     [dim]Then re-download:[/dim] [cyan]gaia init --profile {self.profile} --yes[/cyan]"
                )
            else:
                self._print_success(f"All {models_passed} model(s) verified")

            return True  # Don't fail init due to model issues

        except Exception as e:
            self._print_error(f"Verification failed: {e}")
            return False

    def _print_completion(self):
        """Print completion message with next steps."""
        if RICH_AVAILABLE and self.console:
            self.console.print()
            self.console.print(
                Panel(
                    "[bold green]GAIA initialization complete![/bold green]",
                    border_style="green",
                    padding=(0, 2),
                )
            )
            self.console.print()
            self.console.print("  [bold]Quick start commands:[/bold]")

            # Profile-specific quick start commands
            if self.profile == "sd":
                self.console.print(
                    '    [cyan]gaia sd "create a cute robot kitten and tell me a story"[/cyan]'
                )
                self.console.print('    [cyan]gaia sd "sunset over mountains"[/cyan]')
                self.console.print(
                    "    [cyan]gaia sd -i[/cyan]                                        Interactive mode"
                )
            elif self.profile == "chat":
                self.console.print(
                    "    [cyan]gaia chat[/cyan]              Start interactive chat with RAG"
                )
                self.console.print(
                    "    [cyan]gaia chat init[/cyan]         Setup document folder"
                )
            elif self.profile == "vlm":
                self.console.print(
                    "    [cyan]gaia cache status[/cyan]      Verify VLM model is available"
                )
                self.console.print(
                    "    [dim]Vision model ready! Use with the driver logs processor or VLM SDK:[/dim]"
                )
                self.console.print(
                    "    [cyan]from gaia.vlm import StructuredVLMExtractor[/cyan]"
                )
            elif self.profile == "minimal":
                self.console.print(
                    "    [cyan]gaia llm 'Hello'[/cyan]       Quick LLM query"
                )
                self.console.print(
                    "    [dim]Note: Minimal profile installed. For full features, run:[/dim]"
                )
                self.console.print("    [cyan]gaia init --profile chat[/cyan]")
            else:
                # Default commands for other profiles
                self.console.print(
                    "    [cyan]gaia chat[/cyan]              Start interactive chat"
                )
                self.console.print(
                    "    [cyan]gaia llm 'Hello'[/cyan]       Quick LLM query"
                )
                self.console.print(
                    "    [cyan]gaia talk[/cyan]              Voice interaction"
                )
            self.console.print()
        else:
            self._print("")
            self._print("=" * 60)
            self._print("  GAIA initialization complete!")
            self._print("=" * 60)
            self._print("")
            self._print("  Quick start commands:")

            # Profile-specific quick start commands
            if self.profile == "sd":
                self._print(
                    '    gaia sd "create a cute robot kitten and tell me a story"'
                )
                self._print('    gaia sd "sunset over mountains"')
                self._print(
                    "    gaia sd -i                                        # Interactive mode"
                )
            elif self.profile == "chat":
                self._print(
                    "    gaia chat              # Start interactive chat with RAG"
                )
                self._print("    gaia chat init         # Setup document folder")
            elif self.profile == "vlm":
                self._print(
                    "    gaia cache status      # Verify VLM model is available"
                )
                self._print("")
                self._print(
                    "  Vision model ready! Use with the driver logs processor or VLM SDK:"
                )
                self._print("    from gaia.vlm import StructuredVLMExtractor")
            elif self.profile == "minimal":
                self._print("    gaia llm 'Hello'       # Quick LLM query")
                self._print("")
                self._print(
                    "  Note: Minimal profile installed. For full features, run:"
                )
                self._print("    gaia init --profile chat")
            else:
                # Default commands for other profiles
                self._print("    gaia chat              # Start interactive chat")
                self._print("    gaia llm 'Hello'       # Quick LLM query")
                self._print("    gaia talk              # Voice interaction")
            self._print("")


def run_init(
    profile: str = "chat",
    skip_models: bool = False,
    skip_lemonade: bool = False,
    force_reinstall: bool = False,
    force_models: bool = False,
    yes: bool = False,
    verbose: bool = False,
    remote: bool = False,
) -> int:
    """
    Entry point for `gaia init` command.

    Args:
        profile: Profile to initialize (minimal, chat, code, rag, all)
        skip_models: Skip model downloads
        skip_lemonade: Skip Lemonade installation check (for CI)
        force_reinstall: Force reinstall even if compatible version exists
        force_models: Force re-download models (deletes then re-downloads)
        yes: Skip confirmation prompts
        verbose: Enable verbose output
        remote: Lemonade is on a remote machine (skip local start, still check version)

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    try:
        cmd = InitCommand(
            profile=profile,
            skip_models=skip_models,
            skip_lemonade=skip_lemonade,
            force_reinstall=force_reinstall,
            force_models=force_models,
            yes=yes,
            verbose=verbose,
            remote=remote,
        )
        return cmd.run()
    except ValueError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"❌ Unexpected error: {e}", file=sys.stderr)
        if verbose:
            import traceback

            traceback.print_exc()
        return 1
