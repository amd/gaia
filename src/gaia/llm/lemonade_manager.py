# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Lazy Lemonade Server Manager for GAIA.

Provides singleton initialization shared by CLI and SDK flows.
Operates at the LLM level (not agent level) for flexibility with community agents.
"""

import os
import sys
import threading
import time
from enum import Enum
from typing import Any, Dict, Iterator, Optional, Tuple

from gaia.llm.lemonade_client import (
    DEFAULT_CONTEXT_SIZE,
    DEFAULT_MODEL_NAME,
    LemonadeClient,
    LemonadeClientError,
)
from gaia.logger import get_logger

# Allow-list mapping from detected device -> Lemonade recipe
# TODO: Confirm full recipe vocabulary with the Lemonade specialist
# (@kovtcharov-amd). Currently we map hybrid-capable devices to
# 'oga-hybrid'. Before wiring recipe -> startup (dispatch), verify whether
# device-specific recipes exist (e.g., 'oga-npu', 'oga-dgpu') and update
# this allow-list accordingly.
_RECIPE_BY_DEVICE = {
    "amd_npu": "oga-hybrid",
    "amd_igpu": "oga-hybrid",
    "amd_dgpu": "oga-hybrid",
    "cpu": "oga-cpu",
}

# Device capability priority (high -> low)
_DEVICE_PRIORITY = ["amd_npu", "amd_igpu", "amd_dgpu", "cpu"]

# Map the high-level device selector ('cpu'/'gpu'/'npu') exposed to users
# (Agent UI dropdown, CLI ``--device``) to the minimum detected-device tier
# required to satisfy it.  A GPU requirement is met by EITHER integrated or
# discrete Radeon graphics, so it maps to the lowest GPU tier (``amd_dgpu``):
# ``_DEVICE_PRIORITY`` orders igpu above dgpu and the satisfaction check is
# ``detected_idx <= req_idx``, so requiring ``amd_dgpu`` accepts an iGPU-only
# box too.  Mapping ``gpu`` to ``amd_igpu`` (the old value) would wrongly
# reject a discrete-Radeon-only host.
_DEVICE_TO_MIN = {
    "cpu": "cpu",
    "gpu": "amd_dgpu",
    "npu": "amd_npu",
}

# User-facing remedy for each device when the host can't satisfy it.
_DEVICE_REMEDY = {
    "npu": (
        "NPU not available on this host; run `gaia init --profile npu` to set "
        "up NPU acceleration, or choose --device gpu"
    ),
    "gpu": (
        "No AMD GPU available on this host; run `gaia init` to set up GPU "
        "acceleration, or choose --device cpu"
    ),
    "cpu": "CPU is unavailable on this host",
}


def _device_is_available(info) -> bool:
    """Whether a ``get_system_info()`` device entry counts as usable hardware.

    The contract of ``get_system_info()`` is that availability lives in the
    per-device ``available`` boolean, so a present-but-``available: false``
    entry (e.g. an NPU listed but disabled/unpowered) is NOT detected. A
    missing ``available`` flag is treated as available for backwards
    compatibility with sysinfo payloads that omit it.
    """
    if isinstance(info, dict):
        return bool(info.get("available", True))
    if isinstance(info, list):
        # amd_dgpu can be a list of GPUs — detected if any entry is available.
        return any(_device_is_available(x) for x in info)
    return True


def _is_gpu_device_key(key: str) -> bool:
    """Whether a ``devices`` key names a GPU accelerator.

    Matches any key containing ``"gpu"`` (``amd_gpu``/``nvidia_gpu`` and the
    legacy ``amd_igpu``/``amd_dgpu``) plus ``"metal"`` — Apple Silicon reports
    its GPU under ``metal``, which has no ``"gpu"`` substring.
    """
    k = key.lower()
    return "gpu" in k or k == "metal"


def system_info_has_gpu(devices) -> bool:
    """Whether a ``get_system_info()`` ``devices`` payload reports a usable GPU.

    Availability is delegated to :func:`_device_is_available`, so list-shaped
    entries are handled — live Lemonade returns ``amd_gpu``/``nvidia_gpu`` as
    *lists*, and a present-but-``available: false`` GPU (e.g. an absent discrete
    NVIDIA card) is correctly not counted. Reuses the single availability check
    rather than re-implementing it inline.
    """
    if isinstance(devices, dict):
        return any(
            _is_gpu_device_key(k) and _device_is_available(v)
            for k, v in devices.items()
        )
    if isinstance(devices, list):
        for item in devices:
            if not isinstance(item, dict) or not _device_is_available(item):
                continue
            for k in ("device_type", "type", "id", "name"):
                if k in item and _is_gpu_device_key(str(item[k])):
                    return True
    return False


def _iter_gpu_entries(devices) -> Iterator[Dict[str, Any]]:
    """Yield every per-GPU entry in a ``devices`` payload, shape-agnostic.

    Live Lemonade reports ``amd_gpu``/``nvidia_gpu`` as *lists* of entries
    while ``metal`` and the legacy ``amd_igpu``/``amd_dgpu`` keys are single
    dicts; both are flattened here so callers never branch on shape. A
    top-level *list* payload — where GPU-ness lives in the entry rather than
    the key — is handled the same way :func:`system_info_has_gpu` handles it,
    so the two helpers agree on every shape.
    """
    if isinstance(devices, dict):
        for key, value in devices.items():
            if not _is_gpu_device_key(key):
                continue
            for entry in value if isinstance(value, list) else [value]:
                if isinstance(entry, dict):
                    yield entry
    elif isinstance(devices, list):
        for entry in devices:
            if not isinstance(entry, dict):
                continue
            if any(
                k in entry and _is_gpu_device_key(str(entry[k]))
                for k in ("device_type", "type", "id", "name")
            ):
                yield entry


def _coerce_vram_gb(raw) -> Optional[float]:
    """Parse a device entry's ``vram_gb``, or ``None`` when it is unusable."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        log = get_logger(__name__)
        log.warning(
            "Lemonade reported a non-numeric vram_gb (%r); "
            "reporting VRAM as undetected rather than guessing.",
            raw,
        )
        return None


