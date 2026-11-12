# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""System and health-check endpoints for GAIA Agent UI."""

import asyncio
import json
import logging
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gaia.llm.lemonade_client import (
    DEFAULT_CONTEXT_SIZE,
    UnknownUserModelError,
    _is_local_url,
    _model_ids_match,
    cloud_model_provider,
    lemonade_auth_headers,
    pull_request_body,
    registered_model_ids,
    resolve_effective_ctx_size,
    resolve_lemonade_api_key,
    resolve_lemonade_base_url,
    runs_on_npu,
)
from gaia.llm.lemonade_manager import gpu_display_info

from .. import security, warmup
from ..agent_loop import resolve_agent_mode
from ..database import ChatDatabase
from ..dependencies import get_db
from ..models import (
    DownloadProgress,
    InitTaskInfo,
    ModelServerBackendRequest,
    ModelServerInfo,
    ModelStatus,
    RunningAnswer,
    RunningIndexJob,
    SettingsResponse,
    SettingsUpdateRequest,
    SystemStatus,
    UnloadModelResponse,
    WarmUpStatus,
    WorkInProgress,
)
from ..run_manager import run_manager

# "autonomous" is intentionally absent: its observation cycle is unimplemented
# (#2005) and accepting it would silently behave as "goal_driven".
_VALID_AGENT_MODES = {"manual", "goal_driven"}

logger = logging.getLogger(__name__)

# Hold references to background tasks to prevent GC
_background_tasks: set[asyncio.Task] = set()

router = APIRouter(tags=["system"])


def _default_model_name() -> str:
    """The model this machine runs: config ``default_model`` (which ``gaia init``
    sets from the hardware), else Gemma — what agents resolve to as well."""
    from gaia.llm.lemonade_client import resolve_default_chat_model

    return resolve_default_chat_model()


def _norm_model_id(model_id: str) -> str:
    """Lemonade lists a ``user.X`` model as ``X``; compare without the prefix."""
    lowered = (model_id or "").lower()
    return lowered[len("user.") :] if lowered.startswith("user.") else lowered


# Minimum context window (tokens) needed for reliable agent operation.
# Sourced from ``gaia.llm.lemonade_client`` to keep the GAIA-wide ctx
# requirement in a single module (see that module's ``DEFAULT_CONTEXT_SIZE``).
_MIN_CONTEXT_SIZE = DEFAULT_CONTEXT_SIZE


async def _pull_body(model_name: str) -> Dict[str, Any]:
    """``pull_request_body``, asking Lemonade about a ``user.`` id GAIA didn't add.

    Raises ``RuntimeError`` when the model list can't be read, and whatever
    ``pull_request_body`` raises when the model can't be pulled.
    """
    try:
        return pull_request_body(model_name)
    except UnknownUserModelError:
        pass  # Lemonade may list it by name; ask it before refusing.
    import httpx  # pylint: disable=import-outside-toplevel

    base_url = resolve_lemonade_base_url()
    headers = lemonade_auth_headers(resolve_lemonade_api_key(base_url=base_url))
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{base_url}/models", params={"show_all": "true"}, headers=headers
            )
            resp.raise_for_status()
            registered = registered_model_ids(resp.json())
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(
            f"Can't download '{model_name}': the local model server didn't "
            f"say whether it has this model ({exc}). Try again in a moment."
        ) from exc
    return pull_request_body(model_name, registered=registered)


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
    """Return a copy of the latest progress dict, or None.

    A pull registers a custom model as ``user.X`` and Lemonade then lists it as
    ``X``, so either name finds the download.
    """
    with _download_progress_lock:
        entry = _download_progress.get(model_name) or next(
            (
                v
                for k, v in _download_progress.items()
                if _model_ids_match(k, model_name)
            ),
            None,
        )
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
    """Issue a Lemonade ``/v1/load`` for ``model_name`` at the window chat uses.

    Called from ``_stream_lemonade_pull`` only when the requester opts in
    with ``load_after``. The window is ``resolve_ctx_size(model_name)``, the
    one the chat pre-flight asks for, so the first chat doesn't reload it.

    Skipped while a chat is answering, so a download never cuts an answer
    off. A failed load is logged and shows in ``/api/system/status``.
    """
    logger.info("Auto-load after download: %s", model_name)
    # A chat answering on another model keeps it: the new one loads next turn.
    if _chats_answering():
        logger.info("Auto-load of %s skipped: a chat is answering", model_name)
        return
    await asyncio.to_thread(
        _load_through_the_slot, model_name, None, f"Auto-load {model_name}"
    )


def _install_npu_backend(model_name: str) -> None:
    from gaia.llm.lemonade_client import LemonadeClient

    LemonadeClient(verbose=False).ensure_npu_backend(model_name)


