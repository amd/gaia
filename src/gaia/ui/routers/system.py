# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""System and health-check endpoints for GAIA Agent UI."""

import asyncio
import json
import logging
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gaia.llm.lemonade_client import (
    DEFAULT_CONTEXT_SIZE,
    lemonade_auth_headers,
    resolve_lemonade_api_key,
)

from ..database import ChatDatabase
from ..dependencies import get_db, get_dispatch_queue
from ..models import (
    DownloadProgress,
    InitTaskInfo,
    ModelStatus,
    SettingsResponse,
    SettingsUpdateRequest,
    SystemStatus,
    TaskListResponse,
    TaskResponse,
)

_VALID_AGENT_MODES = {"manual", "goal_driven", "autonomous"}

logger = logging.getLogger(__name__)

# Hold references to background tasks to prevent GC
_background_tasks: set[asyncio.Task] = set()

router = APIRouter(tags=["system"])

# Default model required for GAIA Chat agent
_DEFAULT_MODEL_NAME = "Gemma-4-E4B-it-GGUF"
# Minimum context window (tokens) needed for reliable agent operation.
# Sourced from ``gaia.llm.lemonade_client`` to keep the GAIA-wide ctx
# requirement in a single module (see that module's ``DEFAULT_CONTEXT_SIZE``).
_MIN_CONTEXT_SIZE = DEFAULT_CONTEXT_SIZE


def _get_lemonade_base_url() -> str:
    """Return the Lemonade Server API base URL from environment or default."""
    return os.environ.get("LEMONADE_BASE_URL", "http://localhost:13305/api/v1")


async def _lemonade_post(
    path: str,
    payload: dict,
    *,
    timeout: float,
    log_context: str,
) -> None:
    """POST to a Lemonade API endpoint, logging the result."""
    try:
        import httpx  # pylint: disable=import-outside-toplevel

        base_url = _get_lemonade_base_url()
        headers = lemonade_auth_headers(resolve_lemonade_api_key())
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url}/{path}", json=payload, headers=headers
            )
            if resp.status_code == 200:
                logger.info("%s succeeded", log_context)
            else:
                logger.warning(
                    "%s returned %d: %s",
                    log_context,
                    resp.status_code,
                    resp.text[:200],
                )
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("%s failed: %s", log_context, exc)


# ── Download progress tracking ──────────────────────────────────────────────
#
# Lemonade's ``POST /v1/pull`` with ``stream=true`` emits SSE events:
#
#     event: progress
#     data: {"bytes_downloaded":N, "bytes_previously_downloaded":M,
#            "bytes_total":T, "file":"foo.gguf", "file_index":i,
#            "percent":p, "total_download_size":T_total, "total_files":n}
#     event: complete
#     data: {...}
#     event: error
#     data: {"error":"..."}
#
# We hold one open streaming POST per active download and stash the latest
# event in ``_download_progress`` so the frontend's existing
# ``/api/system/status`` poll loop picks it up — no second polling channel
# needed and no SSE pass-through endpoint to maintain.
#
# Why an in-memory dict instead of a per-request channel: there's only one
# UI per server, downloads are slow (minutes), and the cost of one keyed
# lookup per status poll is negligible. If we ever need to surface progress
# to multiple distinct clients we can swap this for a fan-out without
# touching callers.
_download_progress: Dict[str, Dict[str, Any]] = {}
_download_progress_lock = threading.Lock()
_DOWNLOAD_PROGRESS_TTL = 30.0  # seconds to retain a terminal entry post-finish


def _set_download_progress(model_name: str, payload: Dict[str, Any]) -> None:
    with _download_progress_lock:
        _download_progress[model_name] = payload


def _get_download_progress(model_name: str) -> Optional[Dict[str, Any]]:
    """Return a copy of the latest progress dict, or None."""
    with _download_progress_lock:
        entry = _download_progress.get(model_name)
        return dict(entry) if entry else None


def _clear_download_progress(model_name: str) -> None:
    with _download_progress_lock:
        _download_progress.pop(model_name, None)


