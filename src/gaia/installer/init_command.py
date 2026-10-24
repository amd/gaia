# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA Init Command

Main entry point for `gaia init` command that:
1. Installs and starts GAIA's embedded Lemonade Server (or checks the one
   LEMONADE_BASE_URL names)
2. Downloads required models for the selected profile
3. Verifies setup is working

The server starts without a context size; the verify step loads each model at
the profile's min_context_size, which is where that requirement is enforced.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional
from urllib.parse import urlparse

# Rich imports for better CLI formatting
try:
    from rich.console import Console
    from rich.markup import escape as rich_escape
    from rich.panel import Panel

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from gaia.agents.base.console import AgentConsole
from gaia.agents.install_hints import (
    PackageInstallerUnavailableError,
    format_command,
    gaia_extra_requirements,
    resolve_pip_frontend,
)
from gaia.installer._stdin import stdin_is_tty
from gaia.llm.model_fit import MachineCapacity, format_gb
from gaia.logger import get_logger
from gaia.ui.build import WebuiBuildStatus
from gaia.version import LEMONADE_MIN_VERSION

log = get_logger(__name__)

# The terminal UI ships only with the GAIA installer, never with the wheel.
_INSTALLER_HINT = (
    "irm https://amd-gaia.ai/install.ps1 | iex"
    if sys.platform == "win32"
    else "curl -fsSL https://amd-gaia.ai/install.sh | sh"
)


def is_embedding_model_id(model_id: str) -> bool:
    """Whether a model id names an embedding model rather than a chat LLM.

    Centralized so the Claude-backend skip (models required for RAG/memory
    embeddings only) and the existing verify/test-inference branches agree
    on the same rule instead of each re-deriving it.
    """
    return "embed" in model_id.lower()


_TROUBLESHOOTING_URL = "https://amd-gaia.ai/docs/reference/troubleshooting"


def hub_unreachable_message(
    agent_id: str, hub_url: str, exc: BaseException, retry: str = "gaia init"
) -> str:
    """One self-contained line: what failed, what to do, and where to look.

    One line because the Agent UI setup wizard shows the last error line as
    the reason setup failed.
    """
    from gaia.logger import log_manager

    cause = exc.__cause__ or exc
    if "CERTIFICATE_VERIFY_FAILED" in str(cause):
        remedy = (
            "Your network inspects HTTPS and Python does not trust its "
            "certificate: install your proxy's root certificate in the system "
            "certificate store, or ask IT to."
        )
    else:
        remedy = "Check your internet connection, proxy settings, or GAIA_HUB_URL."
    return (
        f"Could not install the '{agent_id}' agent: its download server at "
        f"{hub_url} could not be reached ({cause}). {remedy} Then run "
        f"`{retry}` again. Log: {log_manager.log_file}; help: "
        f"{_TROUBLESHOOTING_URL}"
    )


# Hub agent ids `gaia init` installs for its profile. Everything else
# (sd/vlm/email/...) owns its own install lifecycle; a generic "install the
# profile's agent" would hard-fail on agents the hub index doesn't carry.
#
# `chat` is NOT here: it is not a catalog agent, so there is nothing for the
# Hub to install. Its wheel is source-install only (#2240).
HUB_INSTALL_AGENTS = frozenset({"gaia"})

# The agent the Agent UI and gaia-tui talk to. Profiles that don't install it
# say so in their completion message and name `gaia init`, which installs it.
FLAGSHIP_AGENT_ID = "gaia"

# Hub agent id -> the module a source/pip install of it makes importable. Every
# `gaia-agent-<id>` distribution installs `gaia_agent_<id>` except the flagship:
# `gaia-agent-gaia` ships plain `gaia_agent` (hub/agents/gaia/python's
# gaia-agent.yaml `entry_module`).
_AGENT_IMPORT_NAMES = {"gaia": "gaia_agent"}

_NPU_ACTIVE_NOTE = (
    "NPU is now the default device. Switch back with: "
    "gaia config set default_device gpu"
)

# The profile a bare `gaia init` runs — the flagship agent's configuration.
DEFAULT_INIT_PROFILE = "gaia"