async def _stream_lemonade_pull(
    model_name: str, force: bool, load_after: bool = False
) -> None:
    """Issue ``POST /v1/pull?stream=true`` and feed the SSE event stream
    into ``_download_progress[model_name]``.

    Handles three terminal cases (success, server error, connection
    blowup) and always leaves the entry in a state the frontend can
    interpret; an unhandled exception that left ``state="downloading"``
    would peg the UI's spinner forever.
    """
    import httpx  # pylint: disable=import-outside-toplevel

    base_url = resolve_lemonade_base_url()
    starting = {
        "state": "starting",
        "model_name": model_name,
        "percent": 0,
        "file": None,
        "file_index": 0,
        "total_files": 0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "message": None,
    }
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
    headers = lemonade_auth_headers(resolve_lemonade_api_key(base_url=base_url))
    from gaia.llm.lemonade_client import LemonadeClientError

    try:
        try:
            payload = await _pull_body(model_name)
        except (RuntimeError, ValueError) as exc:
            _set_download_progress(
                model_name,
                {**starting, "state": "error", "message": str(exc)},
            )
            logger.error("Pull refused for %s: %s", model_name, exc)
            return
        payload["stream"] = True
        if force:
            payload["force"] = True

        _set_download_progress(model_name, starting)
        logger.info("Pull stream starting: %s (force=%s)", model_name, force)
        if runs_on_npu(model_name):
            # A first install downloads a release and can take minutes.
            _set_download_progress(
                model_name,
                {**starting, "message": "Getting the NPU backend ready…"},
            )
            await asyncio.to_thread(_install_npu_backend, model_name)
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
                            if load_after:
                                # The missing-model banner opts in because
                                # there is no active model for the pull to
                                # displace. Settings downloads deliberately
                                # leave the current model untouched until the
                                # user chooses it.
                                load_task = asyncio.create_task(
                                    _auto_load_after_download(payload["model_name"])
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
    except LemonadeClientError as exc:
        logger.error("NPU backend not ready for %s: %s", model_name, exc)
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
                "message": str(exc),
            },
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


# Last message logged per probe; the UI polls status, so repeats log at debug.
_last_probe_warning: Dict[str, str] = {}


def _probe_failed(status: SystemStatus, probe: str, message: str) -> None:
    """Record a status probe that could not run, so the UI can show it."""
    if _last_probe_warning.get(probe) != message:
        _last_probe_warning[probe] = message
        logger.warning("system status: %s", message)
    else:
        logger.debug("system status: %s", message)
    status.probe_warnings.append(message)


def _probe_ok(probe: str) -> None:
    """Re-arm the warning, so a failure that returns after recovery warns again."""
    _last_probe_warning.pop(probe, None)


def _catalog_entries(body: Any) -> List[Dict[str, Any]]:
    """The model entries of a Lemonade ``/models`` answer.

    Raises:
        ValueError: The answer is not a list of model objects.
    """
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or not all(isinstance(m, dict) for m in data):
        raise ValueError(
            "Lemonade's model list came back in an unexpected shape (it answers, "
            "but not with a list of models). Check that the model server URL in "
            "Settings > Advanced > Model server points at Lemonade."
        )
    return data


def _loaded_chat_entry(
    health: Dict[str, Any],
    loaded: List[Dict[str, Any]],
    expected: Optional[str],
) -> Optional[Dict[str, Any]]:
    """The loaded chat model: the expected one, else the root's, else the first.

    The root ``model_loaded`` names the most recent load, which on a shared
    server is often a speech or embedding model; those never count.
    """
    from gaia.llm.model_server_info import is_chat_model_entry

    chat = [m for m in loaded if is_chat_model_entry(m)]
    for wanted in (expected, health.get("model_loaded")):
        if not wanted:
            continue
        for m in chat:
            if _norm_model_id(m.get("model_name") or "") == _norm_model_id(wanted):
                return m
    return chat[0] if chat else None


# Most the Lemonade probes in /api/system/status may take together.
_STATUS_PROBE_BUDGET_S = 20.0


