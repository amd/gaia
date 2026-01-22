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
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

import requests

# Rich imports for better CLI formatting
try:
    from rich.console import Console
    from rich.panel import Panel

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from gaia.installer.lemonade_installer import LemonadeInfo, LemonadeInstaller
from gaia.version import LEMONADE_VERSION

log = logging.getLogger(__name__)

# Profile definitions mapping to agent profiles
# Note: These define which agent profile to use for each init profile
INIT_PROFILES = {
    "minimal": {
        "description": "Fast setup with lightweight model",
        "agent": "minimal",
        "models": ["Qwen3-4B-Instruct-2507-GGUF"],  # Override default minimal model
        "approx_size": "~2.5 GB",
    },
    "chat": {
        "description": "Interactive chat with RAG and vision support",
        "agent": "chat",
        "models": None,  # Use agent profile defaults
        "approx_size": "~25 GB",
    },
    "code": {
        "description": "Autonomous coding assistant",
        "agent": "code",
        "models": None,
        "approx_size": "~18 GB",
    },
    "rag": {
        "description": "Document Q&A with retrieval",
        "agent": "rag",
        "models": None,
        "approx_size": "~25 GB",
    },
    "all": {
        "description": "All models for all agents",
        "agent": "all",
        "models": None,
        "approx_size": "~26 GB",
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
        force_reinstall: bool = False,
        force_models: bool = False,
        yes: bool = False,
        verbose: bool = False,
        progress_callback: Optional[Callable[[InitProgress], None]] = None,
    ):
        """
        Initialize the init command.

        Args:
            profile: Profile to initialize (minimal, chat, code, rag, all)
            skip_models: Skip model downloads
            force_reinstall: Force reinstall even if compatible version exists
            force_models: Force re-download models even if already available
            yes: Skip confirmation prompts
            verbose: Enable verbose output
            progress_callback: Optional callback for progress updates
        """
        self.profile = profile.lower()
        self.skip_models = skip_models
        self.force_reinstall = force_reinstall
        self.force_models = force_models
        self.yes = yes
        self.verbose = verbose
        self.progress_callback = progress_callback

        # Validate profile
        if self.profile not in INIT_PROFILES:
            valid = ", ".join(INIT_PROFILES.keys())
            raise ValueError(f"Invalid profile '{profile}'. Valid profiles: {valid}")

        # Initialize Rich console if available (before installer for console pass-through)
        self.console = Console() if RICH_AVAILABLE else None

        # Use minimal installer for minimal profile
        use_minimal = self.profile == "minimal"

        self.installer = LemonadeInstaller(
            target_version=LEMONADE_VERSION,
            progress_callback=self._download_progress if verbose else None,
            minimal=use_minimal,
            console=self.console,
        )

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
            # Step 1: Check/Install Lemonade
            self._print_step(1, total_steps, "Checking Lemonade Server installation...")
            if not self._ensure_lemonade_installed():
                return 1

            # Step 2: Start server
            step_num = 2
            self._print("")
            self._print_step(step_num, total_steps, "Starting Lemonade Server...")
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
                return False

            return self._install_lemonade()

        else:
            self._print("   Lemonade Server not found")
            self._print("")

            if not self._prompt_yes_no(
                f"Install Lemonade v{LEMONADE_VERSION}?", default=True
            ):
                self._print("")
                self._print(
                    "   To install manually, visit: https://www.lemonade-server.ai"
                )
                return False

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

            # Prompt user to upgrade
            if not self._prompt_yes_no(
                f"Upgrade to v{target_ver}? (will uninstall current version)",
                default=True,
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

            # Install
            if RICH_AVAILABLE and self.console:
                self.console.print("   [bold]Installing...[/bold]")
            else:
                self._print("   Installing...")
            result = self.installer.install(installer_path, silent=True)

            if result.success:
                self._print_success(f"Installed Lemonade v{result.version}")

                # Verify installation by checking version
                if RICH_AVAILABLE and self.console:
                    self.console.print("   [dim]Verifying installation...[/dim]")
                else:
                    self._print("   Verifying installation...")
                verify_info = self.installer.check_installation()

                if verify_info.installed and verify_info.version:
                    self._print_success(
                        f"Verified: lemonade-server v{verify_info.version}"
                    )

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

        Returns:
            True if server is running and healthy, False on failure
        """
        import subprocess

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
            except Exception:
                pass  # Server not running, try to start

            # Try to start the server
            if RICH_AVAILABLE and self.console:
                self.console.print(
                    "   [dim]Server not running, attempting to start...[/dim]"
                )
            else:
                self._print("   Server not running, attempting to start...")

            # Find lemonade-server executable
            lemonade_path = self._find_lemonade_server()
            if not lemonade_path:
                self._print_error("lemonade-server not found in PATH")
                self._print("")
                if RICH_AVAILABLE and self.console:
                    self.console.print(
                        "   [yellow]The MSI installer updated the system PATH, but your[/yellow]"
                    )
                    self.console.print(
                        "   [yellow]current terminal session has the old PATH.[/yellow]"
                    )
                    self.console.print("")
                    self.console.print("   [bold]To fix this:[/bold]")
                    self.console.print(
                        "   [dim]1.[/dim] Close this terminal and open a new one"
                    )
                    self.console.print(
                        "   [dim]2.[/dim] Run [cyan]gaia init[/cyan] again"
                    )
                else:
                    self._print(
                        "   The MSI installer updated the system PATH, but your"
                    )
                    self._print("   current terminal session has the old PATH.")
                    self._print("")
                    self._print("   To fix this:")
                    self._print("   1. Close this terminal and open a new one")
                    self._print("   2. Run 'gaia init' again")
                return False

            # Show where we found the executable
            if self.verbose:
                if RICH_AVAILABLE and self.console:
                    self.console.print(f"   [dim]Found: {lemonade_path}[/dim]")
                else:
                    self._print(f"   Found: {lemonade_path}")

            # Start lemonade-server serve in background
            try:
                # Use subprocess.Popen to start in background
                # Redirect output to DEVNULL for clean background operation
                process = subprocess.Popen(
                    [lemonade_path, "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,  # Detach from parent process
                )
                log.debug(f"Started lemonade-server with PID {process.pid}")
            except Exception as e:
                self._print_error(f"Failed to start lemonade-server: {e}")
                return False

            # Wait for server to be ready (poll health endpoint)
            if RICH_AVAILABLE and self.console:
                self.console.print("   [dim]Waiting for server to be ready...[/dim]")
            else:
                self._print("   Waiting for server to be ready...")
            max_wait = 30  # seconds
            wait_interval = 1
            elapsed = 0

            while elapsed < max_wait:
                time.sleep(wait_interval)
                elapsed += wait_interval

                try:
                    health = client.health_check()
                    if health:
                        self._print_success("Server started successfully")
                        # Verify health details
                        if isinstance(health, dict):
                            status = health.get("status", "unknown")
                            if status == "ok":
                                self._print_success("Server health: OK")
                            else:
                                self._print_warning(f"Server status: {status}")
                        return True
                except Exception:
                    pass  # Keep waiting

            self._print_error("Server failed to start within timeout")
            if RICH_AVAILABLE and self.console:
                self.console.print(
                    "   [dim]Try starting manually with:[/dim] [cyan]lemonade-server serve[/cyan]"
                )
            else:
                self._print("   Try starting manually with: lemonade-server serve")
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

    def _create_progress_callback(self) -> Callable[[str, dict], None]:
        """
        Create a progress callback with its own state.

        Returns:
            A callback function that tracks download progress
        """
        state = {
            "last_update": time.time(),
            "last_bytes": 0,
            "last_percent": -1,
        }
        console = self.console if RICH_AVAILABLE else None

        def callback(event_type: str, data: dict) -> None:
            if event_type == "progress":
                percent = data.get("percent", 0)
                bytes_downloaded = data.get("bytes_downloaded", 0)
                bytes_total = data.get("bytes_total", 0)
                file_name = data.get("file", "")
                file_index = data.get("file_index", 1)
                total_files = data.get("total_files", 1)

                # Skip events with invalid bytes_total (Lemonade server bug)
                # These events can show 0 GB / 0 GB which is confusing
                if bytes_total == 0 and bytes_downloaded == 0:
                    return

                # Calculate speed
                now = time.time()
                elapsed = now - state["last_update"]
                speed_str = ""
                if elapsed > 0.5:  # Update speed every 0.5 seconds
                    bytes_delta = bytes_downloaded - state["last_bytes"]
                    speed = bytes_delta / elapsed / 1024 / 1024  # MB/s
                    speed_str = f" @ {speed:.1f} MB/s"
                    state["last_update"] = now
                    state["last_bytes"] = bytes_downloaded

                # Only update display every 2% or at start/end
                if (
                    percent >= state["last_percent"] + 2
                    or percent == 0
                    or percent == 100
                    or speed_str
                ):
                    # Format sizes
                    if bytes_total > 1024 * 1024 * 1024:  # > 1 GB
                        dl_str = f"{bytes_downloaded / 1024 / 1024 / 1024:.2f} GB"
                        total_str = f"{bytes_total / 1024 / 1024 / 1024:.2f} GB"
                    else:
                        dl_str = f"{bytes_downloaded / 1024 / 1024:.0f} MB"
                        total_str = f"{bytes_total / 1024 / 1024:.0f} MB"

                    # Progress bar
                    bar_width = 20
                    filled = int(bar_width * percent / 100)
                    bar = "━" * filled + "─" * (bar_width - filled)

                    # Single line progress (no flashing)
                    progress_line = (
                        f"   [{bar}] {percent:3d}%  {dl_str} / {total_str}{speed_str}"
                    )
                    print(f"\r{progress_line:<75}", end="", flush=True)

                    state["last_percent"] = percent

            elif event_type == "complete":
                print()  # Newline after progress
                if console:
                    console.print("   [green]✓[/green] Download complete")
                else:
                    print("   ✅ Download complete")
                state["last_percent"] = -1
                state["last_bytes"] = 0

            elif event_type == "error":
                print()
                error_msg = data.get("error", "Unknown error")
                if console:
                    console.print(f"   [red]❌ Error: {error_msg}[/red]")
                else:
                    print(f"   ❌ Error: {error_msg}")

        return callback

    def _verify_model(self, client, model_id: str) -> tuple:
        """
        Verify a model works by running a quick inference test.

        Args:
            client: LemonadeClient instance
            model_id: Model ID to verify

        Returns:
            Tuple of (success: bool, error_type: str or None)
            error_type is "corrupted" for actual file issues, "server_error" for transient issues
        """
        try:
            # Embedding models use different API - check if it's an embedding model
            model_lower = model_id.lower()
            is_embedding = "embed" in model_lower or "nomic" in model_lower

            if is_embedding:
                # Test embedding model
                response = client.embeddings(
                    model=model_id,
                    input="test",
                    timeout=60,  # Longer timeout for model loading
                )
                # Check if we got embeddings
                if response and response.get("data"):
                    return (True, None)
            else:
                # Test chat model with minimal tokens
                response = client.chat_completions(
                    model=model_id,
                    messages=[{"role": "user", "content": "Hi"}],
                    max_tokens=1,
                    timeout=60,  # Longer timeout for model loading
                    auto_download=False,  # Don't auto-download, we're testing
                )
                # Check if we got a valid response
                if response and response.get("choices"):
                    return (True, None)
            return (False, "no_response")
        except Exception as e:
            error_str = str(e).lower()
            # Check for actual corruption indicators
            if any(
                x in error_str for x in ["corrupt", "invalid", "truncated", "checksum"]
            ):
                return (False, "corrupted")
            # Server errors are not corruption - model files are likely fine
            log.debug(f"Model verification failed for {model_id}: {e}")
            return (False, "server_error")

    def _download_models(self) -> bool:
        """
        Download models for the selected profile.

        Returns:
            True if all models downloaded, False on failure
        """
        try:
            from gaia.llm.lemonade_client import LemonadeClient

            client = LemonadeClient(verbose=self.verbose)

            # Get profile config
            profile_config = INIT_PROFILES[self.profile]
            agent = profile_config["agent"]

            # Get models to download
            if profile_config["models"]:
                # Use profile-specific models (for minimal profile)
                model_ids = profile_config["models"]
            else:
                # Use agent profile defaults
                model_ids = client.get_required_models(agent)

            if not model_ids:
                self._print_success("No models required for this profile")
                return True

            # Track corrupted models for deletion before re-download
            models_corrupted = []

            # Force re-download: will delete each model just before re-downloading
            if self.force_models:
                models_to_redownload = []
                for model_id in model_ids:
                    if client.check_model_available(model_id):
                        models_to_redownload.append(model_id)

                if models_to_redownload:
                    if RICH_AVAILABLE and self.console:
                        self.console.print(
                            f"   [yellow]Force re-download requested[/yellow]"
                        )
                        self.console.print(
                            f"   [dim]Will delete and re-download:[/dim] {len(models_to_redownload)} model(s)"
                        )
                        for model_id in models_to_redownload:
                            self.console.print(
                                f"   [red]🗑️[/red] [cyan]{model_id}[/cyan]"
                            )
                    else:
                        self._print("   Force re-download requested")
                        self._print(
                            f"   Will delete and re-download: {len(models_to_redownload)} model(s)"
                        )
                        for model_id in models_to_redownload:
                            self._print(f"   🗑️ {model_id}")
                    self._print("")

                    if not self._prompt_yes_no(
                        "Delete existing models and re-download?", default=True
                    ):
                        self._print("   Skipping force re-download")
                        return True

                # All models need downloading (will delete just before each download)
                models_to_download = list(model_ids)
                models_available = []
            else:
                # Check which need downloading (with verification)
                models_to_download = []
                models_available = []
                models_corrupted = []
                models_missing = []

                for model_id in model_ids:
                    if client.check_model_available(model_id):
                        # Verify the model actually works
                        if RICH_AVAILABLE and self.console:
                            self.console.print(
                                f"   [dim]Verifying[/dim] [cyan]{model_id}[/cyan]...",
                                end="",
                            )
                        else:
                            print(f"   Verifying {model_id}...", end="", flush=True)

                        success, error_type = self._verify_model(client, model_id)
                        if success:
                            models_available.append(model_id)
                            if RICH_AVAILABLE and self.console:
                                self.console.print(" [green]✓[/green]")
                            else:
                                print(" ✓")
                        elif error_type == "corrupted":
                            # Actual file corruption - needs re-download
                            models_corrupted.append(model_id)
                            models_to_download.append(model_id)
                            if RICH_AVAILABLE and self.console:
                                self.console.print(" [red]✗ corrupted[/red]")
                            else:
                                print(" ✗ corrupted")
                        else:
                            # Server error - model is likely fine, skip verification
                            models_available.append(model_id)
                            if RICH_AVAILABLE and self.console:
                                self.console.print(
                                    " [yellow]⚠ server error (assuming OK)[/yellow]"
                                )
                            else:
                                print(" ⚠ server error (assuming OK)")
                    else:
                        models_missing.append(model_id)
                        models_to_download.append(model_id)
                        if RICH_AVAILABLE and self.console:
                            self.console.print(
                                f"   [yellow]📥[/yellow] [cyan]{model_id}[/cyan] [dim]not downloaded[/dim]"
                            )
                        else:
                            print(f"   📥 {model_id} - not downloaded")

                if models_corrupted:
                    self._print("")
                    self._print_warning(
                        f"Found {len(models_corrupted)} corrupted model(s) - will re-download"
                    )

            if not models_to_download:
                self._print_success("All models already downloaded")
                return True

            # Skip redundant prompt if force_models already confirmed
            if not self.force_models:
                if RICH_AVAILABLE and self.console:
                    self.console.print(
                        f"   [bold]Need to download:[/bold] {len(models_to_download)} model(s)"
                    )
                    for model_id in models_to_download:
                        self.console.print(
                            f"   [yellow]📥[/yellow] [cyan]{model_id}[/cyan]"
                        )
                    self.console.print(
                        f"   [dim]Estimated size:[/dim] {profile_config['approx_size']}"
                    )
                else:
                    self._print(
                        f"   Need to download: {len(models_to_download)} model(s)"
                    )
                    for model_id in models_to_download:
                        self._print(f"   📥 {model_id}")
                    self._print(f"   Estimated size: {profile_config['approx_size']}")
                self._print("")

                if not self._prompt_yes_no("Continue with download?", default=True):
                    self._print("   Skipping model downloads")
                    return True

            # Download each model
            success = True
            for model_id in models_to_download:
                self._print("")

                # Delete model if force re-download or corrupted
                should_delete = (
                    self.force_models or model_id in models_corrupted
                ) and client.check_model_available(model_id)

                if should_delete:
                    reason = (
                        "corrupted"
                        if model_id in models_corrupted
                        else "force re-download"
                    )
                    if RICH_AVAILABLE and self.console:
                        self.console.print(
                            f"   [dim]Deleting ({reason})[/dim] [cyan]{model_id}[/cyan]..."
                        )
                    else:
                        self._print(f"   Deleting ({reason}) {model_id}...")
                    try:
                        client.delete_model(model_id)
                        self._print_success(f"Deleted {model_id}")
                    except Exception as e:
                        self._print_error(f"Failed to delete {model_id}: {e}")
                        # Continue to try downloading anyway

                if RICH_AVAILABLE and self.console:
                    self.console.print(
                        f"   [bold]Downloading:[/bold] [cyan]{model_id}[/cyan]"
                    )
                else:
                    self._print(f"   Downloading: {model_id}")

                # Create progress callback for this model
                progress_callback = self._create_progress_callback()

                try:
                    for event in client.pull_model_stream(
                        model_name=model_id,
                        timeout=7200,  # 2 hour timeout for large models
                        progress_callback=progress_callback,
                    ):
                        if event.get("event") == "error":
                            self._print_error(f"Failed to download {model_id}")
                            success = False
                            break
                except requests.exceptions.Timeout:
                    self._print("")
                    self._print_error(f"Download timed out for {model_id}")
                    self._print("   Try downloading via Lemonade app or retry later")
                    success = False
                except requests.exceptions.ConnectionError as e:
                    self._print("")
                    self._print_error(f"Connection error: {e}")
                    self._print("   Check your network connection and retry")
                    success = False
                except Exception as e:
                    self._print("")
                    self._print_error(f"Download failed: {e}")
                    success = False

            return success

        except Exception as e:
            self._print_error(f"Error downloading models: {e}")
            return False

    def _verify_setup(self) -> bool:
        """
        Verify the setup is working.

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

            # Check models
            profile_config = INIT_PROFILES[self.profile]
            if profile_config["models"]:
                model_ids = profile_config["models"]
            else:
                model_ids = client.get_required_models(profile_config["agent"])

            if model_ids and not self.skip_models:
                available = sum(1 for m in model_ids if client.check_model_available(m))
                self._print_success(f"Models ready: {available}/{len(model_ids)}")

                if available < len(model_ids):
                    self._print_warning("Some models are not downloaded")

            return True

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

            profile_config = INIT_PROFILES[self.profile]
            if profile_config["agent"] == "minimal":
                self.console.print(
                    "  [dim]Note: Minimal profile installed. For full features, run:[/dim]"
                )
                self.console.print("    [cyan]gaia init --profile chat[/cyan]")
                self.console.print()
        else:
            self._print("")
            self._print("=" * 60)
            self._print("  GAIA initialization complete!")
            self._print("=" * 60)
            self._print("")
            self._print("  Quick start commands:")
            self._print("    gaia chat              # Start interactive chat")
            self._print("    gaia llm 'Hello'       # Quick LLM query")
            self._print("    gaia talk              # Voice interaction")
            self._print("")

            profile_config = INIT_PROFILES[self.profile]
            if profile_config["agent"] == "minimal":
                self._print(
                    "  Note: Minimal profile installed. For full features, run:"
                )
                self._print("    gaia init --profile chat")
                self._print("")


def run_init(
    profile: str = "chat",
    skip_models: bool = False,
    force_reinstall: bool = False,
    force_models: bool = False,
    yes: bool = False,
    verbose: bool = False,
) -> int:
    """
    Entry point for `gaia init` command.

    Args:
        profile: Profile to initialize (minimal, chat, code, rag, all)
        skip_models: Skip model downloads
        force_reinstall: Force reinstall even if compatible version exists
        force_models: Force re-download models (deletes then re-downloads)
        yes: Skip confirmation prompts
        verbose: Enable verbose output

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    try:
        cmd = InitCommand(
            profile=profile,
            skip_models=skip_models,
            force_reinstall=force_reinstall,
            force_models=force_models,
            yes=yes,
            verbose=verbose,
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