async def _evict_progress_after_ttl(model_name: str) -> None:
    """Delay-evict a terminal progress entry so the frontend's poll cycle
    has a chance to observe the ``complete`` / ``error`` state once.
    Without this, a fast download finishes between two polls and the UI
    misses the "complete" beat entirely.
    """
    try:
        await asyncio.sleep(_DOWNLOAD_PROGRESS_TTL)
    finally:
        _clear_download_progress(model_name)


def _parse_pull_event_block(block: str) -> Optional[Dict[str, Any]]:
    """Convert one ``event:/data:`` block from Lemonade's pull stream into
    ``{"event": <name>, "data": <parsed-json>}``.

    Lemonade sends ``event: <name>`` then ``data: <json>`` then a blank
    line.  Any malformed lines are skipped — pull streams are best-effort
    and a single garbled block shouldn't tank the whole UI.
    """
    event_name: Optional[str] = None
    data_payload: Optional[Dict[str, Any]] = None
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            try:
                data_payload = json.loads(line.split(":", 1)[1].strip())
            except json.JSONDecodeError:
                continue
    if event_name is None:
        return None
    return {"event": event_name, "data": data_payload or {}}


async def _auto_load_after_download(model_name: str) -> None:
    """Issue a Lemonade ``/v1/load`` for ``model_name`` at our default ctx.

    Called from ``_stream_lemonade_pull`` once the pull completes so the
    user doesn't have to take a second action ("model downloaded — now go
    click Load") to actually start chatting. We use the same
    ``DEFAULT_CONTEXT_SIZE`` (32K) the rest of GAIA expects, otherwise
    Lemonade falls back to its own much smaller default and the next
    chat hits the "ctx too small" reload branch.

    Errors are swallowed: a load failure here surfaces via the existing
    ``/api/system/status`` health check; logging is enough.
    """
    payload = {
        "model_name": model_name,
        "ctx_size": DEFAULT_CONTEXT_SIZE,
    }
    logger.info(
        "Auto-load after download: %s (ctx_size=%d)",
        model_name,
        DEFAULT_CONTEXT_SIZE,
    )
    # 600 s ceiling is generous on purpose: cold mmap + KV-cache alloc
    # for a fresh GGUF can take 30–60 s on consumer hardware, more on
    # cold disks. Better to wait than to fire a 30 s timeout that then
    # makes the user wonder why "model loaded" never flips.
    await _lemonade_post(
        "load",
        payload,
        timeout=600.0,
        log_context=f"Auto-load {model_name}",
    )