@router.get("/api/system/status", response_model=SystemStatus)
async def system_status(request: Request, db: ChatDatabase = Depends(get_db)):
    """Check system readiness (Lemonade, models, disk space)."""
    from gaia.config import GaiaConfigError
    from gaia.llm.lemonade_choice import LemonadeChoiceError

    # The config names the Lemonade server, so a broken one is reported, not raised.
    config_errors = (GaiaConfigError, LemonadeChoiceError)
    try:
        status = SystemStatus()
    except config_errors as exc:
        logger.warning("system status: %s", exc)
        status = SystemStatus(lemonade_url=None, config_error=str(exc))
    status.warm_up = WarmUpStatus(**warmup.warm_up_status())
    if db.db_path != ":memory:":
        status.data_dir = str(Path(db.db_path).parent)

    # Resolve how to start Lemonade on THIS host once, here, so every surface
    # renders the same answer. Never fatal: a banner losing its hint is far
    # better than the status endpoint failing.
    try:
        from gaia.llm.lemonade_launcher import describe_start_hint

        # Resolve WITH the required ctx so the rendered command carries it;
        # callers must print it verbatim, never append their own flag.
        hint = describe_start_hint(_MIN_CONTEXT_SIZE)
        status.start_instruction = hint.instruction
        status.start_command = hint.command
    except Exception as exc:  # noqa: BLE001
        logger.warning("system status: could not resolve the start hint: %s", exc)

    # Resolved outside the probe below: its catch-all would report a corrupt
    # config as "Lemonade not running".
    default_model: Optional[str]
    try:
        default_model = _default_model_name()
    except GaiaConfigError as exc:
        # The model is unknown until the config is fixed; the UI shows config_error.
        logger.warning("system status: %s", exc)
        status.config_error = str(exc)
        default_model = None
    # Always named, not only once a model is loaded: the UI's load and download
    # actions target this. None only when an unreadable config hides it.
    status.default_model_name = db.get_setting("custom_model") or default_model
    from .._chat_helpers import _ui_cloud_provider

    status.default_model_is_cloud = bool(_ui_cloud_provider(status.default_model_name))

    # Check Lemonade Server. Each call has a generous timeout (the health
    # check 10 s: a busy LLM answers it slowly); _STATUS_PROBE_BUDGET_S
    # bounds them all together.
    import httpx

    async def _probe_lemonade() -> None:
        # Failures of the supplementary probes below: transport, bad JSON, or
        # an unexpected payload shape.
        probe_errors = (httpx.HTTPError, ValueError, TypeError, AttributeError)

        async with httpx.AsyncClient(timeout=10.0) as client:
            base_url = resolve_lemonade_base_url()
            _auth = lemonade_auth_headers(resolve_lemonade_api_key(base_url=base_url))

            # Use /health endpoint to get the actually loaded model
            # (not /models which returns the full catalog of available models)
            health_resp = await client.get(f"{base_url}/health", headers=_auth)
            if health_resp.status_code == 200:
                status.lemonade_running = True
                health_data = health_resp.json()
                status.lemonade_version = health_data.get("version")
                loaded_models = [
                    m
                    for m in health_data.get("all_models_loaded") or []
                    if isinstance(m, dict)
                ]
                if any(m.get("type") == "embedding" for m in loaded_models):
                    status.embedding_model_loaded = True
                chat_entry = _loaded_chat_entry(
                    health_data, loaded_models, status.default_model_name
                )
                if chat_entry is not None:
                    status.model_loaded = chat_entry.get("model_name") or None
                    status.model_device = chat_entry.get("device")
                    # recipe_options.ctx_size is a config echo, not a
                    # measurement: clamp it to the model's real window (#2992).
                    ctx = (chat_entry.get("recipe_options") or {}).get("ctx_size")
                    # A cloud entry's ctx_size is a 4096 placeholder, not
                    # the provider's window: leave it unknown, never "too small".
                    if chat_entry.get("recipe") == "cloud":
                        ctx = None
                    if ctx is not None:
                        status.model_context_size = resolve_effective_ctx_size(
                            ctx, chat_entry.get("max_context_window")
                        )

                # Catalog for size and labels; its ctx_size is the saved option,
                # not the loaded window.
                model_list_malformed = False
                try:
                    models_resp = await client.get(f"{base_url}/models", headers=_auth)
                    if models_resp.status_code == 200:
                        for m in _catalog_entries(models_resp.json()):
                            if m.get("id") == status.model_loaded:
                                status.model_size_gb = m.get("size")
                                status.model_labels = m.get("labels")
                            if "embed" in (m.get("id") or "").lower():
                                status.embedding_model_loaded = True
                        _probe_ok("models")
                    else:
                        _probe_failed(
                            status,
                            "models",
                            "Could not read Lemonade's model list: it returned "
                            f"HTTP {models_resp.status_code}.",
                        )
                except probe_errors as exc:
                    model_list_malformed = isinstance(exc, ValueError)
                    _probe_failed(
                        status, "models", f"Could not read Lemonade's model list: {exc}"
                    )

                # Validate that the loaded model matches what GAIA Chat (or any
                # registered agent) expects. A custom_model override wins over
                # everything; otherwise the loaded model is "expected" if it
                # matches the baseline default *or* any registered agent's
                # preferred model list. This stops Gaia Lite's 4B (or any other
                # non-default agent model) from tripping a "Wrong model" banner.
                # With the expected model unknown (config_error) neither check runs.
                if status.model_loaded and status.default_model_name:
                    custom_model = db.get_setting("custom_model")
                    loaded_lower = _norm_model_id(status.model_loaded)
                    if custom_model:
                        status.expected_model_loaded = loaded_lower == _norm_model_id(
                            custom_model
                        )
                    else:
                        acceptable = {_norm_model_id(default_model)}
                        registry = getattr(request.app.state, "agent_registry", None)
                        if registry is not None:
                            for reg in registry.list():
                                for m in reg.models:
                                    if m:
                                        acceptable.add(_norm_model_id(m))
                        status.expected_model_loaded = loaded_lower in acceptable
                    # Surface the actual expected name in the response so the
                    # frontend can name it precisely in the warning banner.
                    status.default_model_name = custom_model or default_model

                # When the expected model isn't loaded, check if it is downloaded.
                # Respects the custom_model override.
                # Uses show_all=true to see models that are in the catalog but not
                # yet pulled to disk. A list already named malformed above
                # would only warn twice.
                expected_missing = (
                    not status.model_loaded or not status.expected_model_loaded
                )
                if (
                    expected_missing
                    and status.default_model_name
                    and not status.default_model_is_cloud
                    and not model_list_malformed
                ):
                    _target = status.default_model_name
                    try:
                        catalog_resp = await client.get(
                            f"{base_url}/models",
                            params={"show_all": "true"},
                            timeout=5.0,
                            headers=_auth,
                        )
                        if catalog_resp.status_code != 200:
                            _probe_failed(
                                status,
                                "catalog",
                                f"Could not check whether {_target} is downloaded: "
                                f"Lemonade's model catalog returned HTTP "
                                f"{catalog_resp.status_code}.",
                            )
                        else:
                            default_lower = _norm_model_id(_target)
                            for m in _catalog_entries(catalog_resp.json()):
                                if _norm_model_id(m.get("id") or "") == default_lower:
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
                            _probe_ok("catalog")
                    except probe_errors as exc:
                        _probe_failed(
                            status,
                            "catalog",
                            f"Could not check whether {_target} is downloaded: {exc}",
                        )

                # Validate context size sufficiency only when we have a real,
                # positive reading. A ctx of 0 means "not yet measured" —
                # Lemonade reports 0/absent until the model's real context is
                # populated — so treat it as unknown and keep the safe default
                # (don't warn) rather than firing a false "too small" alarm on
                # load. Once a genuine ctx arrives, the next poll re-evaluates.
                # Another model's window says nothing about chat: indexing loads
                # a small vision model, and chat reloads its own at the right size.
                if status.model_context_size and status.expected_model_loaded:
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
                        _probe_ok("stats")
                    else:
                        _probe_failed(
                            status,
                            "stats",
                            "Could not read inference stats: Lemonade returned "
                            f"HTTP {stats_resp.status_code}.",
                        )
                except probe_errors as exc:
                    _probe_failed(
                        status, "stats", f"Could not read inference stats: {exc}"
                    )

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
                        status.gpu_name, status.gpu_vram_gb = gpu_display_info(devices)
                        for key, dev in devices.items():
                            if "npu" in key.lower() and isinstance(dev, dict):
                                if dev.get("available"):
                                    detected.append("npu")
                        status.detected_devices = detected
                        _probe_ok("devices")
                    else:
                        _probe_failed(
                            status,
                            "devices",
                            "Could not read device info: Lemonade returned "
                            f"HTTP {sysinfo_resp.status_code}.",
                        )
                except probe_errors as exc:
                    _probe_failed(
                        status, "devices", f"Could not read device info: {exc}"
                    )
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
                else:
                    status.lemonade_error = "Lemonade health query failed"

    # The probes run one after another, each with its own timeout; this
    # bounds them together so the endpoint always answers.
    try:
        await asyncio.wait_for(_probe_lemonade(), _STATUS_PROBE_BUDGET_S)
        status.lemonade_probe = "ok" if status.lemonade_running else "error"
        if status.lemonade_running:
            _probe_ok("lemonade")
    except asyncio.TimeoutError:
        if status.lemonade_running:
            # Health answered; only the extra probes ran out of time.
            status.lemonade_probe = "ok"
            _probe_failed(
                status,
                "lemonade",
                f"Some model-server details took over {_STATUS_PROBE_BUDGET_S:g} s "
                "and were skipped",
            )
        else:
            logger.warning(
                "system status: Lemonade did not answer within %gs",
                _STATUS_PROBE_BUDGET_S,
            )
            status.lemonade_probe = "timed_out"
            status.lemonade_error = (
                f"Lemonade did not answer within {_STATUS_PROBE_BUDGET_S:g} s; "
                "it may be busy loading a model."
            )
    except httpx.ConnectError as exc:
        # Refused: nothing is listening, a confirmed outage, so keep the
        # actionable "not responding" banner rather than an ambiguous probe.
        logger.debug("system status: Lemonade is not running: %s", exc)
        status.lemonade_running = False
        status.lemonade_probe = "down"
    except httpx.ConnectTimeout as exc:
        status.lemonade_running = False
        if _is_local_url(resolve_lemonade_base_url()):
            # A local server that is up but not accepting (full backlog,
            # wedged) times out the same way; say busy, never "start it".
            logger.warning("system status: local Lemonade is not accepting: %s", exc)
            status.lemonade_probe = "timed_out"
            status.lemonade_error = "Lemonade is not accepting connections right now"
        else:
            logger.debug("system status: remote Lemonade is not reachable: %s", exc)
            status.lemonade_probe = "down"
    except httpx.TimeoutException as exc:
        # Connected, then no answer in time: busy, not down.
        logger.warning("system status: Lemonade did not answer in time: %s", exc)
        status.lemonade_running = False
        status.lemonade_probe = "timed_out"
        status.lemonade_error = "Lemonade did not answer in time"
    except config_errors as exc:
        # Which server to probe is unknown until the config is fixed.
        status.config_error = str(exc)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("system status: Lemonade health query failed: %s", exc)
        status.lemonade_running = False
        status.lemonade_probe = "error"
        status.lemonade_error = "Lemonade health query failed"

    # Active profile from persistent config (#1220)
    # GaiaConfigError already imported above, in this same function.
    from gaia.config import GaiaConfig

    try:
        status.active_profile = GaiaConfig.load().profile
    except GaiaConfigError as exc:
        logger.warning("system status: %s", exc)
        status.config_error = str(exc)

    # The last Settings or post-download load, if it failed.
    if _last_load_failure:
        _probe_failed(status, "load", _last_load_failure)
    else:
        _probe_ok("load")

    # Disk space
    # Access shutil through gaia.ui.server so test patches on
    # "gaia.ui.server.shutil.disk_usage" take effect correctly.
    try:
        _shutil = sys.modules.get("gaia.ui.server", sys.modules[__name__])
        _shutil_mod = getattr(_shutil, "shutil", shutil)
        usage = _shutil_mod.disk_usage(Path.home())
        status.disk_space_gb = round(usage.free / (1024**3), 1)
        _probe_ok("disk")
    except OSError as exc:
        _probe_failed(status, "disk", f"Could not read free disk space: {exc}")

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
    from gaia.ui.setup_runner import desktop_setup_error, is_initialized

    status.initialized = is_initialized()
    status.setup_error = desktop_setup_error()

    from gaia.device import get_processor_name

    status.processor_name = await asyncio.to_thread(get_processor_name) or None

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
    target_model = db.get_setting("custom_model") or default_model
    progress_dict = _get_download_progress(target_model)
    if progress_dict:
        status.download_progress = DownloadProgress(**progress_dict)

    return status