def _gpu_rank(entry: Dict[str, Any]) -> Tuple[int, float, int, int]:
    """Sort key picking the GPU a user would call "their" GPU (highest wins).

    Ordered: not-integrated, then most VRAM, then carries discrete-card
    identity, then named. The identity term matters on real Windows payloads,
    where an APU's iGPU and a discrete card are both listed with neither
    ``vram_gb`` nor ``integrated`` — only the discrete entry reports a
    ``family``/``driver_version``, so without it a 7900 XTX owner would be
    told they have "AMD Radeon(TM) Graphics". It ranks below VRAM so a larger
    card still wins when both report VRAM.
    """
    vram = _coerce_vram_gb(entry.get("vram_gb"))
    has_discrete_identity = bool(
        str(entry.get("family") or "").strip() or entry.get("driver_version")
    )
    return (
        0 if entry.get("integrated") else 1,
        vram if vram is not None else -1.0,
        1 if has_discrete_identity else 0,
        1 if str(entry.get("name") or "").strip() else 0,
    )


def gpu_display_info(devices) -> Tuple[Optional[str], Optional[float]]:
    """``(name, vram_gb)`` for the GPU a UI should name, from ``get_system_info()``.

    Returns ``(None, None)`` when no *available* GPU is reported, so callers can
    say "not detected" honestly. Never returns a blank string: an entry whose
    ``name`` is empty (real Lemonade does this for an absent NVIDIA card) yields
    ``None``, because a blank name renders as success in the UI when it is not.

    Availability and GPU-key matching reuse :func:`_device_is_available` and
    :func:`_is_gpu_device_key` — the same checks :func:`system_info_has_gpu`
    uses — so the UI and CLI cannot disagree about what counts as a GPU.
    When several GPUs are available, :func:`_gpu_rank` picks the one the user
    would call theirs (the discrete card over an APU's integrated graphics).
    """
    available = [e for e in _iter_gpu_entries(devices) if _device_is_available(e)]
    if not available:
        return None, None
    best = max(available, key=_gpu_rank)
    name = str(best.get("name") or "").strip() or None
    return name, _coerce_vram_gb(best.get("vram_gb"))


def _format_device_error(
    device: Optional[str], required_min_device: Optional[str], detected
) -> str:
    """Build an actionable message for an unmet hardware requirement.

    Names the requested device and the concrete remedy when a high-level
    ``device`` selector was supplied; falls back to the raw tier comparison
    for callers that passed only ``required_min_device`` (e.g. a static
    ``REQUIRED_HARDWARE`` floor).
    """
    detected_list = sorted(str(d) for d in detected)
    if device and device in _DEVICE_REMEDY:
        return (
            f"Requested device '{device}' is not available on this host "
            f"(detected: {detected_list}). {_DEVICE_REMEDY[device]}."
        )
    return (
        f"Hardware requirement not met: required={required_min_device}, "
        f"detected={detected_list}"
    )