async def _stream_lemonade_pull(model_name: str, force: bool) -> None:
    """Issue ``POST /v1/pull?stream=true`` and feed the SSE event stream
    into ``_download_progress[model_name]``.

    Handles three terminal cases (success, server error, connection
    blowup) and always leaves the entry in a state the frontend can
    interpret; an unhandled exception that left ``state="downloading"``
    would peg the UI's spinner forever.
    """
    import httpx  # pylint: disable=import-outside-toplevel

    base_url = _get_lemonade_base_url()
    payload: Dict[str, Any] = {"model_name": model_name, "stream": True}
    if force:
        payload["force"] = True

    _set_download_progress(
        model_name,
        {
            "state": "starting",
            "model_name": model_name,
            "percent": 0,
            "file": None,
            "file_index": 0,
            "total_files": 0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "message": None,
        },
    )
    logger.info("Pull stream starting: %s (force=%s)", model_name, force)

    # Track files we've already finished so the running ``downloaded_bytes``
    # advances monotonically across the multi-file pull. Lemonade's progress
    # events carry per-file counters; we synthesise an overall counter by
    # adding each file's ``bytes_total`` once it transitions away.
    completed_bytes_total = 0
    last_file: Optional[str] = None
    last_bytes_total = 0

    # Connect timeout 10 s (network quirks); read timeout disabled because the
    # download itself can take many minutes between progress events on a slow
    # link, and we'd rather hold open than wrongly bail.
    client_timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    headers = lemonade_auth_headers(resolve_lemonade_api_key())
    try:
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            async with client.stream(
                "POST", f"{base_url}/pull", json=payload, headers=headers
            ) as resp:
                # 401 must be handled BEFORE the generic non-200 branch —
                # response body could carry a reflected Authorization header
                # from a misconfigured reverse proxy, leaking the key into
                # the SSE progress channel and the server log. Matches the
                # pattern used at the five LemonadeClient chokepoints.
                if resp.status_code == 401:
                    _set_download_progress(
                        model_name,
                        {
                            "state": "error",
                            "model_name": model_name,
                            "percent": 0,
                            "file": None,
                            "file_index": 0,
                            "total_files": 0,
                            "downloaded_bytes": 0,
                            "total_bytes": 0,
                            "message": (
                                "Lemonade /pull returned 401 Unauthorized. "
                                "Verify LEMONADE_API_KEY is correct."
                            ),
                        },
                    )
                    logger.error("Pull stream HTTP 401 for %s", model_name)
                    return

                if resp.status_code != 200:
                    body = await resp.aread()
                    snippet = body.decode("utf-8", errors="replace")[:300]
                    _set_download_progress(
                        model_name,
                        {
                            "state": "error",
                            "model_name": model_name,
                            "percent": 0,
                            "file": None,
                            "file_index": 0,
                            "total_files": 0,
                            "downloaded_bytes": 0,
                            "total_bytes": 0,
                            "message": (
                                f"Lemonade /pull returned {resp.status_code}: {snippet}"
                            ),
                        },
                    )
                    logger.error(
                        "Pull stream HTTP %d for %s: %s",
                        resp.status_code,
                        model_name,
                        snippet,
                    )
                    return

                buffer = ""
                async for chunk in resp.aiter_text():
                    buffer += chunk
                    # SSE blocks are separated by a blank line.
                    while "\n\n" in buffer:
                        block, buffer = buffer.split("\n\n", 1)
                        parsed = _parse_pull_event_block(block)
                        if parsed is None:
                            continue
                        ev = parsed["event"]
                        data = parsed["data"]

                        if ev == "progress":
                            file_name = data.get("file") or ""
                            current_bytes = int(data.get("bytes_downloaded") or 0)
                            file_total = int(data.get("bytes_total") or 0)
                            total_size = int(data.get("total_download_size") or 0)

                            # When the file changes, the previous file's full
                            # ``bytes_total`` becomes part of the cumulative
                            # downloaded count. Using the last seen total
                            # (rather than the new event's reported total)
                            # avoids underflow when files have skipped bytes.
                            if last_file is not None and file_name != last_file:
                                completed_bytes_total += last_bytes_total

                            last_file = file_name
                            last_bytes_total = file_total

                            overall_bytes = completed_bytes_total + current_bytes
                            overall_percent = (
                                int(min(100, overall_bytes * 100 / total_size))
                                if total_size > 0
                                else int(data.get("percent") or 0)
                            )

                            _set_download_progress(
                                model_name,
                                {
                                    "state": "downloading",
                                    "model_name": model_name,
                                    "percent": overall_percent,
                                    "file": file_name,
                                    "file_index": int(data.get("file_index") or 0),
                                    "total_files": int(data.get("total_files") or 0),
                                    "downloaded_bytes": overall_bytes,
                                    "total_bytes": total_size,
                                    "message": None,
                                },
                            )
                        elif ev == "complete":
                            _set_download_progress(
                                model_name,
                                {
                                    "state": "complete",
                                    "model_name": model_name,
                                    "percent": 100,
                                    "file": None,
                                    "file_index": int(data.get("file_index") or 0),
                                    "total_files": int(data.get("total_files") or 0),
                                    "downloaded_bytes": completed_bytes_total
                                    + last_bytes_total,
                                    "total_bytes": completed_bytes_total
                                    + last_bytes_total,
                                    "message": None,
                                },
                            )
                            logger.info("Pull stream complete: %s", model_name)
                            # Kick off auto-load so the user doesn't have to
                            # manually click "Load" after a multi-minute pull.
                            # We hand off to a separate task because the load
                            # itself can take ~30 s (the 4B GGUFs warm-load
                            # quickly, but mmap + KV-cache alloc is real work)
                            # and we want this stream's TTL eviction to fire
                            # on schedule rather than waiting on the load.
                            load_task = asyncio.create_task(
                                _auto_load_after_download(model_name)
                            )
                            _background_tasks.add(load_task)
                            load_task.add_done_callback(_background_tasks.discard)
                        elif ev == "error":
                            err_msg = (
                                data.get("error")
                                or data.get("message")
                                or "Download failed"
                            )
                            _set_download_progress(
                                model_name,
                                {
                                    "state": "error",
                                    "model_name": model_name,
                                    "percent": 0,
                                    "file": None,
                                    "file_index": 0,
                                    "total_files": 0,
                                    "downloaded_bytes": 0,
                                    "total_bytes": 0,
                                    "message": err_msg,
                                },
                            )
                            logger.error(
                                "Pull stream error for %s: %s", model_name, err_msg
                            )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Pull stream blew up for %s: %s", model_name, exc, exc_info=True)
        _set_download_progress(
            model_name,
            {
                "state": "error",
                "model_name": model_name,
                "percent": 0,
                "file": None,
                "file_index": 0,
                "total_files": 0,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "message": f"Download stream interrupted: {exc}",
            },
        )
    finally:
        # Schedule TTL-bounded eviction so the next download (potentially with
        # the same model_name on retry) starts from a clean ``starting`` state.
        evict_task = asyncio.create_task(_evict_progress_after_ttl(model_name))
        _background_tasks.add(evict_task)
        evict_task.add_done_callback(_background_tasks.discard)