@router.post("/api/system/warm-up", response_model=WarmUpStatus)
async def request_warm_up(fresh: bool = False):
    """Prime the prompt a new chat's first turn sends; returns the warm-up state.

    Called when the user opens a new chat. Debounced and single-flight, so a
    repeat call while one runs, or just after one finished, changes nothing.
    ``?fresh=true`` retries a picked model's load: it skips the debounce and
    runs after a warm-up already under way.
    """
    if fresh:
        warmup.request_warm_up("model pick retry", fresh=True)
    else:
        warmup.request_warm_up("new chat")
    return WarmUpStatus(**warmup.warm_up_status())


@router.get("/api/system/warm-up", response_model=WarmUpStatus)
async def get_warm_up():
    """The warm-up state alone, cheap enough to poll while one runs."""
    return WarmUpStatus(**warmup.warm_up_status())


async def _check_model_status(model_name: str) -> ModelStatus:
    """Check if a model is found, downloaded, and loaded on Lemonade server.

    ``checked`` stays False when the model list couldn't be read, so "not
    found" is never claimed for a server that didn't answer.
    """
    status = ModelStatus()
    if not model_name:
        return status
    import httpx

    try:
        base_url = resolve_lemonade_base_url()
        _auth = lemonade_auth_headers(resolve_lemonade_api_key(base_url=base_url))
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Check catalog: is model known and downloaded?
            models_resp = await client.get(
                f"{base_url}/models",
                params={"show_all": "true"},
                headers=_auth,
            )
            listed: Optional[List[Dict[str, Any]]] = None
            if models_resp.status_code == 200:
                try:
                    listed = _catalog_entries(models_resp.json())
                except ValueError as e:
                    logger.info("Model list for %s unreadable: %s", model_name, e)
            if listed is not None:
                status.checked = True
                model_name_lower = model_name.lower()
                for m in listed:
                    mid = str(m.get("id") or "")
                    mname = str(m.get("name") or "").lower()
                    if _model_ids_match(mid, model_name) or model_name_lower == mname:
                        status.found = True
                        status.downloaded = bool(m.get("downloaded", False))
                        break

            # Check health: is model currently loaded?
            health_resp = await client.get(f"{base_url}/health", headers=_auth)
            health_data = health_resp.json() if health_resp.status_code == 200 else None
            if isinstance(health_data, dict):
                loaded_model = health_data.get("model_loaded")
                if isinstance(loaded_model, str) and _model_ids_match(
                    loaded_model, model_name
                ):
                    status.found = True
                    status.downloaded = True
                    status.loaded = True
                # Also check all_models_loaded list
                loaded_list = health_data.get("all_models_loaded")
                for m in loaded_list if isinstance(loaded_list, list) else []:
                    if isinstance(m, dict) and _model_ids_match(
                        str(m.get("model_name") or ""), model_name
                    ):
                        status.found = True
                        status.downloaded = True
                        status.loaded = True
                        break
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as e:
        # Unchecked, which Settings says in words; InvalidURL isn't an HTTPError.
        logger.info("Could not check model %s on the model server: %s", model_name, e)

    logger.debug(
        "Model status for %s: found=%s, downloaded=%s, loaded=%s, checked=%s",
        model_name,
        status.found,
        status.downloaded,
        status.loaded,
        status.checked,
    )
    return status


