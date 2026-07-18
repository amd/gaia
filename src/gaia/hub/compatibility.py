# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""System-requirements checker for the Agent Hub install flow.

Given an agent's declared ``requirements`` (the block from ``gaia-agent.yaml``
/ the catalog index entry), this module reports whether the current machine can
run it.  The result separates *blockers* (hard reasons the install must be
refused — wrong platform, not enough disk for the download) from *warnings*
(soft, recommended-but-not-required gaps — below the suggested RAM, no NPU
detected).  ``compatible`` is true exactly when there are no blockers.

The checker is the single place the install lifecycle (``installer.install``)
and the catalog endpoint (``GET /api/agents/catalog``) consult, so the UI and
the backend agree on what "compatible" means.

Detection is best-effort but never silent: anything we cannot probe (GPU/NPU
presence) becomes a *warning* that names what we could not verify — it never
silently passes as if the requirement were met.
"""

from __future__ import annotations

import platform as _platform
import shutil
import sys as _sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.hub.manifest import Requirements
from gaia.hub.native_launcher import NativeAgentError, current_platform
from gaia.logger import get_logger

logger = get_logger(__name__)

_BYTES_PER_GB = 1024**3

# Headroom multiplier applied to an artifact's download size to estimate the
# on-disk install footprint (extraction + pip-resolved dependencies). A wheel
# typically expands to several times its compressed size once dependencies are
# installed; 3x is a deliberately conservative floor so we block *before* a
# half-finished install fills the disk rather than after.
_INSTALL_SIZE_HEADROOM = 3.0


@dataclass
class CompatibilityReport:
    """Outcome of :func:`check_compatibility`.

    ``compatible`` is ``True`` iff ``blockers`` is empty. ``warnings`` are
    advisory — the install proceeds but the UI should surface them.
    """

    compatible: bool
    platform_ok: bool
    memory_ok: bool
    disk_ok: bool
    warnings: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    # Observed values, surfaced so the UI can render "needs 8 GB, have 6 GB".
    detected_platform: Optional[str] = None
    total_memory_gb: Optional[float] = None
    free_disk_gb: Optional[float] = None
    # ``None`` means the caller supplied no hardware scan for that device.
    detected_npu: Optional[bool] = None
    detected_gpu_vram_gb: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "compatible": self.compatible,
            "platform_ok": self.platform_ok,
            "memory_ok": self.memory_ok,
            "disk_ok": self.disk_ok,
            "warnings": list(self.warnings),
            "blockers": list(self.blockers),
            "detected_platform": self.detected_platform,
            "total_memory_gb": self.total_memory_gb,
            "free_disk_gb": self.free_disk_gb,
            "detected_npu": self.detected_npu,
            "detected_gpu_vram_gb": self.detected_gpu_vram_gb,
        }


def detect_platform() -> Optional[str]:
    """Return the running platform triple (e.g. ``"win-x64"``) or ``None``.

    Wraps :func:`gaia.hub.native_launcher.current_platform`, which *raises* on an
    unsupported OS/arch; here an unsupported platform is reported as ``None`` so
    the caller can turn it into a blocker rather than crashing the whole catalog
    request.
    """
    try:
        return current_platform()
    except NativeAgentError as exc:
        logger.debug("compatibility: unsupported platform: %s", exc)
        return None


def detect_total_memory_gb() -> Optional[float]:
    """Total physical RAM in GB, or ``None`` if it cannot be determined."""
    try:
        import psutil

        return psutil.virtual_memory().total / _BYTES_PER_GB
    except Exception as exc:  # noqa: BLE001 - probe is best-effort
        logger.debug("compatibility: could not read total memory: %s", exc)
        return None


def detect_free_disk_gb(path: Path) -> Optional[float]:
    """Free disk space (GB) on the volume that will hold *path*.

    Walks up to the first existing ancestor so the check works before the
    install directory itself has been created.
    """
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free / _BYTES_PER_GB
    except OSError as exc:
        logger.debug("compatibility: could not read disk usage for %s: %s", probe, exc)
        return None


def current_platform_key(plat: Optional[str] = None, arch: Optional[str] = None) -> str:
    """Resolve the host's platform key in the artifact-filename namespace.

    Distinct from :func:`current_platform`'s ``{os}-{arch}`` hub-triple
    vocabulary (``win-x64``) used for ``requirements.platforms`` gates — this
    is the npm-package namespace (``win32-x64``, ``darwin-arm64``) used to
    select an entry from a manifest's ``versions[v].artifacts[]`` by filename
    suffix. Mirrors ``gaia.daemon.sidecars.platform.current_platform_key()``
    exactly (parity-tested); duplicated rather than imported by design (the
    hub stays free of the daemon package). Never raises — an
    unrecognized OS/arch passes through as-is so the artifact-selection loud
    error can name it, rather than crashing here.
    """
    raw_os = plat if plat is not None else _sys.platform
    if raw_os.startswith("win"):
        os_key = "win32"
    elif raw_os == "darwin":
        os_key = "darwin"
    elif raw_os.startswith("linux"):
        os_key = "linux"
    else:
        os_key = raw_os
    raw_arch = (arch if arch is not None else _platform.machine()).lower()
    if raw_arch in ("x86_64", "amd64", "x64"):
        arch_key = "x64"
    elif raw_arch in ("arm64", "aarch64"):
        arch_key = "arm64"
    else:
        arch_key = raw_arch
    return f"{os_key}-{arch_key}"


def _coerce_requirements(requirements: Any) -> Requirements:
    """Accept either a :class:`Requirements` or a plain mapping (index entry)."""
    if isinstance(requirements, Requirements):
        return requirements
    if requirements is None:
        return Requirements()
    if isinstance(requirements, dict):
        return Requirements(
            min_memory_gb=requirements.get("min_memory_gb"),
            min_disk_gb=requirements.get("min_disk_gb"),
            min_context_size=requirements.get("min_context_size"),
            platforms=list(requirements.get("platforms") or []),
            npu=bool(requirements.get("npu", False)),
            gpu_vram_gb=requirements.get("gpu_vram_gb"),
        )
    raise TypeError(
        "requirements must be a Requirements, a mapping, or None, got "
        f"{type(requirements).__name__}"
    )


def check_compatibility(
    requirements: Any,
    *,
    download_size_bytes: int = 0,
    install_dir: Optional[Path] = None,
    detected_npu: Optional[bool] = None,
    detected_gpu_vram_gb: Optional[float] = None,
) -> CompatibilityReport:
    """Check whether the current machine satisfies *requirements*.

    Args:
        requirements: A :class:`Requirements` or the catalog index entry's
            ``requirements`` mapping (must at least carry ``platforms``).
        download_size_bytes: Size of the artifact to be downloaded; used with a
            headroom multiplier to estimate required free disk when the manifest
            declares no explicit ``min_disk_gb``.
        install_dir: Where the agent will be installed; the disk check probes
            this volume. Defaults to ``~/.gaia/agents``.
        detected_npu: Whether an NPU was actually detected by the caller's
            hardware scan (e.g. the Agent UI's ``/api/system/status`` probe).
            ``True``/``False`` turn an ``npu`` requirement into a real
            pass/warning; ``None`` (the default) keeps the conservative
            "cannot verify" warning for callers that have no scan.
        detected_gpu_vram_gb: GPU VRAM (GB) reported by the caller's scan, used
            the same way for a ``gpu_vram_gb`` requirement. ``None`` keeps the
            "cannot verify" warning.

    Returns:
        A :class:`CompatibilityReport`. ``compatible`` is ``True`` only when no
        blockers were found.
    """
    reqs = _coerce_requirements(requirements)
    install_dir = install_dir or (Path.home() / ".gaia" / "agents")

    warnings: List[str] = []
    blockers: List[str] = []

    # --- platform ---
    detected = detect_platform()
    if reqs.platforms:
        if detected is None:
            platform_ok = False
            blockers.append(
                "This OS/CPU architecture is unsupported. The agent supports: "
                f"{', '.join(reqs.platforms)}."
            )
        elif detected not in reqs.platforms:
            platform_ok = False
            blockers.append(
                f"Your platform ({detected}) is not supported by this agent. "
                f"Supported platforms: {', '.join(reqs.platforms)}."
            )
        else:
            platform_ok = True
    else:
        # No declared platforms — assume cross-platform.
        platform_ok = True

    # --- memory (advisory) ---
    total_mem = detect_total_memory_gb()
    memory_ok = True
    if reqs.min_memory_gb:
        if total_mem is None:
            warnings.append(
                f"Could not detect system memory; this agent recommends "
                f"{reqs.min_memory_gb:g} GB."
            )
        elif total_mem < reqs.min_memory_gb:
            memory_ok = False
            warnings.append(
                f"This agent recommends {reqs.min_memory_gb:g} GB RAM; your "
                f"system has {total_mem:.1f} GB. It may run slowly or fail to "
                f"load its model."
            )

    # --- disk (hard) ---
    free_disk = detect_free_disk_gb(install_dir)
    if reqs.min_disk_gb is not None:
        required_disk_gb = float(reqs.min_disk_gb)
    else:
        required_disk_gb = (
            download_size_bytes * _INSTALL_SIZE_HEADROOM
        ) / _BYTES_PER_GB
    disk_ok = True
    if required_disk_gb > 0:
        if free_disk is None:
            warnings.append(
                "Could not determine free disk space; install needs about "
                f"{required_disk_gb:.1f} GB."
            )
        elif free_disk < required_disk_gb:
            disk_ok = False
            blockers.append(
                f"Not enough disk space: install needs ~{required_disk_gb:.1f} GB "
                f"but only {free_disk:.1f} GB is free on {install_dir}."
            )

    # --- NPU / GPU (advisory) ---
    # When the caller passes a real hardware scan (detected_npu /
    # detected_gpu_vram_gb), turn the requirement into a concrete pass or
    # warning instead of the blanket "cannot verify". Absent a scan we fall
    # back to the conservative message — never silently passing (#1727).
    if reqs.npu:
        if detected_npu is True:
            pass  # requirement met — no warning
        elif detected_npu is False:
            warnings.append(
                "This agent requests an NPU, but none was detected on this "
                "machine. It will fall back to GPU/CPU inference, which is "
                "slower. Ensure your Ryzen AI NPU driver is installed."
            )
        else:
            warnings.append(
                "This agent requests an NPU. GAIA cannot verify NPU availability "
                "here; ensure your Ryzen AI NPU driver is installed."
            )
    if reqs.gpu_vram_gb:
        if detected_gpu_vram_gb is None:
            warnings.append(
                f"This agent recommends {reqs.gpu_vram_gb:g} GB of GPU VRAM. GAIA "
                "cannot verify GPU VRAM here."
            )
        elif detected_gpu_vram_gb < reqs.gpu_vram_gb:
            warnings.append(
                f"This agent recommends {reqs.gpu_vram_gb:g} GB of GPU VRAM; this "
                f"machine has {detected_gpu_vram_gb:g} GB. It may run slowly or "
                "fail to load its model."
            )
        # else: detected VRAM meets/exceeds the recommendation — no warning.

    return CompatibilityReport(
        compatible=len(blockers) == 0,
        platform_ok=platform_ok,
        memory_ok=memory_ok,
        disk_ok=disk_ok,
        warnings=warnings,
        blockers=blockers,
        detected_platform=detected,
        total_memory_gb=round(total_mem, 2) if total_mem is not None else None,
        free_disk_gb=round(free_disk, 2) if free_disk is not None else None,
        detected_npu=detected_npu,
        detected_gpu_vram_gb=detected_gpu_vram_gb,
    )