@router.get("/api/system/status", response_model=SystemStatus)
async def system_status(request: Request, db: ChatDatabase = Depends(get_db)):
    """Check system readiness (Lemonade, models, disk space)."""
    status = SystemStatus()

    # Check Lemonade Server
    # Use a generous timeout (10s) because when the LLM is handling many
    # parallel requests it may take a while to respond to the health check.
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            base_url = _get_lemonade_base_url()
            _auth = lemonade_auth_headers(resolve_lemonade_api_key())

            # Derive the Lemonade web UI URL (scheme://host:port without /api/v1)
            try:
                _parsed = urlparse(base_url)
                status.lemonade_url = f"{_parsed.scheme}://{_parsed.netloc}"
            except Exception:
                pass  # Keep the default "http://localhost:13305"

            # Use /health endpoint to get the actually loaded model
            # (not /models which returns the full catalog of available models)
            health_resp = await client.get(f"{base_url}/health", headers=_auth)
            if health_resp.status_code == 200:
                status.lemonade_running = True
                health_data = health_resp.json()
                status.model_loaded = health_data.get("model_loaded") or None
                status.lemonade_version = health_data.get("version")

                # Extract device info AND actual loaded context size from
                # all_models_loaded. Some Lemonade versions omit the root-level
                # model_loaded field and only expose the list, so when the root
                # field is absent we fall back to the first non-embedding entry.
                # Use case-insensitive match in case Lemonade normalises the name.
                loaded_lower = (status.model_loaded or "").lower()
                _llm_found = False
                for m in health_data.get("all_models_loaded", []):
                    if m.get("type") == "embedding":
                        status.embedding_model_loaded = True
                    else:
                        m_name = m.get("model_name", "")
                        # Match by name when root field was present; otherwise
                        # take the first LLM entry as the fallback.
                        is_match = bool(loaded_lower) and m_name.lower() == loaded_lower
                        is_fallback = not loaded_lower
                        if (is_match or is_fallback) and not _llm_found:
                            if not status.model_loaded:
                                status.model_loaded = m_name
                            status.model_device = m.get("device")
                            # Actual loaded context size (preferred over catalog
                            # default). Use `is not None` so ctx_size=0 triggers
                            # a warning.
                            ctx = m.get("recipe_options", {}).get("ctx_size")
                            if ctx is not None:
                                status.model_context_size = ctx
                            _llm_found = True  # take only the first matching LLM

                # Fallback: older Lemonade versions expose context_size at root level
                if status.model_context_size is None:
                    legacy_ctx = health_data.get("context_size")
                    if legacy_ctx is not None:
                        status.model_context_size = legacy_ctx

                # Fetch model catalog for size, labels, and fallback context size
                models_resp = await client.get(f"{base_url}/models", headers=_auth)
                if models_resp.status_code == 200:
                    for m in models_resp.json().get("data", []):
                        if m.get("id") == status.model_loaded:
                            status.model_size_gb = m.get("size")
                            status.model_labels = m.get("labels")
                            # Only use catalog ctx_size when health data didn't
                            # provide it (e.g. model not yet fully loaded)
                            if status.model_context_size is None:
                                ctx = m.get("recipe_options", {}).get("ctx_size")
                                if ctx is not None:
                                    status.model_context_size = ctx
                        if "embed" in m.get("id", "").lower():
                            status.embedding_model_loaded = True

                # Validate that the loaded model matches what GAIA Chat (or any
                # registered agent) expects. A custom_model override wins over
                # everything; otherwise the loaded model is "expected" if it
                # matches the baseline default *or* any registered agent's
                # preferred model list. This stops Gaia Lite's 4B (or any other
                # non-default agent model) from tripping a "Wrong model" banner.
                if status.model_loaded:
                    custom_model = db.get_setting("custom_model")
                    loaded_lower = status.model_loaded.lower()
                    if custom_model:
                        status.expected_model_loaded = (
                            loaded_lower == custom_model.lower()
                        )
                    else:
                        acceptable = {_DEFAULT_MODEL_NAME.lower()}
                        registry = getattr(request.app.state, "agent_registry", None)
                        if registry is not None:
                            for reg in registry.list():
                                for m in reg.models:
                                    if m:
                                        acceptable.add(m.lower())
                        status.expected_model_loaded = loaded_lower in acceptable
                    # Surface the actual expected name in the response so the
                    # frontend can name it precisely in the warning banner.
                    status.default_model_name = custom_model or _DEFAULT_MODEL_NAME

                # When no LLM is loaded, check if the expected model is downloaded.
                # Respects custom_model override; falls back to the built-in default.
                # Uses show_all=true to see models that are in the catalog but not
                # yet pulled to disk.
                if not status.model_loaded:
                    try:
                        catalog_resp = await client.get(
                            f"{base_url}/models",
                            params={"show_all": "true"},
                            timeout=5.0,
                            headers=_auth,
                        )
                        if catalog_resp.status_code == 200:
                            _custom = db.get_setting("custom_model")
                            default_lower = (_custom or _DEFAULT_MODEL_NAME).lower()
                            for m in catalog_resp.json().get("data", []):
                                if m.get("id", "").lower() == default_lower:
                                    status.model_downloaded = m.get("downloaded", False)
                                    # Capture the catalog size so the
                                    # "not downloaded" banner can show an
                                    # accurate hint instead of a hard-coded
                                    # number that drifts with model changes.
                                    cat_size = m.get("size")
                                    if cat_size is not None:
                                        status.default_model_size_gb = float(cat_size)
                                    break
                            # Model not found in catalog → treat as not downloaded
                            if status.model_downloaded is None:
                                status.model_downloaded = False
                    except Exception:
                        pass  # Don't block status on catalog failure

                # Validate context size sufficiency only when we have a real value.
                # Use `is not None` so ctx_size=0 correctly triggers a warning.
                if status.model_context_size is not None:
                    status.context_size_sufficient = (
                        status.model_context_size >= _MIN_CONTEXT_SIZE
                    )
                    logger.debug(
                        "Context size: %d tokens (required: %d, sufficient: %s)",
                        status.model_context_size,
                        _MIN_CONTEXT_SIZE,
                        status.context_size_sufficient,
                    )

                # Fetch last inference stats (short timeout — supplementary info)
                try:
                    stats_resp = await client.get(
                        f"{base_url}/stats", timeout=3.0, headers=_auth
                    )
                    if stats_resp.status_code == 200:
                        stats_data = stats_resp.json()
                        tps = stats_data.get("tokens_per_second")
                        if tps:
                            status.tokens_per_second = round(tps, 1)
                        ttft = stats_data.get("time_to_first_token")
                        if ttft:
                            status.time_to_first_token = round(ttft, 3)
                except Exception:
                    pass

                # Fetch GPU/NPU/device info (short timeout — supplementary info)
                try:
                    sysinfo_resp = await client.get(
                        f"{base_url}/system-info", timeout=3.0, headers=_auth
                    )
                    if sysinfo_resp.status_code == 200:
                        devices = sysinfo_resp.json().get("devices", {})
                        # Build detected_devices list for multi-device support
                        # (#1220). CPU and GPU are always available — llamacpp
                        # works on both via cpu/vulkan backends. NPU requires
                        # explicit hardware detection.
                        detected = ["cpu", "gpu"]
                        for key, dev in devices.items():
                            if "gpu" in key.lower() and isinstance(dev, dict):
                                status.gpu_name = dev.get("name")
                                status.gpu_vram_gb = dev.get("vram_gb")
                            if "npu" in key.lower() and isinstance(dev, dict):
                                if dev.get("available"):
                                    detected.append("npu")
                        status.detected_devices = detected
                except Exception:
                    pass
            else:
                # Fall back to /models if /health isn't available
                resp = await client.get(f"{base_url}/models", headers=_auth)
                if resp.status_code == 200:
                    status.lemonade_running = True
                    data = resp.json()
                    models = data.get("data", [])
                    if models:
                        status.model_loaded = models[0].get("id", "unknown")
                    for m in models:
                        if "embed" in m.get("id", "").lower():
                            status.embedding_model_loaded = True
                            break
    except Exception:
        status.lemonade_running = False

    # Active profile from persistent config (#1220)
    try:
        from gaia.config import GaiaConfig

        gaia_cfg = GaiaConfig.load()
        status.active_profile = gaia_cfg.profile
    except Exception:
        pass  # Keep default "chat"

    # Disk space
    # Access shutil through gaia.ui.server so test patches on
    # "gaia.ui.server.shutil.disk_usage" take effect correctly.
    try:
        _shutil = sys.modules.get("gaia.ui.server", sys.modules[__name__])
        _shutil_mod = getattr(_shutil, "shutil", shutil)
        usage = _shutil_mod.disk_usage(Path.home())
        status.disk_space_gb = round(usage.free / (1024**3), 1)
    except Exception:
        pass

    # Memory
    try:
        import psutil

        mem = psutil.virtual_memory()
        status.memory_available_gb = round(mem.available / (1024**3), 1)
    except ImportError:
        pass
    except Exception as exc:
        # psutil is installed but the syscall failed — seen in containers
        # under tight seccomp policies, on read-only /proc, etc. Leaving the
        # field as None (rather than aborting the whole status endpoint) lets
        # the modal suppress its memory-warning banner instead of rendering
        # a misleading "0 GB available" panel. Logged loudly so the cause is
        # traceable rather than silently degraded.
        logger.warning(
            "psutil.virtual_memory() failed (%s); memory_available_gb stays None",
            exc,
        )

    # Initialized check
    init_marker = Path.home() / ".gaia" / "chat" / "initialized"
    status.initialized = init_marker.exists()

    # Device support check.
    # Skipped when:
    #   1. GAIA_SKIP_DEVICE_CHECK env var is set to "1", "true", or "yes"
    #   2. LEMONADE_BASE_URL points to a non-localhost server — inference runs
    #      remotely so local hardware requirements don't apply.
    try:
        from gaia.device import check_device_supported, get_processor_name

        skip_check = os.environ.get("GAIA_SKIP_DEVICE_CHECK", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        lemonade_url = os.environ.get("LEMONADE_BASE_URL", "")
        _LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}
        try:
            _parsed_hostname = urlparse(lemonade_url).hostname or ""
        except Exception:
            _parsed_hostname = ""
        is_remote = bool(lemonade_url) and _parsed_hostname not in _LOCAL_HOSTS

        if skip_check or is_remote:
            status.device_supported = True
            status.processor_name = get_processor_name() or "unknown"
        else:
            supported, device_name = check_device_supported(log=logger)
            status.processor_name = device_name
            status.device_supported = supported
    except Exception:
        pass  # Unknown device — don't block the UI

    # Boot-time initialization tracking from the DispatchQueue.
    queue = getattr(request.app.state, "dispatch_queue", None)
    if queue:
        from ..dispatch import JobStatus

        visible = queue.get_visible_jobs()
        any_pending = any(
            j.status in (JobStatus.PENDING, JobStatus.RUNNING) for j in visible
        )
        any_failed = any(j.status == JobStatus.FAILED for j in visible)
        if any_pending:
            status.init_state = "initializing"
        elif any_failed:
            status.init_state = "degraded"
        else:
            status.init_state = "ready"
        status.init_tasks = [
            InitTaskInfo(name=j.name, status=j.status.value) for j in visible
        ]

    # ── Live download progress ──────────────────────────────────────────
    # Surfaced for whichever model the UI cares about (custom override
    # wins, else the registered default). Looking up by model name keeps
    # us decoupled from concurrent pulls of unrelated models.
    target_model = db.get_setting("custom_model") or _DEFAULT_MODEL_NAME
    progress_dict = _get_download_progress(target_model)
    if progress_dict:
        status.download_progress = DownloadProgress(**progress_dict)

    return status