def _resolve_dynamic_tools_setting() -> tuple[bool, bool]:
    """Return ``(effective, locked)`` for dynamic tool loading.

    ``GAIA_DYNAMIC_TOOLS`` wins when set (``locked=True`` ⇒ the frontend
    reflects the effective value and disables the toggle, never a silent
    no-op); otherwise ``dynamic_tools`` in ``~/.gaia/config.json`` decides, the
    one place every agent process reads, the flagship's sidecar included.
    Delegates the env parse to ``dynamic_tools_env_override`` so the UI and
    ``ChatAgent._resolve_dynamic_tools_enabled`` share one truthy set.
    """
    # Function-local import keeps the agents layer off the router's import path
    # (no cycle, no module-load cost). The helper lives in the core tool_loader
    # (not an agent wheel) so this core UI setting works without one installed.
    from gaia.agents.base.tool_loader import dynamic_tools_env_override
    from gaia.config import GaiaConfig

    override = dynamic_tools_env_override()
    if override is not None:
        return override, True
    return GaiaConfig.load().dynamic_tools, False


@router.get("/api/settings", response_model=SettingsResponse)
async def get_settings(db: ChatDatabase = Depends(get_db)):
    """Get current user settings with model status."""
    custom_model = db.get_setting("custom_model")
    context_size_str = db.get_setting("context_size")
    context_size = int(context_size_str) if context_size_str else None
    agent_mode = resolve_agent_mode(db)
    dynamic_tools, dynamic_tools_locked = _resolve_dynamic_tools_setting()
    logger.debug(
        "Settings loaded: custom_model=%s, context_size=%s, agent_mode=%s, "
        "dynamic_tools=%s (locked=%s)",
        custom_model,
        context_size,
        agent_mode,
        dynamic_tools,
        dynamic_tools_locked,
    )
    model_status = await _check_model_status(custom_model) if custom_model else None
    from gaia.llm.local_only import cloud_blocked

    return SettingsResponse(
        custom_model=custom_model or None,
        model_status=model_status,
        context_size=context_size,
        agent_mode=agent_mode,
        dynamic_tools=dynamic_tools,
        dynamic_tools_locked=dynamic_tools_locked,
        network_policy="local_ai_only" if cloud_blocked() else "cloud_allowed",
    )