# Profile definitions mapping to agent profiles
# Note: These define which agent profile to use for each init profile
INIT_PROFILES = {
    "gaia": {
        "description": "The flagship GAIA agent — chat, documents, data, web, memory",
        # Installs the flagship from the Agent Hub, which publishes it as a
        # native binary (`gaia-agent`), not a wheel -- so nothing it installs is
        # importable. `gaia chat` still needs the `gaia-agent-chat` and
        # `gaia-agent-gaia` wheels separately; the completion message says so.
        "agent": "gaia",
        "models": ["Gemma-4-E4B-it-GGUF", "user.embeddinggemma-300m-GGUF"],
        "approx_size": "~6 GB",
        # EmbeddingGemma loads only on Lemonade v10.9.0+ (see the chat profile).
        "min_lemonade_version": "10.9.0",
        "min_context_size": 32768,
        # [ui] is [rag] plus the daemon, which gaia-tui, `gaia hub install`
        # and `gaia chat --ui` all need.
        "pip_extras": ["ui"],
    },
    "minimal": {
        "description": "Fast setup with Gemma 4 E4B multimodal model",
        "agent": "minimal",
        "models": ["Gemma-4-E4B-it-GGUF"],
        "approx_size": "~6 GB",
        "min_lemonade_version": LEMONADE_MIN_VERSION,
        "min_context_size": 32768,
        "pip_extras": [],
    },
    "sd": {
        "description": "Image generation with multi-modal AI (LLM + SD)",
        "agent": "sd",
        "models": [
            "SDXL-Turbo",  # Image generation (6.5GB)
            "Gemma-4-E4B-it-GGUF",  # Agentic reasoning + VLM + prompt enhancement (~6GB)
        ],
        "approx_size": "~10 GB",
        "min_lemonade_version": LEMONADE_MIN_VERSION,
        "min_context_size": 32768,
        "pip_extras": [],
    },
    "chat": {
        "description": "Interactive chat with RAG and vision support",
        "agent": "chat",
        "models": ["Gemma-4-E4B-it-GGUF", "user.embeddinggemma-300m-GGUF"],
        "approx_size": "~6 GB",
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
        "approx_size": "~6 GB",
        # EmbeddingGemma loads only on Lemonade v10.9.0+ (see chat profile).
        "min_lemonade_version": "10.9.0",
        "min_context_size": 32768,
        "pip_extras": ["rag"],
    },
    "vlm": {
        "description": "Vision pipeline for document and image extraction",
        "agent": "vlm",
        "models": ["Gemma-4-E4B-it-GGUF"],
        "approx_size": "~6 GB",
        "min_lemonade_version": LEMONADE_MIN_VERSION,
        "min_context_size": 32768,
        "pip_extras": [],
    },
    "email": {
        "description": "Email triage for Gmail/Outlook (local inference)",
        "agent": "email",
        "models": ["Gemma-4-E4B-it-GGUF"],
        "approx_size": "~6 GB",
        # Keep in lock-step with gaia_agent_email.version.MIN_LEMONADE_VERSION
        # and the email gaia-agent.yaml manifest (the GET /v1/email/init readiness
        # check reads the same minimum). A test asserts the three agree.
        "min_lemonade_version": LEMONADE_MIN_VERSION,
        "min_context_size": 32768,
        "pip_extras": [],
    },
    "npu": {
        "description": "Ryzen AI NPU acceleration via FLM backend (requires XDNA2 NPU)",
        # The flagship, from the Agent Hub like the `gaia` profile -- the Agent
        # UI lists no agent without it (#2972). Only the models differ.
        "agent": "gaia",
        # FLM chat model + FLM-native embedder so chat and embeddings stay
        # co-resident on the NPU backend. A GGUF embedder would run on Vulkan
        # and evict the FLM chat model every turn (#1744). Both are built-in
        # Lemonade *-FLM models, pulled by name only (no recipe — #1655).
        "models": ["gemma4-it-e2b-FLM", "embed-gemma-300m-FLM"],
        "approx_size": "~3 GB",
        "min_lemonade_version": LEMONADE_MIN_VERSION,
        # NPU context window. Matches GPU/CPU (32768) so the init report and
        # the runtime load path agree (issue #1745) — the prior 4096 pin made
        # `gaia init --profile npu` report 4096 while the loader requested
        # 32768. FLM at 32k is confirmed loading on a Ryzen AI 7 350 / 16 GB.
        "min_context_size": 32768,
        # Same as the gaia profile: the daemon and Agent UI the flagship runs in.
        "pip_extras": ["ui"],
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
    #: "setup" when `gaia init` still has work to do, "server" when the model
    #: server is installed but will not answer, "load" when everything is
    #: downloaded but a model would not load — which re-running setup cannot fix.
    stage: Optional[str] = None
    #: One ModelLoad per model the load check tried; empty unless ``load=True``.
    models: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "ready": self.ready,
            "stage": self.stage,
            "reasons": list(self.reasons),
            "models": [m.__dict__ for m in self.models],
        }


@dataclass
class ModelLoad:
    """One model the `--check --load` probe loaded, or failed to."""

    id: str
    role: str  # "chat" or "embedding"
    size_gb: Optional[float]
    loaded: bool
    error: Optional[str] = None
    #: Whether the server already has it; None when not checked.
    downloaded: Optional[bool] = None


@dataclass
class ChatModelChoice:
    """Which chat model this machine should run, and why."""

    model_id: str
    #: True when the user set ``default_model``; False when the hardware chose.
    user_set: bool
    #: ``(model_id, reason)`` for each larger default passed over.
    skipped: list
    #: What the machine was judged against; None when the user chose.
    capacity: Optional[MachineCapacity] = None


def resolve_init_chat_model(
    client, *, reset_corrupt: bool, enforce_fit: bool
) -> ChatModelChoice:
    """The chat model `gaia init` sets up: the user's ``default_model`` when it
    is a local Lemonade model, else the largest default that fits this machine
    (Qwen3.6 35B A3B wherever a GPU holds it, Gemma 4 E4B everywhere else).

    ``--check`` and ``run()`` both go through here so they can never disagree
    about what "set up" means on this machine. ``reset_corrupt`` is run()'s
    policy (it rewrites a corrupt config); the read-only check raises instead.
    ``enforce_fit`` refuses a user's model that cannot fit — only run(), which
    would download it, needs that; ``--check`` just reports it missing.
    """
    from gaia.config import GaiaConfig, GaiaConfigError
    from gaia.llm.lemonade_client import (
        cloud_model_provider,
        recommend_default_chat_model,
    )

    try:
        configured = GaiaConfig.load().default_model
    except GaiaConfigError as e:
        if not reset_corrupt:
            raise
        log.warning("Ignoring corrupt config; gaia init rewrites it: %s", e)
        configured = None
    # A Claude or cloud default has nothing to download; the local chat model
    # then follows the hardware, and the user's setting is left as it is.
    is_local = (
        configured
        and not configured.startswith("claude-")
        and not cloud_model_provider(configured)
    )
    if is_local:
        if enforce_fit:
            _refuse_if_it_does_not_fit(client, configured)
        return ChatModelChoice(model_id=configured, user_set=True, skipped=[])
    model_id, skipped, capacity = recommend_default_chat_model(client)
    return ChatModelChoice(
        model_id=model_id, user_set=False, skipped=skipped, capacity=capacity
    )


def _refuse_if_it_does_not_fit(client, model_id: str) -> None:
    """Raise when a user-chosen model would be downloaded but cannot fit.

    Setup must never download a model the PC cannot run. A model already on
    disk is not a download. A model whose size neither GAIA nor Lemonade knows
    cannot be judged here; Lemonade still refuses one that would not fit the
    disk.
    """
    from gaia.llm.lemonade_client import (
        _model_ids_match,
        find_model_requirement,
        kv_cache_for,
        lemonade_server_version,
    )
    from gaia.llm.model_fit import (
        ModelFitError,
        capacity_from_system_info,
        check_fit,
        check_server_supports,
    )

    size = None
    for entry in client.list_models(show_all=True).get("data", []):
        if _model_ids_match(entry.get("id"), model_id):
            if entry.get("downloaded"):
                return
            size = entry.get("size")
            break
    mr = find_model_requirement(model_id)
    size = size or (mr.size_gb if mr else None)
    if size:
        capacity = capacity_from_system_info(client.get_system_info(timeout=15))
        verdict = check_fit(
            float(size), capacity, kv_cache_for(mr, capacity) if mr else 0.0
        )
        if not verdict.fits:
            raise ModelFitError(
                f"default_model {model_id} will not fit this PC: {verdict.reason}. "
                "Choose a smaller one with `gaia config set default_model <id>` "
                "(`/provider` in the TUI lists what fits), then re-run `gaia init`."
            )
    if mr and mr.min_lemonade_version:
        supported = check_server_supports(
            mr.min_lemonade_version, lemonade_server_version(client)
        )
        if not supported.fits:
            raise ModelFitError(f"default_model {model_id} {supported.reason}.")


def pending_download_sizes(client, model_ids) -> dict:
    """GB each model still costs to download: 0.0 when already on disk, None
    when neither Lemonade's catalog nor GAIA's registry knows its size."""
    from gaia.llm.lemonade_client import _model_ids_match, find_model_requirement

    catalog = client.list_models(show_all=True).get("data", [])
    sizes = {}
    for model_id in model_ids:
        entry = next(
            (e for e in catalog if _model_ids_match(e.get("id"), model_id)), None
        )
        if entry and entry.get("downloaded"):
            sizes[model_id] = 0.0
            continue
        size = entry.get("size") if entry else None
        if not isinstance(size, (int, float)):
            mr = find_model_requirement(model_id)
            size = mr.size_gb if mr else None
        sizes[model_id] = float(size) if isinstance(size, (int, float)) else None
    return sizes


def _model_checkpoint(entry: Optional[dict], model_id: str) -> Optional[str]:
    from gaia.llm.lemonade_client import find_model_requirement

    entry = entry or {}
    checkpoint = entry.get("checkpoint") or (entry.get("checkpoints") or {}).get("main")
    if not checkpoint:
        mr = find_model_requirement(model_id)
        checkpoint = mr.checkpoint if mr else None
    return checkpoint if isinstance(checkpoint, str) and checkpoint else None


def _snapshot_bytes(snapshot: Path) -> Optional[int]:
    """Bytes in one snapshot: the files its download manifest lists, finished
    or ``.partial``. None when the manifest cannot be read."""
    manifest = snapshot / ".download_manifest.json"
    if not manifest.is_file():
        return sum(p.stat().st_size for p in snapshot.rglob("*.partial"))
    try:
        files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
        names = [f["name"] for f in files]
    except (OSError, ValueError, KeyError, TypeError) as e:
        log.warning("Cannot read Lemonade download manifest %s: %s", manifest, e)
        return None
    total = 0
    for name in names:
        for path in (snapshot / name, snapshot / f"{name}.partial"):
            if path.is_file():
                total += path.stat().st_size
    return total


def bytes_already_on_disk(client, model_ids, store_path: Optional[str]) -> dict:
    """Bytes of each model Lemonade's store already holds: finished files and
    the ``.partial`` ones an interrupted pull left. None for a model GAIA
    cannot look up: a store not on this PC, or no known checkpoint."""
    from gaia.llm.lemonade_client import _model_ids_match

    store = Path(store_path) if store_path else None
    if store is None or not store.is_dir():
        return {m: None for m in model_ids}
    catalog = client.list_models(show_all=True).get("data", [])
    found = {}
    for model_id in model_ids:
        entry = next(
            (e for e in catalog if _model_ids_match(e.get("id"), model_id)), None
        )
        checkpoint = _model_checkpoint(entry, model_id)
        if not checkpoint:
            found[model_id] = None
            continue
        repo = checkpoint.split(":", 1)[0]
        snapshots = store / ("models--" + repo.replace("/", "--")) / "snapshots"
        counted = (
            [_snapshot_bytes(s) for s in snapshots.iterdir() if s.is_dir()]
            if snapshots.is_dir()
            else []
        )
        found[model_id] = None if None in counted else sum(counted)
    return found


def _size_note(size_gb) -> str:
    if size_gb is None:
        return ""
    if size_gb == 0:
        return " (already downloaded)"
    return f" ({size_gb:.1f} GB)"


# Profiles whose chat model follows the hardware. ``minimal`` promises a small
# setup, and the vlm, email and sd agents pin Gemma themselves.
HARDWARE_CHAT_PROFILES = frozenset({"gaia", "chat", "rag", "all"})


def with_chat_model(profile: str, model_ids, resolve_chat) -> list:
    """``model_ids`` plus the chat model ``profile`` needs on this machine.

    ``resolve_chat`` returns a :class:`ChatModelChoice`, and is only called for
    hardware-chosen profiles, so the others never probe Lemonade. A chat model
    the hardware picked is added beside Gemma, which the image tools load by
    name. A ``default_model`` the user chose replaces Gemma: the image tools
    then download Gemma the first time they are used (:func:`skipped_gemma`).
    """
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, _model_ids_match

    ids = list(model_ids)
    if profile in ("sd", "npu"):
        return ids
    if profile not in HARDWARE_CHAT_PROFILES:
        chat = DEFAULT_MODEL_NAME
    else:
        choice = resolve_chat()
        chat = choice.model_id
        if choice.user_set and not _model_ids_match(chat, DEFAULT_MODEL_NAME):
            ids = [m for m in ids if not _model_ids_match(m, DEFAULT_MODEL_NAME)]
    if chat not in ids:
        ids.append(chat)
    return ids


def skipped_gemma(profile: str, model_ids) -> bool:
    """Whether *profile* lists Gemma but *model_ids* left it to download on use."""
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    return DEFAULT_MODEL_NAME in INIT_PROFILES[profile]["models"] and (
        DEFAULT_MODEL_NAME not in model_ids
    )


def configured_server_too_old(health: object, profile: str, url: str) -> Optional[str]:
    """Why a user-chosen server can't serve *profile*, or None if it can.

    Shared by `gaia init` and `gaia init --check` so they can never disagree.
    A server that advertises no parseable version is logged and allowed, since
    there is nothing to compare.
    """
    from gaia.agents.base.readiness import version_meets_min

    version = health.get("version") if isinstance(health, dict) else None
    minimum = INIT_PROFILES[profile].get("min_lemonade_version")
    verdict = version_meets_min(version, minimum)
    if verdict is None and minimum:
        log.warning(
            "Could not read a version from the Lemonade Server at %s (got %r); "
            "not checking it against the '%s' profile's minimum v%s",
            url,
            version,
            profile,
            minimum,
        )
    if verdict is False:
        return (
            f"Lemonade Server at {url} is v{version}; the '{profile}' profile "
            f"needs v{minimum} or newer"
        )
    return None


def check_setup_status(
    profile: str = DEFAULT_INIT_PROFILE,
    skip_chat_model: bool = False,
    remote: bool = False,
    load: bool = False,
    chat_model: Optional[str] = None,
) -> SetupStatus:
    """Check whether `gaia init --profile <profile>` still has work to do.

    Never installs, prompts, or downloads anything. A stopped GAIA Lemonade
    Server is started through the daemon, which every entry point does anyway,
    so a reboot does not read as "needs setup". Checks the SAME real state
    `run()` itself acts on (GAIA's Lemonade Server installed + running,
    required models present) so this can never
    disagree with what `gaia init` would actually do — the alternative, a
    marker file recording "setup ran once", goes stale the moment a model is
    deleted or Lemonade is uninstalled without GAIA's knowledge.

    Args:
        profile: Profile to check (gaia, minimal, chat, rag, all, ...)
        skip_chat_model: Match run()'s --skip-chat-model filtering (Claude
            backend): only the profile's embedding model(s) are required.
        remote: Check the server LEMONADE_BASE_URL names, which must be set.
            A configured URL is checked the same way without it.
        load: Once everything is downloaded, load each model the way the agent
            will — the chat model at its pinned ctx, the embedder with a
            one-word embedding. "Downloaded" is not "works": a model llama.cpp
            cannot load passes the presence check and fails on first use.
        chat_model: The local chat model the session will use, when it is not
            the profile default. It replaces the default in what ``load`` loads;
            setup cannot download it, so it never becomes a download step.

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
        configured_lemonade_url,
        resolve_lemonade_base_url,
    )

    profile_config = INIT_PROFILES[profile]
    configured = configured_lemonade_url()
    if remote and not configured:
        raise ValueError("--remote needs LEMONADE_BASE_URL set to the server to check.")

    if configured:
        base_url = resolve_lemonade_base_url(configured)
    else:
        from gaia.daemon.errors import DaemonError
        from gaia.llm.lemonade_embedded import EmbeddedLemonade
        from gaia.llm.lemonade_manager import LemonadeManager

        embedded = EmbeddedLemonade()
        try:
            LemonadeManager.start_embedded_if_stopped()
        except DaemonError as e:
            return SetupStatus(
                ready=False,
                reasons=[f"GAIA's Lemonade Server could not be started: {e}"],
                stage="server",
            )
        status = embedded.status()
        if status.unresponsive_pid:
            return SetupStatus(
                ready=False,
                reasons=[
                    f"GAIA's Lemonade Server (pid {status.unresponsive_pid}) "
                    "is running but not answering"
                ],
                stage="server",
            )
        if not status.installed:
            return SetupStatus(
                ready=False,
                reasons=["GAIA's Lemonade Server is not installed"],
                stage="setup",
            )
        if not status.running:
            return SetupStatus(
                ready=False,
                reasons=["GAIA's Lemonade Server is installed but not running"],
                stage="server",
            )
        if status.version != embedded.version:
            return SetupStatus(
                ready=False,
                reasons=[
                    f"GAIA's Lemonade Server is v{status.version}; this GAIA "
                    f"needs v{embedded.version}"
                ],
                stage="setup",
            )
        base_url = status.base_url

    client = LemonadeClient(base_url=base_url, verbose=False)
    try:
        health = client.health_check()
    except LemonadeClientError as e:
        return SetupStatus(
            ready=False,
            reasons=[f"Lemonade Server at {base_url} is not reachable: {e}"],
            stage="server",
        )
    if configured:
        too_old = configured_server_too_old(health, profile, base_url)
        if too_old:
            return SetupStatus(ready=False, reasons=[too_old], stage="server")

    if profile_config["models"]:
        model_ids = list(profile_config["models"])
    else:
        try:
            model_ids = client.get_required_models(profile_config["agent"])
        except LemonadeClientError as e:
            return SetupStatus(
                ready=False,
                reasons=[f"Could not list the models this profile needs: {e}"],
                stage="server",
            )

    if not skip_chat_model:
        model_ids = with_chat_model(
            profile,
            model_ids,
            lambda: resolve_init_chat_model(
                client, reset_corrupt=False, enforce_fit=False
            ),
        )

    if skip_chat_model:
        model_ids = [m for m in model_ids if is_embedding_model_id(m)]

    reasons = []
    downloaded: Dict[str, bool] = {}
    for model_id in model_ids:
        try:
            available = client.check_model_available(model_id)
        except LemonadeClientError as e:
            reasons.append(f"Could not check model '{model_id}': {e}")
            continue
        downloaded[model_id] = bool(available)
        if not available:
            reasons.append(f"Model '{model_id}' is not downloaded")

    if reasons:
        # Sizes let a caller say what the download will cost before it starts;
        # ``downloaded`` lets it leave out models the server already has.
        models = (
            [_describe_model(client, m, downloaded.get(m)) for m in model_ids]
            if load
            else []
        )
        return SetupStatus(ready=False, reasons=reasons, stage="setup", models=models)
    if not load:
        return SetupStatus(ready=True, reasons=[])

    if chat_model and not skip_chat_model and not is_embedding_model_id(chat_model):
        model_ids = [m for m in model_ids if is_embedding_model_id(m)] + [chat_model]
    models = [_load_model_once(client, model_id) for model_id in model_ids]
    failed = [m for m in models if not m.loaded]
    if failed:
        return SetupStatus(
            ready=False,
            reasons=[
                f"Model '{m.id}' is downloaded but would not load: {m.error}"
                for m in failed
            ],
            stage="load",
            models=models,
        )
    return SetupStatus(ready=True, reasons=[], models=models)


def _describe_model(
    client, model_id: str, downloaded: Optional[bool] = None
) -> "ModelLoad":
    """A model setup needs, its download size, and whether the server has it."""
    from gaia.llm.lemonade_client import LemonadeClientError

    role = "embedding" if is_embedding_model_id(model_id) else "chat"
    try:
        size_gb = client.get_model_info(model_id)["size_gb"]
    except LemonadeClientError as e:
        return ModelLoad(model_id, role, None, False, str(e), downloaded)
    return ModelLoad(model_id, role, size_gb, False, downloaded=downloaded)


def _load_model_once(client, model_id: str) -> "ModelLoad":
    """Load one model through the same call the agent's first turn makes."""
    from gaia.llm.lemonade_client import LemonadeClientError

    role = "embedding" if is_embedding_model_id(model_id) else "chat"
    size_gb = None
    try:
        size_gb = client.get_model_info(model_id)["size_gb"]
        if role == "embedding":
            response = client.embeddings(["ok"], model=model_id, timeout=120)
            data = response.get("data") if isinstance(response, dict) else None
            if not data or not data[0].get("embedding"):
                return ModelLoad(
                    model_id, role, size_gb, False, "it returned no embedding"
                )
        else:
            client._ensure_model_loaded(model_id)  # pylint: disable=protected-access
    except LemonadeClientError as e:
        return ModelLoad(model_id, role, size_gb, False, str(e))
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Reported as this model's failure, so the caller names it — never a
        # bare traceback that leaves the check unanswered.
        return ModelLoad(model_id, role, size_gb, False, f"{type(e).__name__}: {e}")
    return ModelLoad(model_id, role, size_gb, True)


class MemoryShortfall(str):
    """A verification failure from too little free memory, not a broken model."""


def _hf_hub_cache() -> str:
    """Where Lemonade keeps downloaded models: the Hugging Face hub cache."""
    if os.environ.get("HF_HUB_CACHE"):
        return os.environ["HF_HUB_CACHE"]
    if os.environ.get("HF_HOME"):
        return os.path.join(os.environ["HF_HOME"], "hub")
    return os.path.expanduser("~/.cache/huggingface/hub")


class _DownloadProgress:
    """Draws a model pull's streamed events as a live progress bar."""

    REDRAW_S = 0.2

    def __init__(self, console, model_id: str):
        self.console = console
        self.model_id = model_id
        self.events = 0
        self.reported_error = False
        self._last_bytes = 0
        self._last_time = time.monotonic()
        self._drawn_at = None
        self._speed_mbps = 0.0

    def __call__(self, event: dict) -> None:
        self.events += 1
        kind = event.get("event")
        if kind == "progress":
            done = event.get("bytes_downloaded", 0)
            total = event.get("bytes_total", 0)
            now = time.monotonic()
            # Lemonade streams thousands of events, many for 0-byte metadata.
            if not total or (
                self._drawn_at is not None and now - self._drawn_at < self.REDRAW_S
            ):
                return
            if now - self._last_time > 0.5 and done > self._last_bytes:
                self._speed_mbps = (
                    (done - self._last_bytes) / (now - self._last_time) / 1024**2
                )
                self._last_bytes, self._last_time = done, now
            self._drawn_at = now
            self.console.print_download_progress(
                percent=int(event.get("percent", 0)),
                bytes_downloaded=done,
                bytes_total=total,
                speed_mbps=self._speed_mbps,
            )
        elif kind == "complete" and self._drawn_at is not None:
            print()  # end the in-place progress line
        elif kind == "error":
            self.reported_error = True
            self.console.print_download_error(
                event.get("error", "unknown error"), self.model_id
            )


class InitEvents:
    """``gaia init --progress-json``: one JSON object per stdout line.

    The desktop installer reads these to show the model download live. Every
    object has an ``event`` key; human output still prints around them.
    """

    def __init__(self, enabled: bool = False, stream=None):
        self.enabled = enabled
        self._stream = stream
        #: Set while an in-place progress bar has not ended its line.
        self.line_open = False

    def emit(self, event: str, **fields) -> None:
        if not self.enabled:
            return
        stream = self._stream or sys.stdout
        start = "\n" if self.line_open else ""
        self.line_open = False
        stream.write(start + json.dumps({"event": event, **fields}) + "\n")
        stream.flush()


class _JsonDownloadProgress:
    """Reports a model pull's streamed events as ``progress`` JSON lines.

    Lemonade reports bytes per file, so a model's bytes are the files already
    finished plus the one in flight.
    """

    EMIT_S = 0.5
    SPEED_WINDOW_S = 2.0

    def __init__(
        self,
        console,
        events: InitEvents,
        model_id: str,
        index: int,
        count: int,
        planned_bytes: Optional[int] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.console = console
        self.emitter = events
        self.model_id = model_id
        self.index = index
        self.count = count
        self.planned_bytes = planned_bytes
        self.events = 0
        self.reported_error = False
        self.bytes_done = 0
        self.bytes_total = planned_bytes or 0
        self.speed_bps = 0.0
        self._clock = clock
        self._finished = 0
        self._file = None
        self._file_total = 0
        self._emitted_at = None
        self._window = None

    def __call__(self, event: dict) -> None:
        self.events += 1
        kind = event.get("event")
        if kind == "progress":
            self._tally(event)
            now = self._clock()
            if self._emitted_at is not None and now - self._emitted_at < self.EMIT_S:
                return
            self._emitted_at = now
            self._emit()
        elif kind == "error":
            self.reported_error = True
            message = str(event.get("error") or "unknown error")
            self.console.print_download_error(message, self.model_id)
            self.emitter.emit("error", model=self.model_id, message=message)

    def _tally(self, event: dict) -> None:
        name = event.get("file") or ""
        done = int(event.get("bytes_downloaded") or 0)
        if self._file is not None and name != self._file:
            self._finished += self._file_total
            self._file_total = 0
        self._file = name
        # Mid-file events can carry bytes_total 0, so keep the largest seen.
        self._file_total = max(
            self._file_total, int(event.get("bytes_total") or 0), done
        )
        self.bytes_done = self._finished + done
        total = int(event.get("total_download_size") or 0)
        self.bytes_total = max(
            total or self.planned_bytes or self._finished + self._file_total,
            self.bytes_done,
        )
        now = self._clock()
        # From the first event, so bytes a resumed pull already had don't count.
        if self._window is None:
            self._window = (now, self.bytes_done)
        started, start_bytes = self._window
        if now - started >= self.SPEED_WINDOW_S:
            self.speed_bps = max(0.0, (self.bytes_done - start_bytes) / (now - started))
            self._window = (now, self.bytes_done)

    def _emit(self) -> None:
        self.emitter.emit(
            "progress",
            model=self.model_id,
            index=self.index,
            count=self.count,
            bytes_done=self.bytes_done,
            bytes_total=self.bytes_total,
            speed_bps=round(self.speed_bps),
        )


def _gb_to_bytes(size_gb) -> Optional[int]:
    return int(size_gb * 1e9) if isinstance(size_gb, (int, float)) else None


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
        lemonade_url: Optional[str] = None,
        gaia_lemonade: bool = False,
        progress_json: bool = False,
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
            lemonade_url: Use the Lemonade Server the user already runs at
                this URL, and save that choice; GAIA installs no server.
            gaia_lemonade: Use GAIA's own Lemonade, and save that choice.
            progress_json: Also print step and download progress as JSON
                lines (see InitEvents), for the desktop installer.
        """
        if lemonade_url and gaia_lemonade:
            raise ValueError(
                "Choose one: --lemonade-url uses your Lemonade Server, "
                "--gaia-lemonade uses GAIA's own."
            )
        self.lemonade_url = lemonade_url
        self.gaia_lemonade = gaia_lemonade
        self._chose_now = False
        # GAIA's own was chosen; saved only once that server is running.
        self._gaia_choice_pending = False
        self.profile = profile.lower()
        # This machine's chat model, resolved once so download and verify agree.
        self._chat_choice: Optional[ChatModelChoice] = None
        # Set once that model is confirmed on disk; only then is it recorded.
        self._chat_model_ready = False
        # Cleared when setup's verify step fails; nothing unverified is
        # pinned as everyone's default.
        self._setup_verified = True
        self.skip_models = skip_models
        self.skip_webui_build = skip_webui_build
        self.force_reinstall = force_reinstall
        self.force_models = force_models
        self.yes = yes
        self.verbose = verbose
        self.remote = remote
        self.skip_chat_model = skip_chat_model
        self.progress_callback = progress_callback
        self.events = InitEvents(progress_json)

        # A configured server is someone else's to run; init only checks it.
        # An explicit choice is applied first, in run(), so a broken saved
        # setting can't stop the flag that fixes it.
        from gaia.llm.lemonade_client import configured_lemonade_url

        self._lemonade_base_url = (
            None if (lemonade_url or gaia_lemonade) else configured_lemonade_url()
        )
        if self.remote and not self._lemonade_base_url:
            raise ValueError(
                "--remote needs LEMONADE_BASE_URL set to the server to use, e.g. "
                "LEMONADE_BASE_URL=http://<host>:13305. Without it, drop --remote "
                "and `gaia init` sets up GAIA's own Lemonade Server."
            )
        if self._lemonade_base_url:
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
        # Set when the hub has no build of the profile's agent for this machine:
        # setup is otherwise done, and nothing the user runs can change that.
        self._no_hub_build_for_machine = False

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
        self.events.emit("step", step=step, total=total, message=message)
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
            self.events.line_open = True

    @staticmethod
    def _tui_installed() -> bool:
        """True if gaia-tui is on PATH or in GAIA's own bin dir."""
        if shutil.which("gaia-tui") is not None:
            return True
        from gaia.config import gaia_home

        name = "gaia-tui.exe" if sys.platform == "win32" else "gaia-tui"
        if (gaia_home() / "bin" / name).is_file():
            return True
        if sys.platform != "win32":
            return False
        install_dir = InitCommand._nsis_install_dir()
        return install_dir is not None and (install_dir / name).is_file()

    @staticmethod
    def _nsis_install_dir() -> Optional[Path]:
        """Where the Windows installer put gaia-tui.exe (installer/nsis/gaia.nsi)."""
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\AMD\GAIA", 0, winreg.KEY_READ
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "InstallDir")
            return Path(value)
        except FileNotFoundError:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if not local_app_data:
                return None
            # The installer's own default InstallDir.
            return Path(local_app_data) / "Programs" / "GAIA"

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
        install_args = gaia_extra_requirements(pip_extras)
        try:
            frontend = resolve_pip_frontend()
        except PackageInstallerUnavailableError as e:
            self._print_error(f"Could not install [{extras_str}] extras: {e}")
            return False

        argv = [*frontend.argv, *install_args]
        retry = format_command([*frontend.display, *install_args])
        self._print_success(f"Installing extras: {extras_str}")
        log.debug("Installing extras: %s", argv)
        try:
            result = subprocess.run(  # noqa: S603 - argv is constructed, not shell
                argv, capture_output=True, text=True, check=False, timeout=900
            )
        except subprocess.TimeoutExpired:
            self._print_error(
                f"Installing [{extras_str}] extras timed out after 15 minutes. "
                f"Check your network, then run `{retry}` and re-run `gaia init`."
            )
            return False
        except OSError as e:
            self._print_error(
                f"Could not run the package installer ({argv[0]}): {e}. "
                f"Fix it, then run `{retry}` and re-run `gaia init`."
            )
            return False

        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            tail = "\n".join(output.splitlines()[-20:]) or "(no output)"
            self._print_error(
                f"Installing [{extras_str}] extras failed (exit "
                f"{result.returncode}). The '{self.profile}' profile needs them.\n"
                f"{tail}\n"
                f"Fix the error above, then run `{retry}` and re-run `gaia init`."
            )
            return False

        self._print_success(f"Installed [{extras_str}] dependencies")
        return True

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
        # Data-driven scope (#2358): keyed off the declared agent, not a
        # hardcoded profile-name literal, so it never touches profiles for
        # agents outside the catalog (chat/sd/vlm/email/...), each of which
        # has its own, separately-owned install lifecycle.
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
            if not self._choose_lemonade():
                return 1
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
                if not self._install_pip_extras():
                    return 1

            # Ensure the profile's hub agent (chat's standalone wheel) is
            # installed (#2358). Independent of the pip-extras step above:
            # the hub install targets the isolated
            # ~/.gaia/agents/chat/site-packages dir, while [rag] extras
            # target the ACTIVE interpreter — one must not replace or block
            # the other. A genuine failure here is allowed to propagate into this
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
            verify_ok = self._verify_setup()
            self._setup_verified = verify_ok

            # Saved even when verification fails -- the models may be fine.
            # Persist profile choice to ~/.gaia/config.json
            try:
                from gaia.config import GaiaConfig

                def _apply_profile(config) -> None:
                    config.profile = self.profile
                    config.default_device = "npu" if self.profile == "npu" else "gpu"
                    self._record_chat_choice(config)

                # Read under the lock, so a default_model another process saved
                # during this long-running init survives. init is the recovery
                # path, so a corrupt file is reset rather than left in place.
                GaiaConfig.update(
                    _apply_profile,
                    on_unreadable=lambda e: self._print_warning(
                        f"Resetting unreadable config: {e}"
                    ),
                )
            except Exception as e:
                self._print_error(
                    f"Failed to save profile '{self.profile}' to "
                    f"~/.gaia/config.json: {e}. Setup otherwise completed, but "
                    "GAIA will fall back to its default profile until this is "
                    "fixed -- re-run `gaia init` or `gaia config set profile "
                    f"{self.profile}` once the cause is resolved."
                )
                return 1

            if not verify_ok:
                return 1

            # A hard Agent UI build failure means the profile's UI isn't
            # usable -- don't report plain success for it. Config persistence
            # above already ran, since it doesn't depend on the frontend
            # build. The build step above
            # already printed the actionable message via warn_fn; don't
            # repeat the full paragraph, just name the outcome.
            if webui_build_result is not None and webui_build_result.status in (
                WebuiBuildStatus.NODE_TOO_OLD,
                WebuiBuildStatus.BUILD_FAILED,
            ):
                self._print_error("Agent UI frontend build failed -- see above.")
                return 1

            return 0 if self._print_completion() else 1

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

    def _choose_lemonade(self) -> bool:
        """Settle which Lemonade this machine uses before anything is installed.

        An explicit ``--lemonade-url`` / ``--gaia-lemonade`` is saved as given.
        Otherwise, on a first setup (no choice saved, GAIA's own not installed,
        no ``LEMONADE_BASE_URL``), a new-enough Lemonade already answering on
        this machine is offered. ``--yes`` never adopts one on its own: it
        installs GAIA's as before and says how to switch.

        Returns:
            False after printing why the chosen server can't be used.
        """
        from gaia.llm import lemonade_choice as choice
        from gaia.llm.lemonade_client import configured_lemonade_url

        try:
            if self.gaia_lemonade:
                self._gaia_choice_pending = True
                self._print("   Using GAIA's own Lemonade Server.")
            elif self.lemonade_url:
                if not self._adopt_external(self.lemonade_url):
                    return False
            elif self._should_offer_existing():
                found = choice.detect_running_lemonade()
                if found is not None and not self._offer_existing(found):
                    return False
        except choice.LemonadeChoiceError as e:
            self._print_error(str(e))
            return False
        if not self._tell_daemon():
            return False
        # GAIA's own, chosen but not yet running, wins over a saved server;
        # LEMONADE_BASE_URL still wins over both.
        if self._gaia_choice_pending and choice.env_override_note() is None:
            self._lemonade_base_url = None
        else:
            self._lemonade_base_url = configured_lemonade_url()
        if self._lemonade_base_url:
            hostname = urlparse(self._lemonade_base_url).hostname or "localhost"
            if hostname not in ("localhost", "127.0.0.1", "::1"):
                self.remote = True
        return True

    def _tell_daemon(self) -> bool:
        """A running daemon must stop (or start) GAIA's own server to match."""
        from gaia.daemon.errors import DaemonError
        from gaia.llm.lemonade_choice import apply_to_daemon

        if not (self.lemonade_url or self._chose_now) or self._gaia_choice_pending:
            return True
        try:
            apply_to_daemon()
        except DaemonError as e:
            self._print_error(f"Saved, but the running GAIA daemon did not switch: {e}")
            return False
        return True

    def _should_offer_existing(self) -> bool:
        from gaia.llm.lemonade_choice import saved_mode
        from gaia.llm.lemonade_embedded import EmbeddedLemonade

        return (
            not self._lemonade_base_url
            and saved_mode() is None
            and not EmbeddedLemonade().is_installed()
        )

    def _offer_existing(self, found) -> bool:
        """Offer the Lemonade found running; save the answer either way."""
        from gaia.llm import lemonade_choice as choice

        if found.status == choice.TOO_OLD:
            self._print(f"   {found.message()}")
            self._print("   Setting up GAIA's own Lemonade Server instead.")
            return True
        label = (
            f"Lemonade Server v{found.version}"
            if found.version
            else "A Lemonade Server"
        )
        if self.yes:
            self._print(
                f"   {label} is already running at {found.url}. GAIA is setting "
                "up its own; to use yours instead, run: "
                f"gaia lemonade connect {found.url}"
            )
            return True
        self._print(f"   {label} is already running at {found.url}.")
        self._print(
            "   GAIA can use it instead of installing its own, so this PC keeps "
            "one server and one copy of each model."
        )
        self._chose_now = True
        if self._prompt_yes_no("Use your Lemonade Server?", default=True):
            return self._adopt_external(found.url)
        self._gaia_choice_pending = True
        self._print("   Setting up GAIA's own Lemonade Server.")
        return True

    def _adopt_external(self, url: str) -> bool:
        """Save the user's server as GAIA's, asking for its key if it needs one."""
        from gaia.llm import lemonade_choice as choice

        # GAIA's own exported key (env.sh) must never go to someone else's server.
        own = os.environ.get("GAIA_LEMONADE_EMBEDDED", "").strip() == "1"
        key = None if own else os.environ.get("LEMONADE_API_KEY", "").strip() or None
        result = choice.probe(url, key)
        if result.status == choice.NEEDS_KEY and not self.yes and stdin_is_tty():
            import getpass

            self._print(f"   {result.message()}")
            key = getpass.getpass("   API key: ").strip() or None
            result = choice.probe(url, key)
        if not result.usable:
            self._print_error(result.message())
            return False
        choice.use_external(result.url, key)
        self._print_success(
            f"Using your Lemonade Server v{result.version} at {result.url} "
            "(saved). GAIA will not install or start its own."
        )
        self._print(
            "   GAIA registers its embedding model there and loads its models "
            "there; it does not change the server's own settings. To go back: "
            "gaia config set lemonade_mode gaia"
        )
        return True

    @staticmethod
    def _uses_saved_server() -> bool:
        from gaia.llm.lemonade_choice import external_mode_active

        return external_mode_active()

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
        from gaia.llm.lemonade_choice import switch_back_hint
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
            self._print(f"   Start that server, or {switch_back_hint()}.")
            return False

        too_old = configured_server_too_old(health, self.profile, url)
        if too_old:
            self._print_error(f"{too_old}.")
            self._print(
                "   Upgrade it from https://lemonade-server.ai, or "
                f"{switch_back_hint()}."
            )
            return False

        version = health.get("version") if isinstance(health, dict) else None
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

            # is_installed() checks this version's own dist dir, so an older
            # binary left on disk after a version change does not count.
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
        if self._gaia_choice_pending:
            # Saved only now: until it runs, "GAIA's own" would name nothing.
            from gaia.llm.lemonade_choice import use_gaia

            use_gaia()
            self._gaia_choice_pending = False
            self._print_success("Saved: GAIA uses its own Lemonade Server.")
        self._hand_server_to_daemon()
        return True

    def _hand_server_to_daemon(self) -> None:
        """Make the GAIA daemon the owner of the server init just started.

        The daemon then stops it with ``gaia daemon stop`` or after the idle
        timeout, instead of it running until the next reboot. Init registers as
        a client so the idle timeout cannot fire during a long model download.
        """
        from gaia.daemon import client as daemon_client
        from gaia.daemon.errors import DaemonError
        from gaia.llm.lemonade_service import hold_lemonade_open

        try:
            daemon_client.ensure_lemonade()
            hold_lemonade_open("cli", "gaia init")
        except DaemonError as e:
            self._print_warning(
                "The GAIA daemon could not take over Lemonade Server, so it keeps "
                f"running until you run `gaia lemonade embedded stop`: {e}"
            )

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

        from gaia.llm.lemonade_client import (
            LemonadeClient,
            LemonadeClientError,
            split_backend_spec,
        )

        try:
            client = LemonadeClient(verbose=self.verbose)

            spec_recipe, backend_key = split_backend_spec(backend_spec)

            # Check if already installed via recipe status
            recipe_name = profile_config.get("recipe", spec_recipe)
            recipe_status = client.get_recipe_status(recipe_name)

            if recipe_status:
                backends = recipe_status.get("backends", {})
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

    def _record_chat_choice(self, config) -> None:
        """Save a hardware-picked chat model as ``default_model`` so every agent
        resolves to it. Never overwrites a model the user chose, and records
        nothing when the pick is the floor model every agent already defaults to
        or when setup did not download it (declined, or ``--skip-models``) or
        could not verify it.
        """
        from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

        choice = self._chat_choice
        if choice is None or choice.user_set or config.default_model:
            return
        # An undownloaded default would make every agent's first load fail.
        if not self._chat_model_ready:
            return
        if not self._setup_verified:
            return
        if choice.model_id != DEFAULT_MODEL_NAME:
            config.default_model = choice.model_id

    def _chat_model_choice(self, client) -> "ChatModelChoice":
        """This machine's :class:`ChatModelChoice`; says why on first resolution."""
        self._chat_model(client)
        return self._chat_choice

    def _chat_model(self, client) -> str:
        """This machine's chat model; says why on first resolution."""
        if self._chat_choice is None:
            choice = resolve_init_chat_model(
                client, reset_corrupt=True, enforce_fit=True
            )
            self._chat_choice = choice
            if choice.user_set:
                self._print(
                    f"   Chat model: {choice.model_id} (default_model in ~/.gaia/config.json)"
                )
            else:
                cap = choice.capacity
                where = (
                    f" — this PC has {format_gb(cap.memory_gb)} GB for models "
                    f"({cap.memory_source})"
                    if cap
                    else ""
                )
                self._print(f"   Chat model: {choice.model_id}{where}")
                for skipped_id, reason in choice.skipped:
                    self._print(f"   Not using {skipped_id}: {reason}")
            if self.skip_models:
                self._print(
                    "   Not downloaded (--skip-models). Run `gaia init` without it "
                    "to download, or pick a cloud model in the Agent UI."
                )
        return self._chat_choice.model_id

    def _download_models(self) -> bool:
        """
        Download models for the selected profile.

        Delegates to LemonadeClient.ensure_model_downloaded() which handles
        checking availability, downloading via API, and waiting for completion.
        Works for both local and remote Lemonade servers.

        Returns:
            True if all models downloaded, False on failure
        """
        if self.force_models and self._uses_saved_server():
            self._print_error(
                "--force-models deletes models, and GAIA doesn't delete models "
                "from your own Lemonade Server. Delete them there, then re-run "
                "`gaia init`."
            )
            return False
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

            # Include this machine's chat model for profiles that use llamacpp.
            # SD profile has its own LLM and doesn't need the default model.
            # NPU profile uses FLM models exclusively — don't append GGUF model.
            if not self.skip_chat_model:
                model_ids = with_chat_model(
                    self.profile, model_ids, lambda: self._chat_model_choice(client)
                )
                if skipped_gemma(self.profile, model_ids):
                    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

                    self._print(
                        f"   Not downloading {DEFAULT_MODEL_NAME}: your "
                        f"default_model is {self._chat_choice.model_id}. Questions "
                        "about images download it (~6 GB) the first time you ask one."
                    )

            # A Claude-backed session never calls the local chat LLM — only
            # RAG/memory/code-index embeddings still need Lemonade (Anthropic has
            # no embeddings API). Drop every non-embedding model rather than
            # pulling several GB that will sit unused.
            if self.skip_chat_model:
                model_ids = [m for m in model_ids if is_embedding_model_id(m)]

            if not model_ids:
                self._print_success("No models required for this profile")
                return True

            # Show which models will be ensured, and what they cost to download
            sizes = pending_download_sizes(client, model_ids)
            notes = {m: _size_note(sizes.get(m)) for m in model_ids}
            total_gb = sum(s for s in sizes.values() if s)
            if RICH_AVAILABLE and self.console:
                self.console.print(
                    f"   [bold]Ensuring {len(model_ids)} model(s) are downloaded:[/bold]"
                )
                for model_id in model_ids:
                    self.console.print(
                        f"   [cyan]•[/cyan] {model_id}[dim]{notes[model_id]}[/dim]"
                    )
            else:
                self._print(f"   Ensuring {len(model_ids)} model(s) are downloaded:")
                for model_id in model_ids:
                    self._print(f"   • {model_id}{notes[model_id]}")
            if total_gb:
                self._print(f"   Total download: about {total_gb:.1f} GB")
            self._print("")

            disk_free_gb = None
            if total_gb:
                from gaia.llm.lemonade_client import LemonadeClientError
                from gaia.llm.model_fit import (
                    check_download_fits_disk,
                    model_store_free_gb,
                )

                try:
                    system_info = client.get_system_info(timeout=15)
                    unread = ""
                except LemonadeClientError as e:
                    log.warning("Lemonade /system-info failed: %s", e)
                    system_info = {}
                    unread = f" ({e})"
                disk_free_gb, store = model_store_free_gb(system_info)
                if disk_free_gb is None:
                    self._print_warning(
                        "Lemonade did not report the free space in its model "
                        f"store{unread}, so GAIA could not check that "
                        f"{total_gb:.1f} GB fits."
                    )
                # An interrupted pull's bytes already take disk; count only the rest.
                pending = [m for m in model_ids if sizes.get(m)]
                on_disk = bytes_already_on_disk(client, pending, store)
                still_gb = sum(
                    max(0.0, sizes[m] - (on_disk[m] or 0) / 1e9) for m in pending
                )
                fits = check_download_fits_disk(still_gb, disk_free_gb, store)
                if not fits.fits:
                    if any(on_disk[m] is None for m in pending):
                        self._print_warning(
                            f"The disk may be too small: {fits.reason}. GAIA could "
                            "not read what an earlier download left there, so it "
                            "downloads anyway; Lemonade stops if the disk fills."
                        )
                    else:
                        message = f"Not enough disk space: {fits.reason}."
                        self._print_error(message)
                        self.events.emit("error", message=message)
                        return False
            self.events.emit(
                "plan",
                models=[
                    {
                        "id": m,
                        "bytes_total": _gb_to_bytes(sizes.get(m)),
                        "downloaded": sizes.get(m) == 0,
                    }
                    for m in model_ids
                ],
                bytes_total=_gb_to_bytes(total_gb) or 0,
                disk_free_bytes=_gb_to_bytes(disk_free_gb),
            )

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
            from gaia.llm.lemonade_client import find_model_requirement

            recipe = profile_config.get("recipe")
            success = True
            for index, model_id in enumerate(model_ids, start=1):
                self._print("")
                mr = find_model_requirement(model_id)
                label = f"{model_id} (recipe={recipe})" if recipe else model_id
                self.agent_console.print(
                    f"   [bold cyan]Downloading:[/bold cyan] {label}"
                )
                # Built-in models are pulled by name only. Passing recipe (even
                # =None) can make Lemonade treat the call as a custom-model
                # registration, which 400s on built-in names (#1655). Only
                # user.-namespaced models carry their registration fields.
                pull_kwargs = mr.pull_kwargs() if mr else {}
                # Two hours covers a few GB on any link; an 80 GB model needs
                # time proportional to its size (~5 MB/s floor).
                if mr and mr.size_gb:
                    pull_kwargs["timeout"] = max(7200, int(mr.size_gb * 200))
                if self.events.enabled:
                    progress = _JsonDownloadProgress(
                        self.agent_console,
                        self.events,
                        model_id,
                        index,
                        len(model_ids),
                        _gb_to_bytes(sizes.get(model_id)),
                    )
                else:
                    progress = _DownloadProgress(self.agent_console, model_id)
                if client.ensure_model_downloaded(
                    model_id, on_progress=progress, **pull_kwargs
                ):
                    if progress.events:
                        self._print_success(f"Downloaded {model_id}")
                    else:
                        self._print_success(f"Already downloaded: {model_id}")
                    self.events.emit(
                        "model_done", model=model_id, downloaded=bool(progress.events)
                    )
                    if self._chat_choice and model_id == self._chat_choice.model_id:
                        self._chat_model_ready = True
                else:
                    if not progress.reported_error:
                        self._print_error(f"Failed to download {model_id}")
                        self.events.emit(
                            "error",
                            model=model_id,
                            message=f"Failed to download {model_id}",
                        )
                    self._print(f"   {self._server_problem_hint()}")
                    success = False

            self.events.emit("done", ok=success)
            return success

        except Exception as e:
            self._print_error(f"Error downloading models: {e}")
            self.events.emit("error", message=f"Error downloading models: {e}")
            return False

    def _test_model_inference(self, client, model_id: str) -> tuple:
        """
        Test a model with a small inference request.

        Args:
            client: LemonadeClient instance
            model_id: Model ID to test

        Returns:
            Tuple of (success: bool, error_message: str or None). A load refused
            for free memory returns its message as a :class:`MemoryShortfall`.
        """
        from gaia.llm.lemonade_client import InsufficientMemoryError

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
                from gaia.llm.lemonade_client import (
                    PIN_UNLOAD_SETTLE_DEADLINE_S,
                    resolve_ctx_size,
                )

                min_ctx = resolve_ctx_size(
                    model=model_id, device="npu" if self.profile == "npu" else "gpu"
                )
                # A user's own server keeps its models and saved options; one
                # saved with "Use mine" is never unloaded either.
                users_server = bool(self._lemonade_base_url)
                # Unload just this model so its recipe_options are saved; the
                # embedder stays loaded instead of being reloaded after it.
                # Wait until it is gone, or the load's free-memory check would
                # count its memory as neither free nor its own.
                if not self._uses_saved_server() and client.check_model_loaded(
                    model_id
                ):
                    client.unload_model(model_id)
                    client._wait_model_state(
                        model_id,
                        present=False,
                        deadline_s=PIN_UNLOAD_SETTLE_DEADLINE_S,
                    )

                client.load_model(
                    model_id,
                    auto_download=False,
                    prompt=False,
                    ctx_size=min_ctx,
                    save_options=not users_server,
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
                        # Inference still runs, so warn rather than fail.
                        log.warning(
                            "Lemonade did not report ctx_size for %s after loading "
                            "it with ctx_size=%s; context is unverified",
                            model_id,
                            min_ctx,
                        )
                        self._ctx_verified = None
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
                # A thinking model spends a short budget on its reasoning; any
                # generated text proves the model loads and runs.
                if response and response.get("choices"):
                    message = response["choices"][0].get("message", {})
                    if message.get("content") or message.get("reasoning_content"):
                        return (True, None)
                    return (False, "Empty response")
                return (False, "Invalid response format")

        except InsufficientMemoryError as e:
            # Kept whole: the remedy (close apps, or another model) is in it.
            return (False, MemoryShortfall(str(e)))
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
            # Loading an undownloaded model makes Lemonade pull it, unannounced.
            if min_ctx and not self.skip_chat_model and not self.skip_models:
                from gaia.llm.lemonade_client import (
                    fit_ctx_to_free_memory,
                    resolve_ctx_size,
                )
                from gaia.llm.lemonade_manager import LemonadeManager

                # The window the chat model gets in the memory free right now.
                min_ctx = fit_ctx_to_free_memory(
                    self._chat_model(client),
                    resolve_ctx_size(device="npu" if self.profile == "npu" else "gpu"),
                    client.base_url,
                )

                self.console.print()
                self.console.print(
                    f"   [dim]Ensuring {min_ctx} token context for {self.profile} profile...[/dim]"
                )
                success = LemonadeManager.ensure_ready(
                    min_context_size=min_ctx,
                    quiet=True,
                    # The local model set up above, even when default_model is cloud.
                    model=self._chat_model(client),
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

            # Same chat model the download step set up (NPU and SD bring their own).
            if not self.skip_chat_model:
                model_ids = with_chat_model(
                    self.profile, model_ids, lambda: self._chat_model_choice(client)
                )

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
            models_ctx_unverified = []

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
                                models_ctx_unverified.append(model_id)

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
            short_of_memory = [
                (m, e) for m, e in models_failed if isinstance(e, MemoryShortfall)
            ]
            broken = [
                (m, e) for m, e in models_failed if not isinstance(e, MemoryShortfall)
            ]
            if models_failed:
                self._print_warning(f"Models verified: {models_passed}/{total} passed")
            if short_of_memory:
                names = ", ".join(m for m, _ in short_of_memory)
                self.console.print()
                self.console.print(
                    f"   [bold]Downloaded but not tested ({names}): this PC does "
                    "not have enough free memory right now.[/bold]"
                )
                self.console.print(
                    "   [dim]Or choose a cloud model or a smaller local model in "
                    "the Agent UI (Settings > Model).[/dim]"
                )
            if broken:
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
                hf_cache = _hf_hub_cache()
                for model_id, error in broken:
                    # Find actual model directory (may have org prefix like ggml-org/model-name)
                    # Search for directories containing the model name; the hub
                    # lowercases some repo names (models--unsloth--gemma-4-...).
                    model_name_part = model_id.split("/")[-1].lower()
                    matching_dirs = [
                        d
                        for d in Path(hf_cache).glob("models--*")
                        if model_name_part in d.name.lower()
                    ]

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
            if not models_failed:
                self._print_success(f"All {models_passed} model(s) verified")

            if models_ctx_unverified:
                self.console.print()
                self._print_warning(
                    "Lemonade did not report a context size for "
                    f"{', '.join(models_ctx_unverified)}, so GAIA cannot confirm "
                    "long prompts and documents fit. The model answers, but if "
                    "long chats or documents get truncated, update Lemonade "
                    f"Server and re-run `gaia init --profile {self.profile} --yes`."
                )

            if broken:
                self.console.print()
                failed_ids = ", ".join(m for m, _ in broken)
                self._print_error(
                    f"Model verification failed for: {failed_ids}. "
                    "If the error above looks transient (a timeout), re-run "
                    "`gaia init` first. Otherwise follow the steps above to "
                    "re-download them, then re-run `gaia init`."
                )
                return False
            if short_of_memory:
                self.console.print()
                self._print_error(
                    "Not enough free memory to test "
                    f"{', '.join(m for m, _ in short_of_memory)}. Close "
                    "memory-heavy apps and re-run `gaia init`, or choose another "
                    "model; nothing needs re-downloading."
                )
                return False

            return True

        except Exception as e:
            self._print_error(f"Verification failed: {e}")
            return False

    @staticmethod
    def _is_hub_agent_available(agent_id: str) -> bool:
        """Whether this profile's hub agent is already present.

        Two probes, because an agent ships in two artifact shapes and only one
        of them is importable. A wheel agent (``chat``) is there when
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

    def _profile_installs_flagship(self) -> bool:
        """Whether this profile installs the flagship agent itself."""
        return INIT_PROFILES[self.profile].get("agent") == FLAGSHIP_AGENT_ID

    def _flagship_install_command(self) -> str:
        """The init run that installs the flagship and keeps this profile's models.

        A bare `gaia init` would switch an `npu` machine to the default
        profile's models, so a profile that installs the flagship names itself.
        """
        if (
            self.profile == DEFAULT_INIT_PROFILE
            or not self._profile_installs_flagship()
        ):
            return "gaia init"
        return f"gaia init --profile {self.profile}"

    def _agent_install_note(self) -> Optional[str]:
        """What to tell the user when no chat agent is installed, else None.

        Every profile gets the same supported remedy, `gaia init`, which
        installs the flagship when it is missing: the Agent UI and gaia-tui list
        no agent without it (#2972).
        """
        if self._is_hub_agent_available(FLAGSHIP_AGENT_ID):
            return None
        if self._profile_installs_flagship():
            return (
                "GAIA agent not installed yet -- run: "
                f"{self._flagship_install_command()}"
            )
        return (
            f"No chat agent is installed: the '{self.profile}' profile sets up "
            "models only. Install the GAIA agent with: gaia init"
        )

    def _ensure_hub_agent_installed(self) -> None:
        """Install this profile's hub agent from the Agent Hub catalog if it
        isn't already available.

        Scoped by ``run()``'s ``has_hub_agent_check`` (profiles whose declared
        ``"agent"`` is in ``HUB_INSTALL_AGENTS``).

        * Catalog unreachable, or the agent not listed in it: prints an error
          naming the hub URL, the cause and the retry command, and returns.
          The rest of setup still runs so the model downloads are not wasted,
          but the agent stays missing, so ``_print_completion()`` reports the
          setup incomplete and ``run()`` exits non-zero.
        * Published but the install itself genuinely fails: the exception
          propagates into ``run()``'s top-level handler and exits non-zero.
        * No build for this machine: a warning, and setup still succeeds --
          see ``_no_hub_build_for_machine``.
        """
        agent_id = INIT_PROFILES[self.profile]["agent"]

        from gaia.hub import catalog as hub_catalog
        from gaia.hub import prerelease

        # A release candidate's flagship comes from its own pre-release. A
        # broken pre-release raises here: init must not finish "successfully"
        # with an agent that predates the candidate.
        rc_source = prerelease.flagship_source(
            agent_id, fetcher=hub_catalog.fetch_bytes
        )
        if rc_source is not None:
            self._ensure_release_candidate_flagship(agent_id, rc_source)
            return

        if self._is_hub_agent_available(agent_id):
            self._print_success(f"{agent_id} agent is installed")
            return

        hub_url = hub_catalog.get_hub_base_url()
        try:
            catalog_result = hub_catalog.load_index()
        except Exception as exc:  # noqa: BLE001 - reported below, never swallowed
            log.debug("Agent Hub catalog fetch failed", exc_info=True)
            self._print_error(
                hub_unreachable_message(
                    agent_id, hub_url, exc, retry=self._flagship_install_command()
                )
            )
            return

        if not any(agent.get("id") == agent_id for agent in catalog_result.agents):
            self._print_error(
                f"The download server at {hub_url} does not list the "
                f"'{agent_id}' agent, so it was not installed. Check GAIA_HUB_URL, "
                f"then run `{self._flagship_install_command()}` again."
            )
            return

        from gaia.hub import installer as hub_installer

        self._print(f"   Installing the '{agent_id}' agent...")
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
            self._no_hub_build_for_machine = True
            self._print_warning(
                f"The '{agent_id}' agent has no build for this machine: {exc}"
            )
            self._print_warning(
                f"Everything else is set up. Run "
                f"`{self._flagship_install_command()}` again once a build is "
                f"available."
            )
            return
        self._print_success(f"Installed the '{agent_id}' agent")

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

    def _ensure_release_candidate_flagship(self, agent_id: str, rc_source) -> None:
        """Install the release candidate's own flagship unless it already is.

        "Already installed" means the same bytes, not the same id: an agent
        installed from the hub, or by an earlier candidate, is replaced.
        """
        from gaia.hub import installer as hub_installer
        from gaia.hub.compatibility import current_platform_key

        want = rc_source.sha256_for(current_platform_key())
        installed = hub_installer.read_sentinel(agent_id)
        if installed is not None and want and installed.artifact_sha256 == want:
            self._print_success(f"{agent_id} agent from {rc_source.tag} is installed")
            return
        if installed is not None:
            self._print(
                f"   Replacing the installed '{agent_id}' agent ({installed.version}) "
                f"with the one built for {rc_source.tag}..."
            )
        else:
            self._print(
                f"   Installing '{agent_id}' from release candidate {rc_source.tag}..."
            )
        try:
            hub_installer.install(agent_id, trusted=True)
        except (
            hub_installer.UnsupportedPlatformError,
            hub_installer.CompatibilityError,
        ) as exc:
            if installed is None:
                self._no_hub_build_for_machine = True
            self._print_warning(
                f"Release candidate {rc_source.tag} has no '{agent_id}' agent build "
                f"for this machine: {exc}"
            )
            if installed is not None:
                self._print_warning(
                    f"The installed '{agent_id}' agent ({installed.version}) is not "
                    f"this candidate's; results from it do not reflect "
                    f"{rc_source.tag}."
                )
            return
        self._print_success(f"Installed '{agent_id}' from {rc_source.tag}")

    def _print_completion(self) -> bool:
        """Print completion message with next steps.

        Returns False when the headline says setup is incomplete, which
        ``run()`` turns into a non-zero exit.
        """
        # The flagship is a downloaded BINARY, so the remedy is `gaia init`,
        # never a pip install from a git checkout.
        agent_note = self._agent_install_note()
        has_tui = self._tui_installed()
        tui_install_note = (
            f"Terminal UI (gaia-tui) ships with the installer -- run: {_INSTALLER_HINT}"
        )
        installs_flagship = self._profile_installs_flagship()
        agent_missing = agent_note is not None
        # Incomplete only when init was meant to install the agent and could have.
        setup_incomplete = (
            agent_missing and installs_flagship and not self._no_hub_build_for_machine
        )
        if setup_incomplete:
            headline = "GAIA initialization incomplete - see below"
        elif agent_missing and installs_flagship:
            headline = (
                f"GAIA initialization complete, except the '{FLAGSHIP_AGENT_ID}' "
                "agent: it has no build for this machine yet"
            )
        elif agent_missing:
            headline = "GAIA initialization complete - no chat agent installed"
        else:
            headline = "GAIA initialization complete!"
        color = "red" if setup_incomplete else "green"
        if RICH_AVAILABLE and self.console:
            self.console.print()
            self.console.print(
                Panel(
                    f"[bold {color}]{headline}[/bold {color}]",
                    border_style=color,
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
            elif self.profile in ("gaia", "npu"):
                if has_tui:
                    self.console.print(
                        "    [cyan]gaia-tui[/cyan]                             Start the GAIA agent (terminal UI)"
                    )
                self.console.print(
                    "    [cyan]gaia chat --ui[/cyan]                       Launch the Agent UI (browser-based)"
                )
                if not has_tui:
                    self.console.print(
                        f"    [dim]{rich_escape(tui_install_note)}[/dim]"
                    )
                if self.profile == "npu":
                    self.console.print(
                        f"    [dim]{rich_escape(_NPU_ACTIVE_NOTE)}[/dim]"
                    )
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
            if agent_note:
                self.console.print(f"    [yellow]{rich_escape(agent_note)}[/yellow]")
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
            elif self.profile in ("gaia", "npu"):
                if has_tui:
                    self._print(
                        "    gaia-tui                             # Start the GAIA agent (terminal UI)"
                    )
                self._print(
                    "    gaia chat --ui                       # Launch the Agent UI (browser-based)"
                )
                if not has_tui:
                    self._print(f"    {tui_install_note}")
                if self.profile == "npu":
                    self._print(f"    {_NPU_ACTIVE_NOTE}")
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
            if agent_note:
                self._print(f"    {agent_note}")
            self._print("")
        return not setup_incomplete


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
    lemonade_url: Optional[str] = None,
    gaia_lemonade: bool = False,
    progress_json: bool = False,
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
        lemonade_url: Use (and save) the Lemonade Server the user runs here
        gaia_lemonade: Use (and save) GAIA's own Lemonade Server
        progress_json: Also print progress as JSON lines (see InitEvents)

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
            lemonade_url=lemonade_url,
            gaia_lemonade=gaia_lemonade,
            progress_json=progress_json,
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