@router.get("/api/system/tasks", response_model=TaskListResponse)
async def list_tasks(queue=Depends(get_dispatch_queue)):
    """Return visible background tasks (startup initialization, etc.)."""
    if not queue:
        return TaskListResponse(tasks=[])
    visible = queue.get_visible_jobs()
    return TaskListResponse(
        tasks=[
            TaskResponse(
                id=j.id,
                name=j.name,
                status=j.status.value,
                error=None,  # Sanitized: don't expose raw exception strings
            )
            for j in visible
        ]
    )


async def _check_model_status(model_name: str) -> ModelStatus:
    """Check if a model is found, downloaded, and loaded on Lemonade server."""
    status = ModelStatus()
    if not model_name:
        return status
    try:
        import httpx

        base_url = _get_lemonade_base_url()
        _auth = lemonade_auth_headers(resolve_lemonade_api_key())
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Check catalog: is model known and downloaded?
            models_resp = await client.get(
                f"{base_url}/models",
                params={"show_all": "true"},
                headers=_auth,
            )
            if models_resp.status_code == 200:
                model_name_lower = model_name.lower()
                for m in models_resp.json().get("data", []):
                    mid = m.get("id", "").lower()
                    mname = m.get("name", "").lower()
                    if model_name_lower in (mid, mname):
                        status.found = True
                        status.downloaded = m.get("downloaded", False)
                        break

            # Check health: is model currently loaded?
            health_resp = await client.get(f"{base_url}/health", headers=_auth)
            if health_resp.status_code == 200:
                health_data = health_resp.json()
                loaded_model = health_data.get("model_loaded", "")
                if loaded_model and loaded_model.lower() == model_name.lower():
                    status.found = True
                    status.downloaded = True
                    status.loaded = True
                # Also check all_models_loaded list
                for m in health_data.get("all_models_loaded", []):
                    if m.get("model_name", "").lower() == model_name.lower():
                        status.found = True
                        status.downloaded = True
                        status.loaded = True
                        break
    except Exception as e:
        logger.debug("Model status check failed for %s: %s", model_name, e)

    logger.debug(
        "Model status for %s: found=%s, downloaded=%s, loaded=%s",
        model_name,
        status.found,
        status.downloaded,
        status.loaded,
    )
    return status


