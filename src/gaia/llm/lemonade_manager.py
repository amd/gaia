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
from typing import Optional

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
        # Map high-level device selector to required_min_device when the
        # caller didn't pass an explicit required_min_device.
        if device and not required_min_device:
            _DEVICE_TO_MIN = {
                "npu": "amd_npu",
                "gpu": "amd_igpu",
            }
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

                # If a caller requested a minimum device tier, resolve
                # detected devices and ensure the requirement is met.
                if required_min_device:
                    try:
                        sys_info = client.get_system_info()
                        devices = sys_info.get("devices", {})
                        detected = set()
                        # devices may be a dict or a list; handle both
                        if isinstance(devices, dict):
                            detected = set(devices.keys())
                        elif isinstance(devices, list):
                            # list of strings or list of dicts
                            if all(isinstance(x, str) for x in devices):
                                detected = set(devices)
                            else:
                                for item in devices:
                                    if isinstance(item, dict):
                                        # try common keys
                                        # Prefer explicit device_type when available.
                                        for k in ("device_type", "type", "id", "name"):
                                            if k in item:
                                                detected.add(str(item[k]))
                                                break
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
                            # Satisfied: determine recipe (allow-listed)
                            recipe = _RECIPE_BY_DEVICE.get(
                                highest, _RECIPE_BY_DEVICE.get("cpu")
                            )
                            cls._log.debug(
                                f"Hardware requirement satisfied: {highest} -> recipe={recipe}"
                            )
                        else:
                            # Not satisfied: raise actionable error
                            raise HardwareRequirementError(
                                f"Hardware requirement not met: required={required_min_device}, detected={sorted(list(detected))}"
                            )
                    except HardwareRequirementError:
                        raise
                    except Exception as e:
                        # Propagate as a HardwareRequirementError so callers
                        # cannot silently ignore device-resolution failures.
                        raise HardwareRequirementError(
                            f"Failed to resolve hardware devices: {e}"
                        ) from e

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
        # entirely because ``nomic-embed-…`` sorts before ``Gemma-…``.
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
            cls._log.debug("LemonadeManager state reset")
