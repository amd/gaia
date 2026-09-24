# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA Init Command

Main entry point for `gaia init` command that:
1. Installs and starts GAIA's embedded Lemonade Server (or checks the one
   LEMONADE_BASE_URL names)
2. Downloads required models for the selected profile
3. Verifies setup is working
"""

import importlib.util
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# Rich imports for better CLI formatting
try:
    from rich.console import Console
    from rich.markup import escape as rich_escape
    from rich.panel import Panel

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from gaia.agents.base.console import AgentConsole
from gaia.agents.install_hints import source_install_command
from gaia.installer._stdin import stdin_is_tty
from gaia.ui.build import WebuiBuildStatus

log = logging.getLogger(__name__)


def is_embedding_model_id(model_id: str) -> bool:
    """Whether a model id names an embedding model rather than a chat LLM.

    Centralized so the Claude-backend skip (models required for RAG/memory
    embeddings only) and the existing verify/test-inference branches agree
    on the same rule instead of each re-deriving it.
    """
    return "embed" in model_id.lower()


# Hub agent ids `gaia init` installs for its profile. Everything else
# (sd/vlm/email/...) owns its own install lifecycle; a generic "install the
# profile's agent" would hard-fail on agents the hub index doesn't carry.
HUB_INSTALL_AGENTS = frozenset({"chat", "gaia"})

# Hub agent id -> the module a source/pip install of it makes importable. Every
# `gaia-agent-<id>` distribution installs `gaia_agent_<id>` except the flagship:
# `gaia-agent-gaia` ships plain `gaia_agent` (hub/agents/gaia/python's
# gaia-agent.yaml `entry_module`).
_AGENT_IMPORT_NAMES = {"gaia": "gaia_agent"}

# The profile a bare `gaia init` runs — the flagship agent's configuration.
DEFAULT_INIT_PROFILE = "gaia"

# Profile definitions mapping to agent profiles
# Note: These define which agent profile to use for each init profile
INIT_PROFILES = {
    "gaia": {
        "description": "The flagship GAIA agent — chat, documents, data, web, memory",
        # Installs the flagship from the Agent Hub, which publishes it as a
        # native binary (`gaia-agent`), not a wheel -- so this does NOT bring
        # `gaia-agent-chat` along the way a pip dependency would. `gaia chat`
        # still needs that wheel separately; the completion message says so.
        "agent": "gaia",
        "models": ["Gemma-4-E4B-it-GGUF", "user.embeddinggemma-300m-GGUF"],
        "approx_size": "~4 GB",
        # EmbeddingGemma loads only on Lemonade v10.9.0+ (see the chat profile).
        "min_lemonade_version": "10.9.0",
        "min_context_size": 32768,
        "pip_extras": ["rag"],
    },
    "minimal": {
        "description": "Fast setup with Gemma 4 E4B multimodal model",
        "agent": "minimal",
        "models": ["Gemma-4-E4B-it-GGUF"],
        "approx_size": "~3 GB",
        "min_lemonade_version": "10.2.0",
        "min_context_size": 32768,
        "pip_extras": [],
    },
    "sd": {
        "description": "Image generation with multi-modal AI (LLM + SD)",
        "agent": "sd",
        "models": [
            "SDXL-Turbo",  # Image generation (6.5GB)
            "Gemma-4-E4B-it-GGUF",  # Agentic reasoning + VLM + prompt enhancement (~3GB)
        ],
        "approx_size": "~10 GB",
        "min_lemonade_version": "10.2.0",
        "min_context_size": 32768,
        "pip_extras": [],
    },
    "chat": {
        "description": "Interactive chat with RAG and vision support",
        "agent": "chat",
        "models": ["Gemma-4-E4B-it-GGUF", "user.embeddinggemma-300m-GGUF"],
        "approx_size": "~4 GB",
        # EmbeddingGemma is validated on Lemonade v10.9.0; older bundled
        # llama.cpp builds fail to load it. Floor the version so init fails
        # loudly instead of the embedder failing at first RAG index.
        "min_lemonade_version": "10.9.0",
        "min_context_size": 32768,
        "pip_extras": ["rag"],
    },
    "rag": {
        "description": "Document Q&A with retrieval",
        "agent": "rag",
        "models": ["Gemma-4-E4B-it-GGUF", "user.embeddinggemma-300m-GGUF"],
        "approx_size": "~4 GB",
        # EmbeddingGemma loads only on Lemonade v10.9.0+ (see chat profile).
        "min_lemonade_version": "10.9.0",
        "min_context_size": 32768,
        "pip_extras": ["rag"],
    },
    "vlm": {
        "description": "Vision pipeline for document and image extraction",
        "agent": "vlm",
        "models": ["Gemma-4-E4B-it-GGUF"],
        "approx_size": "~3 GB",
        "min_lemonade_version": "10.2.0",
        "min_context_size": 32768,
        "pip_extras": [],
    },
    "email": {
        "description": "Email triage for Gmail/Outlook (local inference)",
        "agent": "email",
        "models": ["Gemma-4-E4B-it-GGUF"],
        "approx_size": "~3 GB",
        # Keep in lock-step with gaia_agent_email.version.MIN_LEMONADE_VERSION
        # and the email gaia-agent.yaml manifest (the GET /v1/email/init readiness
        # check reads the same minimum). A test asserts the three agree.
        "min_lemonade_version": "10.2.0",
        "min_context_size": 32768,
        "pip_extras": [],
    },
    "npu": {
        "description": "Ryzen AI NPU acceleration via FLM backend (requires XDNA2 NPU)",
        "agent": "chat",
        # FLM chat model + FLM-native embedder so chat and embeddings stay
        # co-resident on the NPU backend. A GGUF embedder would run on Vulkan
        # and evict the FLM chat model every turn (#1744). Both are built-in
        # Lemonade *-FLM models, pulled by name only (no recipe — #1655).
        "models": ["gemma4-it-e2b-FLM", "embed-gemma-300m-FLM"],
        "approx_size": "~3 GB",
        "min_lemonade_version": "10.2.0",
        # NPU context window. Matches GPU/CPU (32768) so the init report and
        # the runtime load path agree (issue #1745) — the prior 4096 pin made
        # `gaia init --profile npu` report 4096 while the loader requested
        # 32768. FLM at 32k is confirmed loading on a Ryzen AI 7 350 / 16 GB.
        "min_context_size": 32768,
        "pip_extras": [],
        # NPU-specific keys (not present on other profiles):
        "recipe": "flm",
        "backend": "flm:npu",
        "required_device": "amd_npu",
    },
    "all": {
        "description": "All models for all agents",
        "agent": "all",
        "models": None,
        "approx_size": "~26 GB",
        # Includes EmbeddingGemma, which loads only on Lemonade v10.9.0+.
        "min_lemonade_version": "10.9.0",
        "min_context_size": 32768,  # Max requirement across all agents
        "pip_extras": ["rag"],
    },
}


@dataclass
class InitProgress:
    """Progress information for the init command."""

    step: int
    total_steps: int
    step_name: str
    message: str


@dataclass
class SetupStatus:
    """Result of a read-only `gaia init --check` readiness probe.

    ``reasons`` is empty exactly when ``ready`` is True — each entry names one
    thing `gaia init` would still have to do, in plain language, so a caller
    (the TUI's first-boot gate) can show it verbatim.
    """

    ready: bool
    reasons: list


def check_setup_status(
    profile: str = DEFAULT_INIT_PROFILE,
    skip_chat_model: bool = False,
    remote: bool = False,
) -> SetupStatus:
    """Check whether `gaia init --profile <profile>` still has work to do.

    Read-only: never installs, starts a server, prompts, or downloads
    anything. Checks the SAME real state `run()` itself acts on (GAIA's
    Lemonade Server installed + running, required models present) so this can never
    disagree with what `gaia init` would actually do — the alternative, a
    marker file recording "setup ran once", goes stale the moment a model is
    deleted or Lemonade is uninstalled without GAIA's knowledge.

    Args:
        profile: Profile to check (gaia, minimal, chat, rag, all, ...)
        skip_chat_model: Match run()'s --skip-chat-model filtering (Claude
            backend): only the profile's embedding model(s) are required.
        remote: Check the server LEMONADE_BASE_URL names, which must be set.
            A configured URL is checked the same way without it.

    Returns:
        SetupStatus with ready=True iff nothing below would need to run.
    """
    profile = profile.lower()
    if profile not in INIT_PROFILES:
        valid = ", ".join(INIT_PROFILES.keys())
        raise ValueError(f"Invalid profile '{profile}'. Valid profiles: {valid}")

    from gaia.llm.lemonade_client import (
        LemonadeClient,
        LemonadeClientError,
        resolve_lemonade_base_url,
    )

    profile_config = INIT_PROFILES[profile]
    configured = os.environ.get("LEMONADE_BASE_URL", "").strip()
    if remote and not configured:
        raise ValueError("--remote needs LEMONADE_BASE_URL set to the server to check.")

    if configured:
        base_url = resolve_lemonade_base_url(configured)
    else:
        from gaia.llm.lemonade_embedded import EmbeddedLemonade

        embedded = EmbeddedLemonade()
        status = embedded.status()
        # Model availability can't be probed without a running server, so a
        # stopped one is the only thing this check can report.
        if status.unresponsive_pid:
            return SetupStatus(
                ready=False,
                reasons=[
                    f"GAIA's Lemonade Server (pid {status.unresponsive_pid}) "
                    "is running but not answering"
                ],
            )
        if not status.installed:
            return SetupStatus(
                ready=False, reasons=["GAIA's Lemonade Server is not installed"]
            )
        if not status.running:
            return SetupStatus(
                ready=False,
                reasons=["GAIA's Lemonade Server is installed but not running"],
            )
        if status.version != embedded.version:
            return SetupStatus(
                ready=False,
                reasons=[
                    f"GAIA's Lemonade Server is v{status.version}; this GAIA "
                    f"needs v{embedded.version}"
                ],
            )
        base_url = status.base_url

    client = LemonadeClient(base_url=base_url, verbose=False)
    try:
        client.health_check()
    except LemonadeClientError as e:
        return SetupStatus(
            ready=False,
            reasons=[f"Lemonade Server at {base_url} is not reachable: {e}"],
        )

    if profile_config["models"]:
        model_ids = list(profile_config["models"])
    else:
        model_ids = client.get_required_models(profile_config["agent"])

    if profile not in ("sd", "npu") and not skip_chat_model:
        from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

        if DEFAULT_MODEL_NAME not in model_ids:
            model_ids = list(model_ids) + [DEFAULT_MODEL_NAME]

    if skip_chat_model:
        model_ids = [m for m in model_ids if is_embedding_model_id(m)]

    reasons = []
    for model_id in model_ids:
        try:
            available = client.check_model_available(model_id)
        except LemonadeClientError as e:
            reasons.append(f"Could not check model '{model_id}': {e}")
            continue
        if not available:
            reasons.append(f"Model '{model_id}' is not downloaded")

    return SetupStatus(ready=not reasons, reasons=reasons)


class InitCommand:
    """
    Main handler for the `gaia init` command.

    Orchestrates the full initialization workflow:
    1. Install and start GAIA's embedded Lemonade Server
    2. Download models for profile
    3. Verify setup
    """

    # Per-model context verification state, set dynamically during model
    # verification. Declared here (without assignment) so its *absence* on the
    # instance keeps meaning "verification not attempted" while satisfying the
    # pylint attribute-defined-outside-init check.
    _ctx_verified: "Optional[int]"

    def __init__(
        self,
        profile: str = DEFAULT_INIT_PROFILE,
        skip_models: bool = False,
        force_reinstall: bool = False,
        force_models: bool = False,
        yes: bool = False,
        verbose: bool = False,
        remote: bool = False,
        skip_webui_build: bool = False,
        skip_chat_model: bool = False,
        progress_callback: Optional[Callable[[InitProgress], None]] = None,
    ):
        """
        Initialize the init command.

        Args:
            profile: Profile to initialize (minimal, chat, rag, all)
            skip_models: Skip model downloads
            force_reinstall: Reinstall GAIA's embedded Lemonade Server
            force_models: Force re-download models even if already available
            yes: Skip confirmation prompts
            verbose: Enable verbose output
            remote: Use the Lemonade Server LEMONADE_BASE_URL names instead
                of GAIA's own; set automatically for a non-local URL
            skip_webui_build: Skip the Agent UI frontend build step entirely
                (same-day escape hatch if the Node preflight ever false-positives;
                same effect as setting GAIA_SKIP_WEBUI_BUILD)
            skip_chat_model: Skip the profile's chat LLM (e.g. Gemma-4-E4B-it-GGUF)
                while still downloading any embedding model it declares. For a
                session whose inference runs on Anthropic's Claude API instead of
                the local backend: the chat model would never be used, but
                RAG/memory/code-index embeddings have no Claude equivalent and
                still need Lemonade's embedder (see hub/agents/gaia/python/
                gaia_agent/stdio.py). Ignored when skip_models is already set.
            progress_callback: Optional callback for progress updates
        """
        self.profile = profile.lower()
        self.skip_models = skip_models
        self.skip_webui_build = skip_webui_build
        self.force_reinstall = force_reinstall
        self.force_models = force_models
        self.yes = yes
        self.verbose = verbose
        self.remote = remote
        self.skip_chat_model = skip_chat_model
        self.progress_callback = progress_callback

        # A configured server is someone else's to run; init only checks it.
        self._lemonade_base_url = (
            os.environ.get("LEMONADE_BASE_URL", "").strip() or None
        )
        if self.remote and not self._lemonade_base_url:
            raise ValueError(
                "--remote needs LEMONADE_BASE_URL set to the server to use, e.g. "
                "LEMONADE_BASE_URL=http://<host>:13305. Without it, drop --remote "
                "and `gaia init` sets up GAIA's own Lemonade Server."
            )
        if self._lemonade_base_url:
            from urllib.parse import urlparse

            hostname = urlparse(self._lemonade_base_url).hostname or "localhost"
            if hostname not in ("localhost", "127.0.0.1", "::1"):
                self.remote = True

        # Validate profile
        if self.profile not in INIT_PROFILES:
            valid = ", ".join(INIT_PROFILES.keys())
            raise ValueError(f"Invalid profile '{profile}'. Valid profiles: {valid}")

        # Initialize Rich console if available (before installer for console pass-through)
        self.console = Console() if RICH_AVAILABLE else None

        # Initialize AgentConsole for formatted output
        self.agent_console = AgentConsole()

        # Context verification state. _ctx_verified is set per-model during
        # verification (only for LLM models with a min context size); its
        # absence means verification was not attempted for that model.
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
            # Escape the message so brackets in it (e.g. "[rag]") aren't eaten
            # as Rich markup tags.
            self.console.print(
                f"[bold blue]Step {step}/{total}:[/bold blue] {rich_escape(message)}"
            )
        else:
            self._print(f"Step {step}/{total}: {message}")

    def _print_success(self, message: str):
        """Print success message."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"   [green]✓[/green] {rich_escape(message)}")
        else:
            self._print(f"   ✓ {message}")

    def _print_warning(self, message: str):
        """Print warning message."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"   [yellow]⚠️  {rich_escape(message)}[/yellow]")
        else:
            self._print(f"   ⚠️  {message}")

    def _print_error(self, message: str):
        """Print error message."""
        if RICH_AVAILABLE and self.console:
            self.console.print(f"   [red]❌ {rich_escape(message)}[/red]")
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
        except EOFError:
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

    def _install_pip_extras(self) -> bool:
        """
        Install pip extras required by the current profile.

        Returns:
            True on success or if no extras needed, False on failure.
        """
        profile_config = INIT_PROFILES[self.profile]
        pip_extras = profile_config.get("pip_extras", [])
        if not pip_extras:
            return True

        extras_str = ",".join(pip_extras)

        # Package-manager frontends to try, most-preferred first. The standalone
        # ``uv`` binary leads because uv-created venvs ship neither ``pip`` nor
        # the ``uv`` module, so ``python -m uv`` / ``python -m pip`` both fail
        # there; the standalone binary honours the active VIRTUAL_ENV instead.
        frontends = [
            ["uv", "pip"],
            [sys.executable, "-m", "uv", "pip"],
            [sys.executable, "-m", "pip"],
        ]

        # Detect editable vs package install using whichever frontend responds.
        editable = False
        location = ""
        for frontend in frontends:
            try:
                result = subprocess.run(
                    frontend + ["show", "amd-gaia"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except (FileNotFoundError, OSError):
                continue
            if result.returncode != 0:
                continue
            for line in result.stdout.splitlines():
                if line.startswith("Editable project location:"):
                    editable = True
                    location = line.split(":", 1)[1].strip()
                    break
            break

        # The fallback message must resolve in a stock venv with no `uv` on
        # PATH (same reasoning as gaia.agents.install_hints.
        # source_install_command, #2358) -- this is the frontend the loop
        # below always ends up trying last, so it's the one the user's
        # terminal message must actually work with.
        if editable and location:
            install_spec = f'{sys.executable} -m pip install -e ".[{extras_str}]"'
            install_args = ["install", "-e", f"{location}[{extras_str}]"]
        else:
            install_spec = f'{sys.executable} -m pip install "amd-gaia[{extras_str}]"'
            install_args = ["install", f"amd-gaia[{extras_str}]"]

        self._print_success(f"Installing extras: {extras_str}")

        for frontend in frontends:
            try:
                result = subprocess.run(
                    frontend + install_args,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
                if result.returncode == 0:
                    self._print_success(f"Installed [{extras_str}] dependencies")
                    return True
            except (FileNotFoundError, OSError):
                continue
            except subprocess.TimeoutExpired:
                self._print_warning(
                    f"Pip install timed out. Please run manually: {install_spec}"
                )
                return True
            except Exception:
                continue

        self._print_warning(
            f"Could not install [{extras_str}] extras automatically. "
            f"Please run: {install_spec}"
        )
        return True  # Warn but don't fail

    def run(self) -> int:
        """
        Execute the initialization workflow.

        Returns:
            Exit code (0 for success, non-zero for failure)
        """
        # No one to answer prompts non-interactively -- refuse instead of
        # silently declining every one and claiming a setup that never ran.
        if not self.yes and not stdin_is_tty():
            print(
                f"Error: refusing to run 'gaia init --profile {self.profile}' "
                "non-interactively without --yes.\n"
                "Pass --yes to auto-confirm setup prompts (add --skip-models "
                "to also skip downloading models).",
                file=sys.stderr,
            )
            return 1

        self._print_header()

        profile_config = INIT_PROFILES[self.profile]
        has_pip_extras = bool(profile_config.get("pip_extras"))
        # Data-driven scope (#2358): "chat" and "npu" both declare
        # `"agent": "chat"` (they resolve to the same standalone wheel), so
        # keying off the declared agent -- not a hardcoded profile-name
        # literal -- naturally covers both without a special case, and never
        # touches profiles for other hub agents (gaia/email/...), each of
        # which has its own, separately-owned install lifecycle.
        has_hub_agent_check = profile_config.get("agent") in HUB_INSTALL_AGENTS

        _webui_src = Path(__file__).resolve().parent.parent / "apps" / "webui" / "src"
        _is_dev_install = _webui_src.is_dir()
        _runs_webui_build = _is_dev_install and not self.skip_webui_build

        has_device_check = bool(profile_config.get("required_device"))
        has_backend_install = bool(profile_config.get("backend"))

        total_steps = 3 if not self.skip_models else 2
        if has_device_check:
            total_steps += 1
        if has_backend_install:
            total_steps += 1
        if has_pip_extras:
            total_steps += 1
        if has_hub_agent_check:
            total_steps += 1
        if _runs_webui_build:
            total_steps += 1

        try:
            step_num = 1
            self._print_step(step_num, total_steps, "Starting Lemonade Server...")
            if not self._ensure_lemonade_ready():
                return 1

            # NPU-specific: Detect hardware
            if has_device_check:
                step_num += 1
                self._print("")
                self._print_step(step_num, total_steps, "Detecting NPU hardware...")
                if not self._check_device_available():
                    return 1

            # NPU-specific: Install backend
            if has_backend_install:
                step_num += 1
                self._print("")
                backend_spec = profile_config.get("backend", "")
                self._print_step(
                    step_num,
                    total_steps,
                    f"Installing {backend_spec} backend...",
                )
                if not self._install_backend():
                    return 1

            # Step 3+: Download models (unless skipped)
            if not self.skip_models:
                step_num += 1
                self._print("")
                self._print_step(
                    step_num,
                    total_steps,
                    f"Downloading models for '{self.profile}' profile...",
                )
                if not self._download_models():
                    return 1

            # Install pip extras (after models, before verify)
            if has_pip_extras:
                step_num += 1
                self._print("")
                self._print_step(
                    step_num, total_steps, "Installing Python dependencies..."
                )
                self._install_pip_extras()

            # Ensure the profile's hub agent (chat's standalone wheel) is
            # installed (#2358). Independent of the pip-extras step above:
            # the hub install targets the isolated
            # ~/.gaia/agents/chat/site-packages dir, while [rag] extras
            # target the ACTIVE interpreter — one must not replace or block
            # the other. Unlike _install_pip_extras (warn-but-continue), a
            # genuine failure here is allowed to propagate into this
            # method's own top-level `except Exception` below, which already
            # converts it into an actionable non-zero exit — silently
            # continuing would just recreate the "chat isn't installed"
            # state this issue exists to close.
            if has_hub_agent_check:
                step_num += 1
                self._print("")
                self._print_step(
                    step_num,
                    total_steps,
                    f"Checking {profile_config['agent']} agent installation...",
                )
                self._ensure_hub_agent_installed()

            # Build Agent UI frontend (dev/source installs only). No
            # try/except here: ensure_webui_built() never raises for an
            # expected toolchain/version/build failure -- it reports those
            # via webui_build_result.status, checked below.
            webui_build_result = None
            if _runs_webui_build:
                step_num += 1
                self._print("")
                self._print_step(step_num, total_steps, "Building Agent UI frontend...")
                from gaia.ui.build import ensure_webui_built

                webui_build_result = ensure_webui_built(
                    log_fn=self._print, warn_fn=self._print_warning
                )
                # Suppress the success line when OK carries a message (the
                # stale-but-usable-dist outcome) -- printing "ready" right
                # under a build-failure warning is the muted version of the
                # bug this issue exists to fix.
                if (
                    webui_build_result.status == WebuiBuildStatus.OK
                    and not webui_build_result.message
                ):
                    self._print_success("Agent UI frontend ready")

            # Final step: Verify setup
            step_num += 1
            self._print("")
            self._print_step(step_num, total_steps, "Verifying setup...")
            if not self._verify_setup():
                return 1

            # Persist profile choice to ~/.gaia/config.json
            try:
                from gaia.config import GaiaConfig, GaiaConfigError

                # Load-then-update so a user-set default_model (or any future
                # field) survives re-running `gaia init`. init is also the
                # natural recovery path, so if the existing file is corrupt,
                # reset to a fresh config rather than leaving the bad file.
                try:
                    config = GaiaConfig.load()
                except GaiaConfigError as e:
                    log.warning(f"Resetting corrupt config: {e}")
                    config = GaiaConfig()
                config.profile = self.profile
                config.default_device = "npu" if self.profile == "npu" else "gpu"
                config.save()
            except Exception as e:
                self._print_error(
                    f"Failed to save profile '{self.profile}' to "
                    f"~/.gaia/config.json: {e}. Setup otherwise completed, but "
                    "GAIA will fall back to its default profile until this is "
                    "fixed -- re-run `gaia init` or `gaia config set profile "
                    f"{self.profile}` once the cause is resolved."
                )
                return 1

            # A hard Agent UI build failure means the profile's UI isn't
            # usable -- don't report plain success for it. verify_setup and
            # config persistence above already ran unconditionally, since
            # neither depends on the frontend build. The build step above
            # already printed the actionable message via warn_fn; don't
            # repeat the full paragraph, just name the outcome.
            if webui_build_result is not None and webui_build_result.status in (
                WebuiBuildStatus.NODE_TOO_OLD,
                WebuiBuildStatus.BUILD_FAILED,
            ):
                self._print_error("Agent UI frontend build failed -- see above.")
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

    def _ensure_lemonade_ready(self) -> bool:
        """Get a Lemonade Server answering: the configured one, or GAIA's own.

        ``LEMONADE_BASE_URL`` names a server someone else runs, so init only
        checks it. Otherwise init installs and starts GAIA's embedded Lemonade,
        which needs no admin rights and never touches a system-wide install.

        Returns:
            True when a server is up, False after printing why it is not.
        """
        if self._lemonade_base_url:
            return self._check_configured_server()
        return self._start_embedded_server()

    def _check_configured_server(self) -> bool:
        """Check the server ``LEMONADE_BASE_URL`` names: reachable and new enough."""
        from gaia.agents.base.readiness import version_meets_min
        from gaia.llm.lemonade_client import (
            LemonadeClient,
            LemonadeClientError,
            resolve_lemonade_base_url,
        )

        url = resolve_lemonade_base_url(self._lemonade_base_url)
        try:
            health = LemonadeClient(base_url=url, verbose=self.verbose).health_check()
        except LemonadeClientError as e:
            self._print_error(f"Lemonade Server at {url} is not reachable: {e}")
            self._print(
                "   Start that server, or unset LEMONADE_BASE_URL so `gaia init` "
                "sets up GAIA's own."
            )
            return False

        version = health.get("version") if isinstance(health, dict) else None
        minimum = INIT_PROFILES[self.profile].get("min_lemonade_version")
        if version_meets_min(version, minimum) is False:
            self._print_error(
                f"Lemonade Server at {url} is v{version}; the '{self.profile}' "
                f"profile needs v{minimum} or newer."
            )
            self._print(
                "   Upgrade that server, or unset LEMONADE_BASE_URL so `gaia init` "
                "sets up GAIA's own."
            )
            return False

        label = f"Lemonade Server v{version}" if version else "Lemonade Server"
        self._print_success(f"Using {label} at {url}")
        return True

    def _start_embedded_server(self) -> bool:
        """Install GAIA's embedded Lemonade if needed, then start it.

        Init is the recovery path, so it also replaces an instance running an
        older version and one that stopped answering.
        """
        from gaia.llm.lemonade_embedded import (
            EmbeddedLemonade,
            EmbeddedLemonadeError,
        )

        embedded = EmbeddedLemonade(progress_callback=self._download_progress)
        try:
            current = embedded.status()
            if current.unresponsive_pid:
                self._print(
                    f"   Lemonade Server (pid {current.unresponsive_pid}) stopped "
                    "answering -- restarting it..."
                )
                embedded.stop()
            elif current.running and current.version != embedded.version:
                self._print(
                    f"   Replacing Lemonade Server v{current.version} with "
                    f"v{embedded.version}..."
                )
                embedded.stop()
            elif current.running and self.force_reinstall:
                self._print("   Stopping Lemonade Server to reinstall it...")
                embedded.stop()

            if self.force_reinstall or not embedded.is_installed():
                self._print(f"   Downloading Lemonade Server v{embedded.version}...")
                embedded.install(force=self.force_reinstall)
                self._print("")
                self._print_success(
                    f"Installed Lemonade Server v{embedded.version} "
                    f"in {embedded.dist_dir}"
                )

            status = embedded.start(install_if_missing=False)
        except EmbeddedLemonadeError as e:
            self._print_error(str(e))
            return False

        self._print_success(
            f"Lemonade Server v{status.version} running on port {status.port}"
        )
        return True

    def _server_problem_hint(self) -> str:
        """Where to look when the server misbehaves after it started."""
        if self._lemonade_base_url:
            return f"Check the Lemonade Server at {self._lemonade_base_url}."
        from gaia.llm.lemonade_embedded import EmbeddedLemonade

        return f"Read {EmbeddedLemonade().log_path} for the server's own error."

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

    def _check_device_available(self) -> bool:
        """Check that the required hardware device is available.

        Only called for profiles with a ``required_device`` key (e.g. NPU).
        Fails loudly if the device is not detected — no silent fallback.

        Returns:
            True if device is available, False on failure.
        """
        profile_config = INIT_PROFILES[self.profile]
        required = profile_config.get("required_device")
        if not required:
            return True

        from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError

        try:
            client = LemonadeClient(verbose=self.verbose)
            sysinfo = client.get_system_info()
            devices = sysinfo.get("devices", {})

            device_info = devices.get(required, {})
            available = device_info.get("available", False)

            if available:
                name = device_info.get("name", required)
                self._print_success(f"Detected: {name}")
                return True

            # Device not available — actionable error
            device_label = required.replace("amd_", "AMD ").upper()
            self._print_error(
                f"No {device_label} detected. "
                f"The '{self.profile}' profile requires {device_label} hardware "
                f"(Ryzen AI 300/400/Max series with XDNA2)."
            )
            self._print_error("Run 'gaia init' for GPU-based setup instead.")
            return False
        except LemonadeClientError as e:
            self._print_error(f"Lemonade Server could not report the hardware: {e}")
            self._print_error(self._server_problem_hint())
            return False

    def _install_backend(self) -> bool:
        """Install the Lemonade backend required by the current profile.

        Only called for profiles with a ``backend`` key (e.g. ``"flm:npu"``).
        Checks recipe status first to skip if already installed.

        Returns:
            True if backend is ready, False on failure.
        """
        profile_config = INIT_PROFILES[self.profile]
        backend_spec = profile_config.get("backend")
        if not backend_spec:
            return True

        from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError

        try:
            client = LemonadeClient(verbose=self.verbose)

            # Check if already installed via recipe status
            recipe_name = profile_config.get("recipe", backend_spec.split(":")[0])
            recipe_status = client.get_recipe_status(recipe_name)

            if recipe_status:
                backends = recipe_status.get("backends", {})
                backend_key = backend_spec.split(":")[-1] if ":" in backend_spec else ""
                backend_info = backends.get(backend_key, {})

                if backend_info.get("state") == "installed":
                    self._print_success(f"Backend '{backend_spec}' already installed")
                    return True

            # Install the backend
            self._print(f"   Installing backend: {backend_spec}...")
            client.install_backend(backend_spec)
            self._print_success(f"Backend '{backend_spec}' installed")
            return True

        except LemonadeClientError as e:
            self._print_error(f"Failed to install backend '{backend_spec}': {e}")
            self._print_error(self._server_problem_hint())
            return False

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

            # Include default GPU model for profiles that use llamacpp.
            # SD profile has its own LLM and doesn't need the default model.
            # NPU profile uses FLM models exclusively — don't append GGUF model.
            if self.profile not in ("sd", "npu") and not self.skip_chat_model:
                from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

                if DEFAULT_MODEL_NAME not in model_ids:
                    model_ids = list(model_ids) + [DEFAULT_MODEL_NAME]

            # A Claude-backed session never calls the local chat LLM — only
            # RAG/memory/code-index embeddings still need Lemonade (Anthropic has
            # no embeddings API). Drop every non-embedding model rather than
            # pulling several GB that will sit unused.
            if self.skip_chat_model:
                model_ids = [m for m in model_ids if is_embedding_model_id(m)]

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

            # Download each model via LemonadeClient API.
            # NPU/FLM models (e.g. ``gemma4-it-e2b-FLM``) are built-in Lemonade
            # models — pull them by name only. Passing ``recipe`` makes Lemonade
            # treat the call as a *new* model registration, which requires the
            # ``user.`` prefix and 400s on built-in names (#1655). The recipe is
            # baked into the built-in model and applied at load time.
            #
            # Custom (``user.``-namespaced) models — e.g. the EmbeddingGemma
            # embedder — are NOT built-ins: they must be registered on first pull
            # with checkpoint + recipe + the embedding label. Look those up from
            # the model registry so the pull request is valid (#1745 auto-label bug
            # is avoided by passing ``embedding=True`` explicitly).
            from gaia.llm.lemonade_client import MODELS

            registry_by_id = {mr.model_id: mr for mr in MODELS.values()}

            recipe = profile_config.get("recipe")
            success = True
            for model_id in model_ids:
                self._print("")
                mr = registry_by_id.get(model_id)
                is_custom = model_id.startswith("user.")
                label = f"{model_id} (recipe={recipe})" if recipe else model_id
                self.agent_console.print(
                    f"   [bold cyan]Downloading:[/bold cyan] {label}"
                )
                # Built-in models are pulled by name only. Passing recipe (even
                # =None) can make Lemonade treat the call as a custom-model
                # registration, which 400s on built-in names (#1655). Only
                # user.-namespaced models carry checkpoint + recipe + the
                # embedding label.
                pull_kwargs = (
                    {
                        "checkpoint": mr.checkpoint,
                        "recipe": mr.recipe,
                        "embedding": mr.embedding,
                    }
                    if (mr and is_custom)
                    else {}
                )
                if client.ensure_model_downloaded(model_id, **pull_kwargs):
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
            if min_ctx and not self.skip_chat_model:
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
                    self._print_error(
                        f"Lemonade Server could not load a model with a {min_ctx} "
                        f"token context. {self._server_problem_hint()}"
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
            if self.profile != "sd" and not self.skip_chat_model:
                from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

                if DEFAULT_MODEL_NAME not in model_ids:
                    model_ids = list(model_ids) + [DEFAULT_MODEL_NAME]

            if self.skip_chat_model:
                model_ids = [m for m in model_ids if is_embedding_model_id(m)]

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

            try:
                for model_id in model_ids:
                    # Check if model is available first
                    if not client.check_model_available(model_id):
                        self.console.print(
                            f"   [yellow]⏭️[/yellow]  [cyan]{model_id}[/cyan] [dim]- not downloaded[/dim]"
                        )
                        continue

                    # Reset per-model context state. _test_model_inference
                    # sets _ctx_verified only for LLM models that declare a min
                    # context size; SD/embedding models leave verification N/A
                    # and must not inherit a stale "unverified" flag from
                    # __init__ or a prior model.
                    if hasattr(self, "_ctx_verified"):
                        delattr(self, "_ctx_verified")
                    self._ctx_warning = None

                    # Test the model
                    success, error = self._test_model_inference(client, model_id)
                    if success:
                        # Show context only when verification was attempted
                        # (LLM models with a min_ctx requirement).
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
                # Ctrl-C means stop, not "skip the rest and declare success" --
                # propagate to run()'s own KeyboardInterrupt handler.
                raise

            # Summary
            total = len(model_ids)
            self.console.print()
            if models_failed:
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
                            # PowerShell is GAIA's assumed Windows shell; cmd's
                            # `rmdir /s /q` is not valid PowerShell syntax.
                            self.console.print(
                                f'       [yellow]Remove-Item -Recurse -Force[/yellow] [cyan]"{model_path}"[/cyan]'
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

    @staticmethod
    def _is_hub_agent_available(agent_id: str) -> bool:
        """Whether this profile's hub agent is already present.

        Two probes, because the hub ships two artifact shapes and only one of
        them is importable. A wheel agent (``chat``) is there when
        ``gaia_agent_<id>`` imports -- the naming convention
        ``install_hints._AGENT_SOURCE_SUBDIRS`` and every ``gaia-agent-*``
        wheel share, with the flagship's ``gaia_agent`` spelling read from
        ``_AGENT_IMPORT_NAMES``. The flagship itself publishes a native
        BINARY, which no import can ever see, so its install sentinel under
        ``~/.gaia/agents`` is the only evidence it is there. Probing the
        module alone would re-download it from the hub on every run and
        report a finished setup as "incomplete".
        """
        module = _AGENT_IMPORT_NAMES.get(agent_id, f"gaia_agent_{agent_id}")
        if importlib.util.find_spec(module) is not None:
            return True

        # Function-local: gaia.hub.installer imports gaia.agents.registry at
        # module level, so a top-level import here risks the same circular
        # partial-init AgentRegistry defers this import to avoid.
        from gaia.hub import installer as hub_installer

        return hub_installer.read_sentinel(agent_id) is not None

    @staticmethod
    def _chat_agent_available() -> bool:
        """Whether the standalone gaia-agent-chat wheel is importable.

        ``gaia chat`` resolves through that wheel (#1102), which no init
        profile installs (it isn't a pip extra -- #2240). Printing `gaia
        chat` as a ready next step when it isn't installed is a false
        promise, so completion messaging checks first. Delegates to
        ``_is_hub_agent_available``, so a hub install counts too.
        """
        return InitCommand._is_hub_agent_available("chat")

    def _profile_agent_available(self) -> bool:
        """Whether the hub agent THIS profile installs is present.

        True for profiles that install none (sd/vlm/minimal/...), so their
        completion headline is never gated on someone else's agent.
        """
        agent_id = INIT_PROFILES[self.profile].get("agent")
        if agent_id not in HUB_INSTALL_AGENTS:
            return True
        return self._is_hub_agent_available(agent_id)

    def _ensure_hub_agent_installed(self) -> None:
        """Install this profile's hub agent from the Agent Hub catalog if it
        isn't already available and the live catalog confirms it's published.

        Scoped by ``run()``'s ``has_hub_agent_check`` (profiles whose declared
        ``"agent"`` is in ``HUB_INSTALL_AGENTS`` -- ``gaia``, plus ``chat`` and
        ``npu``, which both declare ``"agent": "chat"``).

        Distinguishes two catalog states (#2358):

        * Not yet published (today's state — only ``email`` is live on the
          Hub): NOT an error. A blind "call install() and fail loud" would
          turn today's soft success (``init`` completes, prints a
          source-install hint) into a hard failure for every `gaia init
          --profile chat` until the publish lands — a real regression this
          method must not introduce. Silently returns; the existing
          ``_print_completion()`` hint already tells the user how to
          source-install it in the meantime.
        * Published but the install itself genuinely fails: this method
          does NOT catch that exception — it propagates into ``run()``'s
          own top-level ``except Exception`` handler, which already turns
          any unexpected exception into an actionable non-zero exit. Unlike
          ``_install_pip_extras``, a real hub-install failure must fail
          loudly, not warn-and-continue (that would just recreate the
          "agent isn't installed" state this issue exists to close).

        A catalog-fetch failure (network down, no offline cache) is treated
        the same as "not yet published" — `gaia init` must not hard-fail
        merely because the Hub catalog service is briefly unreachable.
        """
        agent_id = INIT_PROFILES[self.profile]["agent"]

        if self._is_hub_agent_available(agent_id):
            return

        from gaia.hub import catalog as hub_catalog

        try:
            catalog_result = hub_catalog.load_index()
            published = any(
                agent.get("id") == agent_id for agent in catalog_result.agents
            )
        except Exception as exc:  # noqa: BLE001 - catalog reachability, not install
            log.warning(
                "Could not check the Agent Hub catalog for '%s': %s -- "
                "treating as not-yet-published (non-fatal)",
                agent_id,
                exc,
            )
            published = False

        if not published:
            log.debug(
                "'%s' is not yet published to the Agent Hub catalog; "
                "skipping the hub install for this run.",
                agent_id,
            )
            return

        from gaia.hub import installer as hub_installer

        self._print(f"   Installing '{agent_id}' from the Agent Hub...")
        # Curated first-run profile agent: a hardcoded INIT_PROFILES id, not user
        # input, so GAIA's own curation is the trust decision. Pass the trust
        # opt-in explicitly — every non-verified agent now needs it, and the
        # profile agents are not published in the "verified" tier.
        try:
            result = hub_installer.install(agent_id, trusted=True)
        except (
            hub_installer.UnsupportedPlatformError,
            hub_installer.CompatibilityError,
        ) as exc:
            # "The hub has no build for THIS machine" is the same situation as
            # "not published yet", and must not fail a run that already installed
            # Lemonade and several GB of models. The flagship ships a native
            # binary, so it is platform-gated: without this the DEFAULT profile
            # exits non-zero on Intel Mac, Windows-on-ARM and ARM Linux —
            # machines where `gaia init` works today.
            #
            # Deliberately narrow. Every OTHER InstallError still propagates
            # into run()'s handler and exits non-zero (#2358): an install that
            # was attempted and failed must stay loud.
            self._print_warning(
                f"'{agent_id}' has no Agent Hub build for this machine: {exc}"
            )
            self._print_warning(
                f"Everything else is set up. Install it with "
                f"`gaia hub install {agent_id}` once a build is available."
            )
            return
        self._print_success(f"Installed '{agent_id}' from the Agent Hub")

        # No AgentRegistry exists in this process to hot-register into (we
        # deliberately don't construct one just for this), so mirror the
        # sys.path side of _hot_register directly: without this, THIS same
        # process's own _print_completion() would still (incorrectly) show
        # the "chat agent not installed yet" hint immediately after a
        # successful install, since installer.install() only mutates
        # sys.path when a registry= is passed. isinstance-guarded (rather
        # than a bare truthiness/attribute check) so a test double standing
        # in for InstallResult can't accidentally make it past this into a
        # real sys.path mutation.
        if isinstance(result.path, Path):
            site_packages = result.path / hub_installer.SITE_PACKAGES_DIRNAME
            if site_packages.is_dir():
                sp = str(site_packages)
                if sp not in sys.path:
                    sys.path.append(sp)
                    importlib.invalidate_caches()

    def _print_completion(self):
        """Print completion message with next steps."""
        chat_agent_available = self._chat_agent_available()
        chat_install_note = (
            "Chat agent not installed yet -- run: "
            f"{source_install_command('gaia-agent-chat')}"
        )
        # The flagship is a hub BINARY, so its missing-hint names the hub, not a
        # pip command. Pointing at gaia-agent-chat here would answer a headline
        # about the flagship with a different package's install line.
        flagship_install_note = (
            "GAIA agent not installed yet -- run: gaia hub install gaia"
        )
        # Scoped like run()'s has_hub_agent_check -- gating on the chat wheel
        # alone would mark sd/vlm/minimal permanently "incomplete", and would
        # call the flagship profile complete while its own wheel is missing.
        setup_incomplete = not self._profile_agent_available()
        headline = (
            "GAIA initialization incomplete - see below"
            if setup_incomplete
            else "GAIA initialization complete!"
        )
        if RICH_AVAILABLE and self.console:
            self.console.print()
            self.console.print(
                Panel(
                    f"[bold green]{headline}[/bold green]",
                    border_style="green",
                    padding=(0, 2),
                )
            )
            self.console.print()
            self.console.print("  [bold]Quick start commands:[/bold]")

            # Profile-specific quick start commands
            if self.profile == "sd":
                self.console.print(
                    "    [cyan]gaia chat[/cyan]                            "
                    "Then ask for an image — image generation runs through the "
                    "agent's SD tools"
                )
            elif self.profile == "gaia":
                self.console.print(
                    "    [cyan]gaia-tui[/cyan]                             Start the GAIA agent (terminal UI)"
                )
                self.console.print(
                    "    [cyan]gaia chat --ui[/cyan]                       Launch the Agent UI (browser-based)"
                )
                if not self._profile_agent_available():
                    self.console.print(f"    [yellow]{flagship_install_note}[/yellow]")
                if not chat_agent_available:
                    self.console.print(f"    [yellow]{chat_install_note}[/yellow]")
            elif self.profile == "chat":
                self.console.print(
                    "    [cyan]gaia chat[/cyan]                            Start interactive chat with RAG"
                )
                self.console.print(
                    "    [cyan]gaia chat --index report.pdf[/cyan]         Index a PDF for Q&A"
                )
                self.console.print(
                    "    [cyan]gaia chat --watch ./docs[/cyan]             Auto-index a folder of docs"
                )
                self.console.print(
                    "    [cyan]gaia chat --ui[/cyan]                       Launch the Agent UI (browser-based)"
                )
                if not chat_agent_available:
                    self.console.print(f"    [yellow]{chat_install_note}[/yellow]")
            elif self.profile == "npu":
                self.console.print(
                    "    [cyan]gaia chat --device npu[/cyan]             Chat using Ryzen AI NPU"
                )
                self.console.print(
                    "    [cyan]gaia chat --ui[/cyan]                     Agent UI (select NPU in device dropdown)"
                )
                self.console.print(
                    "    [dim]Note: NPU inference is active. Use --device gpu to switch back.[/dim]"
                )
                if not chat_agent_available:
                    self.console.print(f"    [yellow]{chat_install_note}[/yellow]")
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
                self.console.print("    [cyan]gaia init[/cyan]")
            else:
                # Default commands for other profiles
                self.console.print(
                    "    [cyan]gaia chat[/cyan]              Start interactive chat"
                )
                self.console.print(
                    "    [cyan]gaia chat --ui[/cyan]         Launch the Agent UI (browser-based)"
                )
                self.console.print(
                    "    [cyan]gaia llm 'Hello'[/cyan]       Quick LLM query"
                )
                self.console.print(
                    "    [cyan]gaia talk[/cyan]              Voice interaction"
                )
                if not chat_agent_available:
                    self.console.print(f"    [yellow]{chat_install_note}[/yellow]")
            self.console.print()
        else:
            self._print("")
            self._print("=" * 60)
            self._print(f"  {headline}")
            self._print("=" * 60)
            self._print("")
            self._print("  Quick start commands:")

            # Profile-specific quick start commands
            if self.profile == "sd":
                self._print(
                    "    gaia chat                    Then ask for an image — "
                    "image generation runs through the agent's SD tools"
                )
            elif self.profile == "gaia":
                self._print(
                    "    gaia-tui                             # Start the GAIA agent (terminal UI)"
                )
                self._print(
                    "    gaia chat --ui                       # Launch the Agent UI (browser-based)"
                )
                if not self._profile_agent_available():
                    self._print(f"    {flagship_install_note}")
                if not chat_agent_available:
                    self._print(f"    {chat_install_note}")
            elif self.profile == "chat":
                self._print(
                    "    gaia chat                            # Start interactive chat with RAG"
                )
                self._print(
                    "    gaia chat --index report.pdf         # Index a PDF for Q&A"
                )
                self._print(
                    "    gaia chat --watch ./docs             # Auto-index a folder of docs"
                )
                self._print(
                    "    gaia chat --ui                       # Launch the Agent UI (browser-based)"
                )
                if not chat_agent_available:
                    self._print(f"    {chat_install_note}")
            elif self.profile == "npu":
                self._print(
                    "    gaia chat --device npu             # Chat using Ryzen AI NPU"
                )
                self._print(
                    "    gaia chat --ui                     # Agent UI (select NPU in device dropdown)"
                )
                self._print("")
                self._print(
                    "  Note: NPU inference is active. Use --device gpu to switch back."
                )
                if not chat_agent_available:
                    self._print(f"    {chat_install_note}")
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
                self._print("    gaia init")
            else:
                # Default commands for other profiles
                self._print("    gaia chat              # Start interactive chat")
                self._print(
                    "    gaia chat --ui         # Launch the Agent UI (browser-based)"
                )
                self._print("    gaia llm 'Hello'       # Quick LLM query")
                self._print("    gaia talk              # Voice interaction")
                if not chat_agent_available:
                    self._print(f"    {chat_install_note}")
            self._print("")


def run_init(
    profile: str = DEFAULT_INIT_PROFILE,
    skip_models: bool = False,
    force_reinstall: bool = False,
    force_models: bool = False,
    yes: bool = False,
    verbose: bool = False,
    remote: bool = False,
    skip_webui_build: bool = False,
    skip_chat_model: bool = False,
) -> int:
    """
    Entry point for `gaia init` command.

    Args:
        profile: Profile to initialize (minimal, chat, rag, all)
        skip_models: Skip model downloads
        force_reinstall: Reinstall GAIA's embedded Lemonade Server
        force_models: Force re-download models (deletes then re-downloads)
        yes: Skip confirmation prompts
        verbose: Enable verbose output
        remote: Use the Lemonade Server LEMONADE_BASE_URL names
        skip_webui_build: Skip the Agent UI frontend build step entirely
        skip_chat_model: Skip the profile's chat LLM, keep any embedding model
            (see InitCommand's docstring — for a Claude-backed session)

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
            remote=remote,
            skip_webui_build=skip_webui_build,
            skip_chat_model=skip_chat_model,
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
