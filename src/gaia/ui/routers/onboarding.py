# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""First-run onboarding endpoints for the GAIA Agent UI (#1726, #1727).

Two concerns live here:

* **Hardware pre-flight** (``GET /api/onboarding/preflight``) — scans RAM, disk,
  NPU, and GPU VRAM, classifies a hardware *tier*, recommends a first-run
  profile/model, and runs the shared :func:`gaia.hub.compatibility.check_compatibility`
  so the wizard shows the *same* blockers/warnings the hub install path would.
  Detected NPU/GPU are fed into the checker so a no-NPU machine gets a real
  warning instead of "couldn't verify" (#1727).

* **First-run state** (``GET /api/onboarding/status`` / ``POST
  /api/onboarding/complete``) — reads and writes the ``~/.gaia/chat/initialized``
  marker the rest of GAIA already consults (``system_status.initialized``,
  ``agent_loop`` startup gate), so completing the wizard once suppresses it for
  good and a CLI ``gaia init`` and the UI wizard agree on "is this set up".

No silent fallbacks: a shortage of disk is a hard blocker with the exact GB
figures; anything we cannot probe becomes a named warning, never a silent pass.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from gaia.hub.compatibility import check_compatibility
from gaia.hub.manifest import Requirements
from gaia.llm.lemonade_client import lemonade_auth_headers, resolve_lemonade_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["onboarding"])

# The light, multimodal default every first-run profile pulls (see
# ``INIT_PROFILES`` in ``gaia.installer.init_command``). Kept in sync with
# ``gaia.ui.routers.system._DEFAULT_MODEL_NAME``.
_RECOMMENDED_MODEL = "Gemma-4-E4B-it-GGUF"
# Disk the recommended first-run download needs (model + embedder + headroom).
# Deliberately conservative so we block *before* a half-finished pull fills the
# disk rather than after.
_RECOMMENDED_DISK_GB = 6.0
# RAM below which the recommended model is likely to swap/fail to load.
_RECOMMENDED_MEMORY_GB = 8.0

_INIT_MARKER = Path.home() / ".gaia" / "chat" / "initialized"


def _get_lemonade_base_url() -> str:
    return os.environ.get("LEMONADE_BASE_URL", "http://localhost:13305/api/v1")


async def _probe_lemonade_devices() -> Dict[str, Any]:
    """Best-effort NPU/GPU probe via Lemonade's ``/system-info``.

    Returns ``{"lemonade_running": bool, "npu_detected": Optional[bool],
    "gpu_name": Optional[str], "gpu_vram_gb": Optional[float]}``. A ``None`` for
    a device means we could not determine it (Lemonade down, or it did not
    report that device) — the caller turns that into a "couldn't verify"
    warning rather than assuming the hardware is present.
    """
    result: Dict[str, Any] = {
        "lemonade_running": False,
        "npu_detected": None,
        "gpu_name": None,
        "gpu_vram_gb": None,
    }
    try:
        import httpx  # pylint: disable=import-outside-toplevel

        base_url = _get_lemonade_base_url()
        auth = lemonade_auth_headers(resolve_lemonade_api_key())
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/system-info", headers=auth)
            if resp.status_code != 200:
                return result
            result["lemonade_running"] = True
            devices = resp.json().get("devices", {})
            # Once Lemonade reports its device table we *do* know whether an NPU
            # is present — so absence becomes a definite False, not None.
            npu_seen = False
            for key, dev in devices.items():
                if not isinstance(dev, dict):
                    continue
                if "gpu" in key.lower():
                    result["gpu_name"] = dev.get("name") or result["gpu_name"]
                    vram = dev.get("vram_gb")
                    if vram is not None:
                        result["gpu_vram_gb"] = float(vram)
                if "npu" in key.lower():
                    npu_seen = True
                    if dev.get("available"):
                        result["npu_detected"] = True
            if result["npu_detected"] is None and npu_seen:
                result["npu_detected"] = False
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("onboarding: lemonade device probe failed: %s", exc)
    return result


def _classify_tier(ram_gb: Optional[float], npu: Optional[bool]) -> str:
    """Coarse hardware tier used only to phrase the recommendation."""
    if ram_gb is None:
        return "unknown"
    if ram_gb >= 32 and npu:
        return "full"
    if ram_gb >= 16:
        return "standard"
    if ram_gb >= _RECOMMENDED_MEMORY_GB:
        return "light"
    return "insufficient"


class PreflightReport(BaseModel):
    """Result of the first-run hardware scan."""

    os: Optional[str] = None
    detected_platform: Optional[str] = None
    ram_gb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    npu_detected: Optional[bool] = None
    gpu_name: Optional[str] = None
    gpu_vram_gb: Optional[float] = None
    lemonade_running: bool = False
    tier: str = "unknown"
    recommended_profile: str = "chat"
    recommended_model: str = _RECOMMENDED_MODEL
    required_disk_gb: float = _RECOMMENDED_DISK_GB
    required_memory_gb: float = _RECOMMENDED_MEMORY_GB
    # ``compatible`` is False only when there is a hard blocker (e.g. no disk).
    compatible: bool = True
    blockers: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class OnboardingStatus(BaseModel):
    initialized: bool = False
    skipped: bool = False
    completed_at: Optional[str] = None