@router.get("/api/settings", response_model=SettingsResponse)
async def get_settings(db: ChatDatabase = Depends(get_db)):
    """Get current user settings with model status."""
    custom_model = db.get_setting("custom_model")
    context_size_str = db.get_setting("context_size")
    context_size = int(context_size_str) if context_size_str else None
    agent_mode = db.get_setting("agent_mode") or "autonomous"
    logger.debug(
        "Settings loaded: custom_model=%s, context_size=%s, agent_mode=%s",
        custom_model,
        context_size,
        agent_mode,
    )
    model_status = await _check_model_status(custom_model) if custom_model else None
    return SettingsResponse(
        custom_model=custom_model or None,
        model_status=model_status,
        context_size=context_size,
        agent_mode=agent_mode,
    )


@router.put("/api/settings", response_model=SettingsResponse)
async def update_settings(
    request: SettingsUpdateRequest, db: ChatDatabase = Depends(get_db)
):
    """Update user settings.

    Setting ``custom_model`` to an **empty string** clears the override
    and reverts to the default model. Sending ``null`` (or omitting the
    field) is a no-op — by GAIA convention only explicit ``""`` clears.
    Pydantic *can* distinguish an explicit ``null`` from an unset field
    via ``model_fields_set``; we don't lean on that distinction here so
    clients have a single, simple rule (omit to keep, ``""`` to clear).

    Setting ``context_size`` to null resets to the default (32768 tokens).
    Non-null values must be >= 32768.

    Setting ``agent_mode`` controls autonomous behaviour:
    'manual' | 'goal_driven' | 'autonomous' (default).
    """
    if request.custom_model is not None:
        value = request.custom_model.strip() if request.custom_model else None
        if value:
            logger.info("Custom model override set: %s", value)
        else:
            logger.info("Custom model override cleared")
            value = None
        db.set_setting("custom_model", value)

    # Only touch context_size when the field was explicitly included in the request.
    if "context_size" in request.model_fields_set:
        if request.context_size is None:
            db.set_setting("context_size", None)
            logger.info("Context size reset to default (%d)", _MIN_CONTEXT_SIZE)
        else:
            # Pydantic ge=32768 already rejects values below minimum at validation time,
            # but guard here too in case the model is bypassed.
            if request.context_size < _MIN_CONTEXT_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"context_size must be >= {_MIN_CONTEXT_SIZE}",
                )
            db.set_setting("context_size", str(request.context_size))
            logger.info("Context size set: %d tokens", request.context_size)

    if "agent_mode" in request.model_fields_set and request.agent_mode is not None:
        mode = request.agent_mode.strip()
        if mode not in _VALID_AGENT_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"agent_mode must be one of: {sorted(_VALID_AGENT_MODES)}",
            )
        db.set_setting("agent_mode", mode)
        logger.info("Agent mode set: %s", mode)

    custom_model = db.get_setting("custom_model")
    context_size_str = db.get_setting("context_size")
    context_size = int(context_size_str) if context_size_str else None
    agent_mode = db.get_setting("agent_mode") or "autonomous"
    model_status = await _check_model_status(custom_model) if custom_model else None
    return SettingsResponse(
        custom_model=custom_model or None,
        model_status=model_status,
        context_size=context_size,
        agent_mode=agent_mode,
    )


