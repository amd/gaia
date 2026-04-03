# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""System and health-check endpoints for GAIA Agent UI."""

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..database import ChatDatabase
from ..dependencies import get_db
from ..models import ModelStatus, SettingsResponse, SettingsUpdateRequest, SystemStatus

_VALID_AGENT_MODES = {"manual", "goal_driven", "autonomous"}

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

# Default model required for GAIA Chat agent
_DEFAULT_MODEL_NAME = "Qwen3.5-35B-A3B-GGUF"
# Minimum context window (tokens) needed for reliable agent operation.
# Must match DEFAULT_CONTEXT_SIZE in gaia.llm.lemonade_manager.
_MIN_CONTEXT_SIZE = 32768


def _get_lemonade_base_url() -> str:
    """Return the Lemonade Server API base URL from environment or default."""
    return os.environ.get("LEMONADE_BASE_URL", "http://localhost:8000/api/v1")


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
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base_url}/{path}", json=payload)
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


@router.get("/api/system/status", response_model=SystemStatus)
async def system_status(db: ChatDatabase = Depends(get_db)):
    """Check system readiness (Lemonade, models, disk space)."""
    status = SystemStatus()

    # Check Lemonade Server
    # Use a generous timeout (10s) because when the LLM is handling many
    # parallel requests it may take a while to respond to the health check.
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            base_url = _get_lemonade_base_url()

            # Derive the Lemonade web UI URL (scheme://host:port without /api/v1)
            try:
                _parsed = urlparse(base_url)
                status.lemonade_url = f"{_parsed.scheme}://{_parsed.netloc}"
            except Exception:
                pass  # Keep the default "http://localhost:8000"

            # Use /health endpoint to get the actually loaded model
            # (not /models which returns the full catalog of available models)
            health_resp = await client.get(f"{base_url}/health")
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
                models_resp = await client.get(f"{base_url}/models")
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

                # Validate that the loaded model matches what GAIA Chat expects.
                # Respects custom_model override if the user has configured one.
                if status.model_loaded:
                    custom_model = db.get_setting("custom_model")
                    expected = (custom_model or _DEFAULT_MODEL_NAME).lower()
                    status.expected_model_loaded = (
                        status.model_loaded.lower() == expected
                    )
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
                        )
                        if catalog_resp.status_code == 200:
                            _custom = db.get_setting("custom_model")
                            default_lower = (_custom or _DEFAULT_MODEL_NAME).lower()
                            for m in catalog_resp.json().get("data", []):
                                if m.get("id", "").lower() == default_lower:
                                    status.model_downloaded = m.get("downloaded", False)
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
                    stats_resp = await client.get(f"{base_url}/stats", timeout=3.0)
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

                # Fetch GPU info (short timeout — supplementary info)
                try:
                    sysinfo_resp = await client.get(
                        f"{base_url}/system-info", timeout=3.0
                    )
                    if sysinfo_resp.status_code == 200:
                        devices = sysinfo_resp.json().get("devices", {})
                        for key, dev in devices.items():
                            if "gpu" in key.lower() and isinstance(dev, dict):
                                status.gpu_name = dev.get("name")
                                status.gpu_vram_gb = dev.get("vram_gb")
                                break
                except Exception:
                    pass
            else:
                # Fall back to /models if /health isn't available
                resp = await client.get(f"{base_url}/models")
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

    return status


async def _check_model_status(model_name: str) -> ModelStatus:
    """Check if a model is found, downloaded, and loaded on Lemonade server."""
    status = ModelStatus()
    if not model_name:
        return status
    try:
        import httpx

        base_url = _get_lemonade_base_url()
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Check catalog: is model known and downloaded?
            models_resp = await client.get(
                f"{base_url}/models", params={"show_all": "true"}
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
            health_resp = await client.get(f"{base_url}/health")
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

    Setting custom_model to an empty string or null clears the override
    and reverts to the default model.

    Setting context_size to null resets to the default (32768 tokens).
    Non-null values must be >= 32768.

    Setting agent_mode controls autonomous behaviour:
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
    ctx_size: Optional[int] = None


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
    asyncio.create_task(
        _lemonade_post("load", payload, timeout=300.0, log_context=f"Load {model_name}")
    )
    return {"status": "loading", "model": model_name, "ctx_size": ctx_size}


class DownloadModelRequest(BaseModel):
    model_name: str
    force: bool = False


@router.post("/api/system/download-model", status_code=202)
async def download_model_endpoint(body: DownloadModelRequest):
    """Trigger downloading a model via Lemonade server (non-blocking).

    Returns 202 immediately; download proceeds in the background.
    Poll /api/system/status to detect when the model becomes available.
    Set force=True to re-download even if the file already exists (repairs
    corrupted or incomplete downloads).
    """
    model_name = body.model_name.strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="model_name must not be empty")

    payload: dict = {"model_name": model_name}
    if body.force:
        payload["force"] = True
    asyncio.create_task(
        _lemonade_post(
            "pull", payload, timeout=7200.0, log_context=f"Download {model_name}"
        )
    )
    return {"status": "downloading", "model": model_name}
