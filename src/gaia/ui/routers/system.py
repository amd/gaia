# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
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
from ..models import SystemStatus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get("/api/system/status", response_model=SystemStatus)
async def system_status():
    """Check system readiness (Lemonade, models, disk space)."""
    status = SystemStatus()

    # Check Lemonade Server
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as client:
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

                # Check loaded models list for embedding model
                for m in health_data.get("all_models_loaded", []):
                    if m.get("type") == "embedding":
                        status.embedding_model_loaded = True
                        break

                # If no embedding found in loaded models,
                # fall back to checking the model catalog
                if not status.embedding_model_loaded:
                    models_resp = await client.get(f"{base_url}/models")
                    if models_resp.status_code == 200:
                        for m in models_resp.json().get("data", []):
                            if "embed" in m.get("id", "").lower():
                                status.embedding_model_loaded = True
                                break
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

    return status


@router.get("/api/health")
async def health(db: ChatDatabase = Depends(get_db)):
    """Health check endpoint."""
    stats = db.get_stats()
    return {
        "status": "ok",
        "service": "gaia-agent-ui",
        "stats": stats,
    }