@router.get("/api/health")
async def health(db: ChatDatabase = Depends(get_db)):
    """Health check endpoint."""
    stats = db.get_stats()
    return {
        "status": "ok",
        "service": "gaia-agent-ui",
        "stats": stats,
    }


class LoadModelRequest(BaseModel):
    model_name: str
    # ctx_size must be positive. Lemonade silently accepts 0 or negative values
    # and then fails deep in the backend with no actionable error — enforce at
    # the boundary so callers get a 422 immediately.
    ctx_size: Optional[int] = Field(None, gt=0)


@router.post("/api/system/load-model", status_code=202)
async def load_model_endpoint(body: LoadModelRequest):
    """Trigger loading a model on Lemonade server (non-blocking).

    Returns 202 immediately; loading proceeds in the background.
    Poll /api/system/status to detect when loading completes.
    """
    model_name = body.model_name.strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="model_name must not be empty")

    ctx_size = body.ctx_size if body.ctx_size is not None else _MIN_CONTEXT_SIZE
    payload = {"model_name": model_name, "ctx_size": ctx_size}
    task = asyncio.create_task(
        _lemonade_post("load", payload, timeout=300.0, log_context=f"Load {model_name}")
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": "loading", "model": model_name, "ctx_size": ctx_size}


class DownloadModelRequest(BaseModel):
    model_name: str
    force: bool = False


@router.post("/api/system/download-model", status_code=202)
async def download_model_endpoint(body: DownloadModelRequest):
    """Trigger downloading a model via Lemonade server (non-blocking).

    Returns 202 immediately; the actual download runs in a background task
    that streams Lemonade's ``POST /v1/pull`` SSE events into an in-memory
    progress map. Frontend polls ``/api/system/status`` to read the latest
    ``download_progress`` snapshot — no separate progress endpoint to
    maintain.

    Set ``force=True`` to re-download even if the file already exists
    (repairs corrupted or incomplete downloads).
    """
    model_name = body.model_name.strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="model_name must not be empty")

    task = asyncio.create_task(_stream_lemonade_pull(model_name, body.force))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": "downloading", "model": model_name}