class HardwareRequirementError(Exception):
    """Raised when an agent's hardware requirement is not met by the host."""


# Re-export for backwards compatibility — existing callers import
# ``DEFAULT_CONTEXT_SIZE`` from this module. Single source of truth lives
# in ``gaia.llm.lemonade_client``.
__all__ = ["DEFAULT_CONTEXT_SIZE", "LemonadeManager", "MessageType"]
# Lemonade v10.1.0+ default port (was 8000 in v10.0.x). PR #865 bumped the
# minimum supported version, so 13305 is the right default everywhere.
DEFAULT_LEMONADE_URL = "http://localhost:13305"


class MessageType(Enum):
    """Message type for context size notifications."""

    ERROR = "error"
    WARNING = "warning"


class LemonadeManager:
    """Singleton manager for lazy Lemonade server initialization.

    Operates at the LLM level, not tied to specific agent implementations.
    This allows community agents to use GAIA without being hardcoded into profiles.

    Example:
        # Basic usage - just ensure Lemonade is running (default: 32768 context)
        if LemonadeManager.ensure_ready():
            print("Lemonade is ready")

        # With smaller context size for simple tasks
        LemonadeManager.ensure_ready(min_context_size=4096)

        # CLI usage (verbose)
        LemonadeManager.ensure_ready(quiet=False)

        # Get base URL after initialization if needed
        base_url = LemonadeManager.get_base_url()
    """

    _initialized = False
    _base_url: Optional[str] = None
    _context_size: int = 0
    _lock = threading.Lock()
    _log = get_logger(__name__)

    # Rate-limit the per-turn context re-check that fires when context_size==0.
    # Without this, every single message triggers 2 HTTP calls to /health and
    # /models just to re-validate context size — even for "cool" or "no" replies.
    _last_recheck_time: float = 0.0
    _RECHECK_INTERVAL: float = 30.0  # seconds between re-checks

    # Device tiers (``amd_npu``/``amd_dgpu``/``cpu``/…) already validated OK on
    # this host. Hardware is static per process, so once a tier passes we never
    # re-probe it — but a NEWLY requested tier (e.g. the user switching the UI
    # device dropdown to NPU after the manager warmed up on GPU) is still
    # validated, which the ``_initialized`` fast-path would otherwise skip.
    _validated_min_devices: set = set()

    @classmethod
    def is_lemonade_installed(cls) -> bool:
        """Check if Lemonade server is installed."""
        client = LemonadeClient(verbose=False)
        return client.get_lemonade_version() is not None

    @classmethod
    def print_server_error(cls, min_context_size: int = DEFAULT_CONTEXT_SIZE):
        """Print informative error when Lemonade server is not running.

        Shared by CLI and SDK for consistent error messages.

        Args:
            min_context_size: Context size to recommend in error message.
        """
        print(
            "❌ Error: Lemonade server is not running or not accessible.",
            file=sys.stderr,
        )
        print("", file=sys.stderr)

        if not cls.is_lemonade_installed():
            print(
                "📥 Lemonade server is not installed on your system.", file=sys.stderr
            )
            print("", file=sys.stderr)
            print("To install Lemonade server:", file=sys.stderr)
            print("  1. Visit: https://lemonade-server.ai", file=sys.stderr)
            print("  2. Download the installer for your platform", file=sys.stderr)
            print("  3. Run the installer and follow prompts", file=sys.stderr)
            print("", file=sys.stderr)
            print("After installation, try your command again.", file=sys.stderr)
        else:
            print("Lemonade server is installed but not running.", file=sys.stderr)
            print("", file=sys.stderr)
            print(
                "GAIA will automatically start Lemonade Server if installed.",
                file=sys.stderr,
            )
            print("If auto-start fails, you can start it manually by:", file=sys.stderr)
            print("  • Double-clicking the desktop shortcut, or", file=sys.stderr)
            if min_context_size >= 32768:
                print(
                    f"  • Running: lemonade-server serve --ctx-size {min_context_size}",
                    file=sys.stderr,
                )
            else:
                print("  • Running: lemonade-server serve", file=sys.stderr)
            print("", file=sys.stderr)
            if min_context_size >= 32768:
                print(
                    f"Note: GAIA requires larger context size ({min_context_size} tokens)",
                    file=sys.stderr,
                )
                print("", file=sys.stderr)
            base_url = os.getenv("LEMONADE_BASE_URL", f"{DEFAULT_LEMONADE_URL}/api/v1")
            print(
                f"The server should be accessible at {base_url}/health",
                file=sys.stderr,
            )
            print("Then try your command again.", file=sys.stderr)

    @classmethod
    def print_context_message(
        cls,
        current_size: int,
        required_size: int,
        message_type: MessageType = MessageType.ERROR,
    ):
        """Print message when context size is insufficient.

        Shared by CLI and SDK for consistent messages.

        Args:
            current_size: Current server context size in tokens.
            required_size: Required context size in tokens.
            message_type: MessageType.WARNING for warning, MessageType.ERROR for error.
        """
        if message_type == MessageType.WARNING:
            symbol = "⚠️ "
            label = "Context size below recommended"
        else:
            symbol = "❌"
            label = "Insufficient context size"

        print("", file=sys.stderr)
        print(f"{symbol} {label}.", file=sys.stderr)
        print(
            f"   Current: {current_size} tokens, Required: {required_size} tokens",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        print("   To fix this issue:", file=sys.stderr)
        print("   1. Stop the Lemonade server (if running)", file=sys.stderr)
        print(
            f"   2. Restart with: lemonade-server serve --ctx-size {required_size}",
            file=sys.stderr,
        )
        print("", file=sys.stderr)

    @classmethod
    def _validate_device_requirement(cls, client, required_min_device, device):
        """Raise ``HardwareRequirementError`` if *required_min_device* isn't met.

        Resolves detected devices via ``client.get_system_info()`` and compares
        capability tiers. Raises only on a *genuine* hardware shortfall — any
        failure reaching the server (connection refused, timeout) propagates
        unchanged so the caller can treat it as "not reachable yet" rather than
        mislabelling a down server as missing hardware.
        """
        sys_info = client.get_system_info()
        devices = sys_info.get("devices", {})
        detected = set()
        # devices may be a dict or a list; handle both. A device only counts as
        # detected when its ``available`` flag is truthy — present-but-disabled
        # hardware must not satisfy a REQUIRED_HARDWARE floor.
        if isinstance(devices, dict):
            detected = {
                name for name, info in devices.items() if _device_is_available(info)
            }
        elif isinstance(devices, list):
            if all(isinstance(x, str) for x in devices):
                detected = set(devices)
            else:
                for item in devices:
                    if isinstance(item, dict):
                        if not _device_is_available(item):
                            continue
                        # Prefer explicit device_type when available.
                        for k in ("device_type", "type", "id", "name"):
                            if k in item:
                                detected.add(str(item[k]))
                                break
        # Lemonade on Apple Silicon reports the llama.cpp Metal backend as
        # 'metal' in system_info while its health payload calls the same device
        # 'gpu' — normalize to the generic GPU tier so a default device='gpu'
        # request validates on macOS instead of falling through to cpu.
        if "metal" in detected:
            detected.add("amd_dgpu")
        # Find highest-capability detected device
        highest = None
        for dev in _DEVICE_PRIORITY:
            if dev in detected:
                highest = dev
                break
        if highest is None:
            # assume CPU-only host if nothing reported
            highest = "cpu"

        # Check capability ordering: lower index == higher capability
        req_idx = (
            _DEVICE_PRIORITY.index(required_min_device)
            if required_min_device in _DEVICE_PRIORITY
            else len(_DEVICE_PRIORITY) - 1
        )
        detected_idx = (
            _DEVICE_PRIORITY.index(highest)
            if highest in _DEVICE_PRIORITY
            else len(_DEVICE_PRIORITY) - 1
        )
        if detected_idx <= req_idx:
            recipe = _RECIPE_BY_DEVICE.get(highest, _RECIPE_BY_DEVICE.get("cpu"))
            cls._log.debug(
                f"Hardware requirement satisfied: {highest} -> recipe={recipe}"
            )
        else:
            raise HardwareRequirementError(
                _format_device_error(device, required_min_device, detected)
            )

    @classmethod
    def _maybe_validate_device(
        cls, required_min_device, device, base_url, host, port, quiet
    ):
        """Validate the requested device on EVERY ensure_ready call.

        The ``_initialized`` singleton fast-path skips the full init block, so
        without this a device switch after the manager is already warm (the UI
        dropdown case) would never be checked. Memoised per tier so a passing
        device is probed at most once per process. Caller must hold ``_lock``.
        """
        if not required_min_device:
            return
        if required_min_device in cls._validated_min_devices:
            return
        try:
            if base_url:
                probe = LemonadeClient(
                    base_url=base_url, keep_alive=True, verbose=not quiet
                )
            else:
                probe = LemonadeClient(
                    host=host, port=port, keep_alive=True, verbose=not quiet
                )
            cls._validate_device_requirement(probe, required_min_device, device)
        except HardwareRequirementError:
            raise
        except Exception as e:
            # A server that's merely unreachable (connection refused / timeout)
            # is expected and soft — don't mislabel it as missing hardware; the
            # down server is surfaced by the init path (or the model load).
            # Anything else (e.g. a malformed system-info response, a bug in
            # _validate_device_requirement) is logged at WARNING so it stays
            # visible instead of silently skipping validation.
            msg = str(e)
            is_conn = (
                "Request failed:" in msg
                or "refused" in msg.lower()
                or "timeout" in msg.lower()
                or "timed out" in msg.lower()
            )
            if is_conn:
                cls._log.debug("Skipping device validation (not reachable yet): %s", e)
            else:
                cls._log.warning(
                    "Skipping device validation (unexpected probe error): %s", e
                )
            return
        cls._validated_min_devices.add(required_min_device)

    @classmethod
    def ensure_ready(
        cls,
        min_context_size: int = DEFAULT_CONTEXT_SIZE,
        quiet: bool = True,
        base_url: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        required_min_device: Optional[str] = None,
        device: Optional[str] = None,
    ) -> bool:
        """Ensure Lemonade server is running with sufficient context size.

        This is the main entry point for both CLI and SDK flows.
        Safe to call multiple times - validates context size on each call.
        An explicit ``min_context_size=None`` (callers threading through an
        unset config value) means "the default floor", never a crash.

        Args:
            min_context_size: Minimum context size required (default: 32768).
            quiet: Suppress output (default: True for SDK, set False for CLI)
            base_url: Full base URL (e.g., "http://localhost:13305/api/v1").
                     If provided, host and port are parsed from it.
            host: Override host (default: from LEMONADE_BASE_URL env or localhost)
            port: Override port (default: from LEMONADE_BASE_URL env or 13305)
            required_min_device: Optional device tier required by the caller.
                If provided, Lemonade is queried at runtime via
                `LemonadeClient.get_system_info()` and the detected device
                capability is compared against `required_min_device`. NOTE:
                This method queries the running Lemonade server at runtime; it
                does NOT read or write a local `~/.gaia/` hardware config.
                The resolved `recipe` is computed and logged for debugging,
                but is NOT applied to the Lemonade server by this method.
            device: High-level device selector ('cpu', 'gpu', 'npu').
                When set, maps to the appropriate ``required_min_device``
                value.  Explicit ``required_min_device`` takes precedence.

        Returns:
            True if Lemonade server is ready, False otherwise.
            Use get_base_url() to retrieve the server URL after initialization.

        Note:
            The Lemonade server must be running before calling this method.
            Start it with: lemonade-server serve --ctx-size 32768
        """
        # Callers thread config values through verbatim — an unset (None)
        # floor means the default, never a TypeError at the ctx comparison.
        if min_context_size is None:
            min_context_size = DEFAULT_CONTEXT_SIZE
        # Map high-level device selector to required_min_device when the
        # caller didn't pass an explicit required_min_device.
        if device and not required_min_device:
            required_min_device = _DEVICE_TO_MIN.get(device)
        # Parse host and port from base_url if provided
        if base_url and (host is None or port is None):
            from urllib.parse import urlparse

            parsed = urlparse(base_url)
            if host is None:
                host = parsed.hostname
            if port is None:
                port = parsed.port
        with cls._lock:
            # Validate the requested device first — runs on every call (not just
            # first init) so a UI device switch after the manager is warm is
            # still checked. Memoised per tier, so this is at most one probe per
            # distinct device per process. Raises HardwareRequirementError when
            # the device is genuinely absent.
            cls._maybe_validate_device(
                required_min_device, device, base_url, host, port, quiet
            )

            # If already initialized, just verify context size
            if cls._initialized:
                if cls._context_size >= min_context_size:
                    cls._log.debug(
                        "Lemonade already initialized with sufficient context"
                    )
                    return True
                else:
                    # Context size is below minimum — may be cached from before
                    # models were loaded.  Rate-limit re-checks: without this guard,
                    # every single chat message triggers 2 HTTP calls (/health +
                    # /models) just to re-validate context size, adding 40-200 ms of
                    # blocking overhead even for trivial replies like "cool".
                    now = time.monotonic()
                    if now - cls._last_recheck_time < cls._RECHECK_INTERVAL:
                        cls._log.debug(
                            "Skipping context re-check (%.1fs ago, interval=%.1fs)",
                            now - cls._last_recheck_time,
                            cls._RECHECK_INTERVAL,
                        )
                        return True
                    cls._last_recheck_time = now

                    # Re-check current status to see if models are loaded now
                    try:
                        if base_url:
                            client = LemonadeClient(
                                base_url=base_url,
                                keep_alive=True,
                                verbose=not quiet,
                            )
                        else:
                            client = LemonadeClient(
                                host=host,
                                port=port,
                                keep_alive=True,
                                verbose=not quiet,
                            )
                        status = client.get_status()
                        # Update cached context size
                        cls._context_size = status.context_size or 0

                        # Only warn if LLM models are loaded AND context is insufficient
                        # SD models don't have context size, only LLM models do
                        llm_models_loaded = any(
                            "image" not in model.get("labels", [])
                            for model in status.loaded_models
                        )

                        # If models are loaded but the server doesn't report context_size
                        # (returns 0 — common with Lemonade 10+), treat it as sufficient
                        # so the fast path is taken on subsequent calls.
                        if cls._context_size == 0 and llm_models_loaded:
                            cls._log.debug(
                                "LLM models loaded but context_size not reported by server; "
                                "assuming context is sufficient (min=%d)",
                                min_context_size,
                            )
                            cls._context_size = min_context_size

                        # Only warn if context_size is non-zero (0 means no model loaded or still loading)
                        if (
                            cls._context_size > 0
                            and cls._context_size < min_context_size
                            and llm_models_loaded
                        ):
                            if cls._try_reload_with_ctx(
                                client, status, min_context_size, quiet, cls._lock
                            ):
                                return True
                            cls._log.warning(
                                f"Lemonade running with {cls._context_size} tokens, "
                                f"but {min_context_size} requested. "
                                f"Restart with: lemonade-server serve --ctx-size {min_context_size}"
                            )
                            if not quiet:
                                cls.print_context_message(
                                    cls._context_size,
                                    min_context_size,
                                    MessageType.WARNING,
                                )
                    except Exception as e:
                        cls._log.debug(f"Failed to re-check status: {e}")
                    return True

            cls._log.debug(f"Initializing Lemonade (min context: {min_context_size})")

            try:
                # When base_url is provided, pass it directly to LemonadeClient
                # so it preserves the full URL (including https:// for ngrok, etc.)
                # rather than reconstructing from host/port with http://
                if base_url:
                    client = LemonadeClient(
                        base_url=base_url,
                        keep_alive=True,
                        verbose=not quiet,
                    )
                else:
                    client = LemonadeClient(
                        host=host,
                        port=port,
                        keep_alive=True,
                        verbose=not quiet,
                    )

                # Just check server status - no agent profile required
                status = client.get_status()

                if not status.running:
                    cls._log.warning("Lemonade server is not running")
                    if not quiet:
                        cls.print_server_error(min_context_size)
                    return False

                # Defensive normalisation: some Lemonade versions can return
                # `loaded_models: null` in their JSON, which would crash the
                # `any(... for model in ...)` calls below.
                if status.loaded_models is None:
                    status.loaded_models = []

                # Snapshot context size — we may overwrite it below if the
                # preload helper successfully seeds the server.
                context_size_value = status.context_size or 0

                # Detect LLM-loaded state once for the branch decisions below.
                llm_models_loaded = any(
                    # Health-format ``type=="llm"`` is the precise check;
                    # the label fallback covers any legacy code path that
                    # populated ``status.loaded_models`` from the catalog.
                    model.get("type") == "llm"
                    or (
                        model.get("type") is None
                        and "image" not in model.get("labels", [])
                        and "embeddings" not in model.get("labels", [])
                    )
                    for model in status.loaded_models
                )

                # Idle server (no model loaded, no ctx reported): proactively
                # load the default model with the required ctx_size.  Without
                # this, the user would land on a server with a too-small
                # default ctx and be told to manually stop and restart it
                # (issue #839).  We run this BEFORE setting cls._initialized
                # so a failed preload leaves the singleton retryable instead
                # of poisoned with (initialized=True, ctx=0).
                if context_size_value == 0 and not llm_models_loaded:
                    cls._try_preload_with_ctx(
                        client, min_context_size, quiet, cls._lock
                    )
                    context_size_value = min_context_size
                    # Re-fetch model list so the small-ctx reload branch below
                    # sees the freshly-loaded model.
                    status = client.get_status()
                    if status.loaded_models is None:
                        status.loaded_models = []
                    llm_models_loaded = any(
                        "image" not in model.get("labels", [])
                        for model in status.loaded_models
                    )

                # Cache server state for subsequent calls.  Setting
                # _initialized=True after the preload guard ensures a failed
                # preload does NOT poison the singleton: ensure_ready will
                # re-enter this block on the next call.
                cls._initialized = True
                cls._base_url = client.base_url
                cls._context_size = context_size_value

                cls._log.debug(
                    f"Lemonade ready at {cls._base_url} "
                    f"(context: {cls._context_size} tokens)"
                )

                # Device requirement is validated up-front by
                # ``_maybe_validate_device`` (runs on every call, before this
                # init block), so there is nothing to re-check here.

                # Only warn if:
                # 1. Context size is non-zero (0 means no model loaded or model still loading)
                # 2. Context size is less than required
                # 3. LLM models are loaded (SD models don't have context size)
                if (
                    cls._context_size > 0
                    and cls._context_size < min_context_size
                    and llm_models_loaded
                ):
                    if cls._try_reload_with_ctx(
                        client, status, min_context_size, quiet, cls._lock
                    ):
                        return True
                    cls._log.warning(
                        f"Context size {cls._context_size} is less than "
                        f"requested {min_context_size}. Some features may not work correctly."
                    )
                    if not quiet:
                        cls.print_context_message(
                            cls._context_size, min_context_size, MessageType.WARNING
                        )
                    return True

                return True

            except (LemonadeClientError, HardwareRequirementError):
                # Actionable errors - propagate so callers see the cause and
                # can handle/report it (no silent fallbacks).
                raise
            except Exception as e:
                cls._log.warning(f"Failed to initialize Lemonade: {e}")
                if not quiet:
                    cls.print_server_error(min_context_size)
                return False

    @classmethod
    def _try_preload_with_ctx(
        cls,
        client: "LemonadeClient",
        min_context_size: int,
        quiet: bool,
        lock: "threading.Lock",
    ) -> None:
        """Load the default LLM with the required ctx_size on an idle server.

        Closes the gap left by `_try_reload_with_ctx`, which only handles the
        "model already loaded with too-small ctx" path.  When the server is
        running but idle (no model loaded, no ctx reported), this helper
        proactively seeds it so the user does not see the legacy
        "Restart with: lemonade-server serve --ctx-size N" message
        (issue #839).

        Releases `lock` for the duration of the blocking `load_model` call —
        important because `auto_download=True` means a first-run user pays a
        full model-download window (potentially minutes), and we must not
        block other threads (status pollers, parallel `ensure_ready` callers)
        for that long.  Mirrors the lock discipline of `_try_reload_with_ctx`.

        Raises:
            LemonadeClientError: if `load_model` fails. Carries an actionable
                message (Lemonade / ctx_size= / lemonade-server serve) so the
                user can recover manually if the auto-preload cannot.
        """
        cls._log.info(
            "Preloading '%s' with ctx_size=%d on idle Lemonade server",
            DEFAULT_MODEL_NAME,
            min_context_size,
        )
        if not quiet:
            print(
                f"\n⏳ Loading {DEFAULT_MODEL_NAME} with ctx_size={min_context_size} "
                f"tokens. This may take a moment (first run downloads the model)...",
                flush=True,
            )

        # Release the lock for the duration of the blocking call so
        # concurrent callers and status-pollers are not stalled.  The
        # `finally` block re-acquires before any exception propagates back
        # up to the surrounding `with cls._lock:` context manager.
        lock.release()
        try:
            client.load_model(
                DEFAULT_MODEL_NAME,
                ctx_size=min_context_size,
                prompt=False,
                auto_download=True,
            )
        except Exception as e:
            raise LemonadeClientError(
                f"Failed to preload Lemonade model {DEFAULT_MODEL_NAME!r} with "
                f"ctx_size={min_context_size} on idle server at "
                f"{client.base_url}.\n"
                f"To recover manually: stop the running server, then run "
                f"'lemonade-server serve --ctx-size {min_context_size}' and "
                f"re-run your GAIA command.\n"
                f"See the Lemonade server log for details "
                f"(typical path: ~/.cache/lemonade/server.log)."
            ) from e
        finally:
            lock.acquire()

        if not quiet:
            print(
                f"✅ Loaded {DEFAULT_MODEL_NAME} with ctx_size={min_context_size}.",
                flush=True,
            )

    @classmethod
    def _try_reload_with_ctx(
        cls,
        client: "LemonadeClient",
        status,
        min_context_size: int,
        quiet: bool,
        lock: "threading.Lock",
    ) -> bool:
        """Attempt to reload the current LLM model with a larger context size.

        Temporarily releases `lock` during the blocking load_model() call so
        other threads are not stalled for the duration of the reload.

        Returns True if reload succeeded and context is now sufficient.
        """
        # Filter to the LLM(s) actually loaded. ``type=="llm"`` is the
        # precise check on health-format entries; the label fallback
        # covers legacy code paths that populate ``loaded_models`` from
        # the catalog (which lacks ``type``). Embedding and image models
        # are excluded — reloading them with an LLM ctx_size makes no
        # sense and (pre-#1030 follow-up) used to load the wrong model
        # entirely because the embedder can sort before ``Gemma-…``.
        llm_models = [
            m
            for m in status.loaded_models
            if m.get("type") == "llm"
            or (
                m.get("type") is None
                and "image" not in m.get("labels", [])
                and "embeddings" not in m.get("labels", [])
            )
        ]
        if not llm_models:
            return False

        model_id = llm_models[0].get("model_name") or llm_models[0].get("id", "")
        if not model_id:
            return False

        cls._log.info(
            f"Auto-reloading '{model_id}' with ctx_size={min_context_size} "
            f"(was {cls._context_size})"
        )
        if not quiet:
            print(
                f"\n⏳ Reloading model with ctx_size={min_context_size} tokens "
                f"(was {cls._context_size}). This may take a moment...",
                flush=True,
            )

        # Release the lock for the duration of the blocking model reload so
        # other threads (e.g. status polling) are not stalled.
        lock.release()
        try:
            client.load_model(model_id, ctx_size=min_context_size, prompt=False)
            # Check if the server now reports the new context size.
            # Some Lemonade versions do not expose ctx_size in their status
            # (they return 0), and some may not honor the ctx_size parameter
            # at all (always reporting the default, e.g. 4096).
            #
            # Regardless of what the server reports, update cls._context_size
            # to min_context_size so we don't trigger an infinite reload loop
            # on every request.  If the reload didn't actually change the
            # model's context window the agent will still run — responses may
            # be degraded — but at least the UI won't be stuck in a reload
            # cycle on every message.
            new_status = client.get_status()
            reported_ctx = new_status.context_size or 0
            actual_ctx = (
                reported_ctx if reported_ctx >= min_context_size else min_context_size
            )
            success = reported_ctx >= min_context_size
            if success:
                cls._log.info(
                    f"Model reloaded successfully with ctx_size={reported_ctx}"
                )
                if not quiet:
                    print(f"✅ Context size updated to {reported_ctx} tokens.")
            else:
                cls._log.warning(
                    "ctx_size after reload: reported=%d (need %d). "
                    "Assuming reload succeeded to prevent reload loop.",
                    reported_ctx,
                    min_context_size,
                )
            # Always update the cached context size to break the reload loop.
            cls._context_size = actual_ctx
            return success
        except Exception as e:
            cls._log.warning(f"Auto-reload failed: {e}")
            return False
        finally:
            # Re-acquire before returning to the `with cls._lock:` block.
            lock.acquire()

    @classmethod
    def is_initialized(cls) -> bool:
        """Check if Lemonade has been initialized."""
        return cls._initialized

    @classmethod
    def get_base_url(cls) -> Optional[str]:
        """Get the base URL if initialized."""
        return cls._base_url

    @classmethod
    def get_context_size(cls) -> int:
        """Get the current context size."""
        return cls._context_size

    @classmethod
    def reset(cls):
        """Reset initialization state.

        Primarily used for testing to allow re-initialization.
        """
        with cls._lock:
            cls._initialized = False
            cls._base_url = None
            cls._context_size = 0
            cls._last_recheck_time = 0.0
            cls._validated_min_devices = set()
            cls._log.debug("LemonadeManager state reset")