@router.put("/api/settings", response_model=SettingsResponse)
async def update_settings(
    request: SettingsUpdateRequest,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
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

    Setting ``agent_mode`` controls background behaviour:
    'manual' | 'goal_driven' (default). 'autonomous' is rejected until its
    observation cycle ships (#2005) — it would silently behave as
    'goal_driven'.
    """
    if request.custom_model is not None:
        value = request.custom_model.strip() if request.custom_model else None
        from gaia.config import GaiaConfig, GaiaConfigError

        from .providers import (
            clear_restore_notice,
            provider_of,
            refuse_blocked_cloud_model,
        )

        if value:
            refuse_blocked_cloud_model(value)
            logger.info("Custom model override set: %s", value)
        else:
            logger.info("Custom model override cleared")
            value = None
        # last_model is what the flagship sidecar warms and the next start restores.
        try:
            GaiaConfig.save_keys(
                {
                    "last_model": value,
                    "last_provider": provider_of(value) if value else None,
                }
            )
        except GaiaConfigError as e:
            raise HTTPException(
                status_code=500,
                detail=f"The custom model was not saved: {e} Then save it again.",
            ) from e
        db.set_setting("custom_model", value)
        clear_restore_notice()

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
        if mode == "autonomous":
            raise HTTPException(
                status_code=400,
                detail=(
                    "agent_mode 'autonomous' is not implemented yet: its "
                    "observation cycle (docs/spec/autonomous-agent-mode.md "
                    "§6.7) has not shipped, so it would behave identically "
                    "to 'goal_driven'. Use 'goal_driven' instead. Tracked in "
                    "https://github.com/amd/gaia/issues/2005."
                ),
            )
        if mode not in _VALID_AGENT_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"agent_mode must be one of: {sorted(_VALID_AGENT_MODES)}",
            )
        db.set_setting("agent_mode", mode)
        logger.info("Agent mode set: %s", mode)

    if (
        "dynamic_tools" in request.model_fields_set
        and request.dynamic_tools is not None
    ):
        # Persist the user's intent even while GAIA_DYNAMIC_TOOLS locks the
        # effective value — so their choice applies once the env var is unset.
        from gaia.config import GaiaConfig

        GaiaConfig.save_key("dynamic_tools", request.dynamic_tools)
        logger.info("Dynamic tools setting persisted: %s", request.dynamic_tools)

    policy_warnings: List[str] = []
    policy_notices: List[Dict[str, Any]] = []
    if (
        "network_policy" in request.model_fields_set
        and request.network_policy is not None
    ):
        security.require_local_pc(http_request)
        from gaia.llm import local_only

        try:
            result = local_only.apply_policy(request.network_policy)
        except (ValueError, OSError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        policy_warnings = result["warnings"]
        policy_notices = result["notices"]
        if result["policy"] is local_only.NetworkPolicy.LOCAL_AI_ONLY:
            # Preserve a local custom model; only a cloud selection would keep
            # the UI pointing at a model the shared cleanup has removed.
            from .._chat_helpers import _ui_cloud_provider

            custom = (db.get_setting("custom_model") or "").strip()
            if custom and (
                _ui_cloud_provider(custom) or custom.lower().startswith("claude")
            ):
                db.set_setting("custom_model", None)
        logger.info("Network policy set: %s", result["policy"].value)

    custom_model = db.get_setting("custom_model")
    context_size_str = db.get_setting("context_size")
    context_size = int(context_size_str) if context_size_str else None
    agent_mode = resolve_agent_mode(db)
    dynamic_tools, dynamic_tools_locked = _resolve_dynamic_tools_setting()
    model_status = await _check_model_status(custom_model) if custom_model else None
    from gaia.llm.local_only import cloud_blocked

    return SettingsResponse(
        custom_model=custom_model or None,
        model_status=model_status,
        context_size=context_size,
        agent_mode=agent_mode,
        dynamic_tools=dynamic_tools,
        dynamic_tools_locked=dynamic_tools_locked,
        network_policy="local_ai_only" if cloud_blocked() else "cloud_allowed",
        policy_warnings=policy_warnings,
        policy_notices=policy_notices,
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


def _read_model_server() -> ModelServerInfo:
    """Read Lemonade's /health and /system-info into the Model server panel.

    Unreachable is an answer, not an exception: the panel shows one line.
    """
    from gaia.config import GaiaConfig
    from gaia.llm.lemonade_client import (
        LemonadeClient,
        LemonadeClientError,
        _read_embedded_lemonade_state,
    )
    from gaia.llm.model_server_info import describe_model_server, server_owner

    preferred = GaiaConfig.load().llamacpp_backend
    client = LemonadeClient(verbose=False)
    base_url = client.base_url
    state = _read_embedded_lemonade_state()
    owner = server_owner(base_url, state.get("port") if state else None)
    try:
        health = client.health_check(timeout=5)
    except LemonadeClientError as exc:
        logger.info("model server: %s did not answer: %s", base_url, exc)
        return ModelServerInfo(
            reachable=False,
            error=f"Lemonade is not answering at {base_url}.",
            base_url=base_url,
            owner=owner,
            preferred_backend=preferred,
        )
    system_info = None
    system_info_error = None
    try:
        system_info = client.get_system_info(timeout=5)
    except LemonadeClientError as exc:
        system_info_error = f"Could not read Lemonade's system info: {exc}"
        logger.warning("model server: %s", system_info_error)
    info = describe_model_server(
        base_url=base_url,
        owner=owner,
        health=health if isinstance(health, dict) else {},
        system_info=system_info if isinstance(system_info, dict) else None,
        preferred_backend=preferred,
        is_cloud_model=cloud_model_provider,
    )
    return ModelServerInfo(**info, system_info_error=system_info_error)


def _config_http_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=500, detail=str(exc))


@router.get("/api/system/model-server", response_model=ModelServerInfo)
async def model_server():
    """Which Lemonade GAIA uses, where, and what backend each loaded model runs on."""
    from gaia.config import GaiaConfigError

    try:
        return await asyncio.to_thread(_read_model_server)
    except GaiaConfigError as exc:
        raise _config_http_error(exc) from exc


@router.put("/api/system/model-server/backend", response_model=ModelServerInfo)
async def set_model_server_backend(body: ModelServerBackendRequest):
    """Save the llama.cpp backend for chat models; it applies on the next load.

    Only a backend GAIA has tested and Lemonade can run on this machine is accepted.
    """
    from gaia.config import GaiaConfig, GaiaConfigError

    choice = body.backend.strip().lower()
    try:
        if choice != "auto":
            current = await asyncio.to_thread(_read_model_server)
            if not current.reachable:
                raise HTTPException(status_code=503, detail=current.error)
            usable = sorted(b.name for b in current.available_backends)
            if choice not in usable:
                offered = ", ".join(usable) if usable else "none on this machine"
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"llama.cpp's '{choice}' backend isn't one GAIA offers here: "
                        f"it offers only backends it has tested that this machine "
                        f"can run ({offered})."
                    ),
                )
        await asyncio.to_thread(GaiaConfig.save_key, "llamacpp_backend", choice)
        return await asyncio.to_thread(_read_model_server)
    except GaiaConfigError as exc:
        raise _config_http_error(exc) from exc


class LoadModelRequest(BaseModel):
    model_name: str
    # ctx_size must be positive. Lemonade silently accepts 0 or negative values
    # and then fails deep in the backend with no actionable error — enforce at
    # the boundary so callers get a 422 immediately.
    ctx_size: Optional[int] = Field(None, gt=0)
    # Load even while a chat is answering (the user confirmed the interruption).
    force: bool = False


# The last background model load's failure, until a load succeeds.
_last_load_failure: Optional[str] = None


def _load_through_the_slot(
    model_name: str, ctx_size: Optional[int], log_context: str
) -> None:
    """Load a model the way a chat turn does: broker lease, then the load lock.

    ``LemonadeClient.load_model`` takes the lease, checks free memory and adds
    the chosen llama.cpp backend; ``model_load_lock`` keeps it from racing a
    chat's own pre-flight load in this process. A ``ctx_size`` of None loads
    at ``resolve_ctx_size(model_name)``.
    """
    from gaia.config import GaiaConfigError
    from gaia.daemon.broker_client import BrokerUnavailableError, model_lease
    from gaia.llm.lemonade_client import (
        LemonadeClient,
        LemonadeClientError,
        resolve_ctx_size,
    )
    from gaia.ui._chat_helpers import model_load_lock, note_model_loaded

    try:
        if ctx_size is None:
            ctx_size = resolve_ctx_size(model_name)
        logger.info("%s at ctx_size=%d", log_context, ctx_size)
        # The order every load in this process uses: lease, then the lock.
        with model_lease(model_name, priority="interactive"), model_load_lock:
            LemonadeClient(verbose=False).load_model(
                model_name, ctx_size=ctx_size, prompt=False, timeout=600
            )
            note_model_loaded(model_name)
    except (
        LemonadeClientError,
        BrokerUnavailableError,
        GaiaConfigError,
        OSError,
    ) as exc:
        global _last_load_failure  # pylint: disable=global-statement
        logger.error("%s failed: %s", log_context, exc)
        # Shown by /api/system/status until a load succeeds.
        _last_load_failure = f"{log_context} failed: {exc}"
        return
    _clear_load_failure()
    logger.info("%s succeeded", log_context)


def _clear_load_failure() -> None:
    global _last_load_failure  # pylint: disable=global-statement
    _last_load_failure = None


def _chats_answering() -> list:
    """Chats with a turn in flight in this server right now."""
    from gaia.ui.run_manager import run_manager

    return run_manager.active_sessions()


@router.post("/api/system/load-model", status_code=202)
async def load_model_endpoint(body: LoadModelRequest):
    """Trigger loading a model on Lemonade server (non-blocking).

    Returns 202 immediately; loading proceeds in the background.
    Poll /api/system/status to detect when loading completes.
    """
    model_name = body.model_name.strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="model_name must not be empty")

    answering = _chats_answering()
    if answering and not body.force:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{len(answering)} chat(s) are answering right now. Loading "
                f"{model_name} now competes with them for memory and the "
                "model, and can slow or stop their answers. Wait for them to "
                "finish, or load anyway (force=true)."
            ),
        )
    ctx_size = body.ctx_size
    if ctx_size is None:
        from gaia.config import GaiaConfigError
        from gaia.llm.lemonade_client import LemonadeClientError, resolve_ctx_size

        # The window a chat on this model asks for, so its first turn doesn't reload.
        try:
            ctx_size = await asyncio.to_thread(resolve_ctx_size, model_name)
        except (LemonadeClientError, GaiaConfigError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not size the context window for {model_name}: {exc}",
            ) from exc
    task = asyncio.create_task(
        asyncio.to_thread(
            _load_through_the_slot, model_name, ctx_size, f"Load {model_name}"
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": "loading", "model": model_name, "ctx_size": ctx_size}


def _running_index_ids(request: Request) -> List[str]:
    """Documents being indexed now: uploads in the background and monitor re-indexes."""
    ids = list(request.app.state.indexing_tasks)
    monitor = getattr(request.app.state, "document_monitor", None)
    if monitor is not None:
        ids.extend(d for d in monitor.reindexing_docs if d not in ids)
    return ids


@router.get("/api/system/work-in-progress", response_model=WorkInProgress)
async def work_in_progress(request: Request, db: ChatDatabase = Depends(get_db)):
    """Answers still being written, documents still being indexed, and setup still downloading.

    The desktop app asks this before it quits or frees the model's memory,
    so it can warn that the work will be lost.
    """
    from gaia.ui.setup_runner import runner as setup_runner

    setup_status = setup_runner.status() or {}
    answers = []
    for sid in run_manager.active_sessions():
        session = db.get_session(sid)
        answers.append(
            RunningAnswer(session_id=sid, title=session["title"] if session else None)
        )
    indexing = []
    for doc_id in _running_index_ids(request):
        doc = db.get_document(doc_id)
        indexing.append(
            RunningIndexJob(
                document_id=doc_id, filename=doc["filename"] if doc else None
            )
        )
    return WorkInProgress(
        answers=answers,
        indexing=indexing,
        # A model download started from Retry or Settings runs outside a setup run.
        setup=setup_status.get("state") in ("running", "verifying")
        or any(
            m.get("state") in ("queued", "downloading", "verifying")
            for m in setup_status.get("models") or []
        ),
    )


class UnloadModelRequest(BaseModel):
    model_name: str = Field(min_length=1)


@router.post("/api/system/unload-model", response_model=UnloadModelResponse)
async def unload_model_endpoint(body: UnloadModelRequest, request: Request):
    """Unload one model from the model server to free its memory.

    Only the named model: the embedder and any other loaded model stay. The
    next message loads it again. Refused with ``409`` while an answer is
    running or a document is indexing in this app.
    """
    if run_manager.active_sessions():
        raise HTTPException(
            status_code=409,
            detail="An answer is still being written. Stop it or wait for it "
            "to finish, then free memory.",
        )
    if _running_index_ids(request):
        raise HTTPException(
            status_code=409,
            detail="A document is still being indexed. Wait for it to finish, "
            "then free memory.",
        )
    from gaia.config import GaiaConfigError
    from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError

    try:
        await asyncio.to_thread(
            lambda: LemonadeClient(verbose=False).unload_model(body.model_name)
        )
    except (GaiaConfigError, LemonadeClientError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"The model server did not unload {body.model_name}: {exc}",
        ) from exc
    logger.info("Unloaded %s from the model server on request", body.model_name)
    return UnloadModelResponse(status="unloaded")


class DownloadModelRequest(BaseModel):
    model_name: str
    force: bool = False
    load_after: bool = False


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
    try:
        await _pull_body(model_name)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    task = asyncio.create_task(
        _stream_lemonade_pull(model_name, body.force, body.load_after)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": "downloading", "model": model_name}
