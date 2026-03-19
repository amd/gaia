# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""System and health-check endpoints for GAIA Agent UI."""

import logging
import os
import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, Depends

from ..database import ChatDatabase
from ..dependencies import get_db
from ..models import ModelStatus, SettingsResponse, SettingsUpdateRequest, SystemStatus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get("/api/system/status", response_model=SystemStatus)
async def system_status():
    """Check system readiness (Lemonade, models, disk space)."""
    status = SystemStatus()

    # Check Lemonade Server
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            base_url = os.environ.get(
                "LEMONADE_BASE_URL", "http://localhost:8000/api/v1"
            )

            # Use /health endpoint to get the actually loaded model
            # (not /models which returns the full catalog of available models)
            health_resp = await client.get(f"{base_url}/health")
            if health_resp.status_code == 200:
                status.lemonade_running = True
                health_data = health_resp.json()
                status.model_loaded = health_data.get("model_loaded") or None
                status.lemonade_version = health_data.get("version")

                # Extract device info from loaded models
                for m in health_data.get("all_models_loaded", []):
                    if m.get("type") == "embedding":
                        status.embedding_model_loaded = True
                    elif m.get("model_name") == status.model_loaded:
                        status.model_device = m.get("device")

                # Fetch model catalog for size, labels, context size
                models_resp = await client.get(f"{base_url}/models")
                if models_resp.status_code == 200:
                    for m in models_resp.json().get("data", []):
                        if m.get("id") == status.model_loaded:
                            status.model_size_gb = m.get("size")
                            status.model_labels = m.get("labels")
                            ctx = m.get("recipe_options", {}).get("ctx_size")
                            if ctx:
                                status.model_context_size = ctx
                        if "embed" in m.get("id", "").lower():
                            status.embedding_model_loaded = True

                # Fetch last inference stats
                try:
                    stats_resp = await client.get(f"{base_url}/stats")
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

                # Fetch GPU info
                try:
                    sysinfo_resp = await client.get(f"{base_url}/system-info")
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
        _LOCAL_ADDRS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")
        is_remote = lemonade_url and not any(
            loc in lemonade_url for loc in _LOCAL_ADDRS
        )

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

        base_url = os.environ.get("LEMONADE_BASE_URL", "http://localhost:8000/api/v1")
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
    logger.debug("Settings loaded: custom_model=%s", custom_model)
    model_status = await _check_model_status(custom_model) if custom_model else None
    return SettingsResponse(
        custom_model=custom_model or None, model_status=model_status
    )


@router.put("/api/settings", response_model=SettingsResponse)
async def update_settings(
    request: SettingsUpdateRequest, db: ChatDatabase = Depends(get_db)
):
    """Update user settings.

    Setting custom_model to an empty string or null clears the override
    and reverts to the default model.
    """
    if request.custom_model is not None:
        value = request.custom_model.strip() if request.custom_model else None
        if value:
            logger.info("Custom model override set: %s", value)
        else:
            logger.info("Custom model override cleared")
            value = None
        db.set_setting("custom_model", value)

    custom_model = db.get_setting("custom_model")
    model_status = await _check_model_status(custom_model) if custom_model else None
    return SettingsResponse(
        custom_model=custom_model or None, model_status=model_status
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