class CompleteOnboardingRequest(BaseModel):
    # ``skipped`` records that the user bailed out of the wizard (power-user
    # path) vs. ran it to completion — surfaced back via ``/status`` so the app
    # can tell the two apart without re-deriving it.
    skipped: bool = False
    # ISO-8601 timestamp; the frontend stamps it (the backend has no clock
    # dependency to keep this testable and cache-stable).
    completed_at: Optional[str] = None


@router.get("/api/onboarding/preflight", response_model=PreflightReport)
async def onboarding_preflight() -> PreflightReport:
    """Scan hardware and report whether this machine can run the first-run model.

    Reuses :func:`check_compatibility` so the blockers/warnings match the hub
    install path exactly, then layers a tier + model recommendation on top.
    """
    devices = await _probe_lemonade_devices()

    npu_detected = devices["npu_detected"]
    gpu_vram = devices["gpu_vram_gb"]

    # Synthesize the recommended first-run download's requirements and run the
    # shared checker. NPU is recommended (advisory) — a no-NPU box still runs on
    # GPU/CPU, so we surface it as a warning rather than blocking first run.
    # The report carries the detected RAM/disk, so we surface those values from
    # the *same* scan that produced the blockers/warnings — one detection, no
    # drift between what we display and what we gate on.
    reqs = Requirements(
        min_memory_gb=_RECOMMENDED_MEMORY_GB,
        min_disk_gb=_RECOMMENDED_DISK_GB,
        npu=True,
        platforms=[],  # cross-platform; don't block on the platform triple here
    )
    report = check_compatibility(
        reqs,
        install_dir=Path.home() / ".gaia",
        detected_npu=npu_detected,
        detected_gpu_vram_gb=gpu_vram,
    )

    ram_gb = report.total_memory_gb

    return PreflightReport(
        os=report.detected_platform,
        detected_platform=report.detected_platform,
        ram_gb=round(ram_gb, 1) if ram_gb is not None else None,
        disk_free_gb=report.free_disk_gb,
        npu_detected=npu_detected,
        gpu_name=devices["gpu_name"],
        gpu_vram_gb=gpu_vram,
        lemonade_running=devices["lemonade_running"],
        tier=_classify_tier(ram_gb, npu_detected),
        recommended_profile="chat",
        recommended_model=_RECOMMENDED_MODEL,
        required_disk_gb=_RECOMMENDED_DISK_GB,
        required_memory_gb=_RECOMMENDED_MEMORY_GB,
        compatible=report.compatible,
        blockers=list(report.blockers),
        warnings=list(report.warnings),
    )


@router.get("/api/onboarding/status", response_model=OnboardingStatus)
async def onboarding_status() -> OnboardingStatus:
    """Report whether first-run setup has already completed."""
    if not _INIT_MARKER.exists():
        return OnboardingStatus(initialized=False)
    skipped = False
    completed_at: Optional[str] = None
    try:
        raw = _INIT_MARKER.read_text(encoding="utf-8").strip()
        if raw:
            data = json.loads(raw)
            skipped = bool(data.get("skipped", False))
            completed_at = data.get("completed_at")
    except (OSError, json.JSONDecodeError):
        # A legacy empty/plain marker still counts as initialized — it just
        # carries no skip/timestamp metadata.
        pass
    return OnboardingStatus(
        initialized=True, skipped=skipped, completed_at=completed_at
    )


@router.post("/api/onboarding/complete", response_model=OnboardingStatus)
async def onboarding_complete(body: CompleteOnboardingRequest) -> OnboardingStatus:
    """Write the ``initialized`` marker so the wizard never re-triggers.

    The marker doubles as the same first-run gate the CLI ``gaia init`` and the
    agent-loop startup check read, so UI and CLI stay in lock-step.
    """
    payload = {
        "skipped": body.skipped,
        "completed_at": body.completed_at,
        "source": "agent-ui-onboarding",
    }
    try:
        _INIT_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _INIT_MARKER.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        # Fail loudly — a swallowed write here would re-show the wizard forever.
        from fastapi import HTTPException  # local import: rare error path

        raise HTTPException(
            status_code=500,
            detail=(
                f"Could not write onboarding marker at {_INIT_MARKER}: {exc}. "
                "Check that your home directory is writable, then retry."
            ),
        ) from exc
    logger.info("Onboarding marked complete (skipped=%s)", body.skipped)
    return OnboardingStatus(
        initialized=True, skipped=body.skipped, completed_at=body.completed_at
    )
