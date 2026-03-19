# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Device compatibility detection for GAIA Agent UI.

Lightweight module with no heavy dependencies — safe to import from both
``gaia.cli`` and ``gaia.ui`` without triggering circular imports.
"""

import sys

# ── Supported device fingerprints ─────────────────────────────────────────
# Strix Halo processors contain "Ryzen AI Max" (iGPU/HBM, unified memory).
_SUPPORTED_CPU_KEYWORDS = ["RYZEN AI MAX"]

# AMD Radeon discrete GPU keyword — must be paired with a >= 24 GB VRAM check.
# 24 GB is the minimum to load Qwen3-Coder-30B-A3B-Instruct-GGUF (Q4_K_M ~17 GB).
_RADEON_GPU_KEYWORD = "AMD RADEON"
_MIN_GPU_VRAM_GB = 24.0

GITHUB_DEVICE_SUPPORT_URL = (
    "https://github.com/amd/gaia/issues/new?"
    "template=feature_request.md&"
    "title=[Feature]%20Support%20Agent%20UI%20on%20additional%20devices&"
    "labels=enhancement,agent-ui"
)


def get_processor_name() -> str:
    """Get the human-readable processor name.

    On Windows, reads from the registry for instant results (no subprocess).
    Falls back to ``platform.processor()`` on other OSes.

    Returns:
        Processor name string (e.g. "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S"),
        or empty string if detection fails.
    """
    # Windows: read from registry (instant, no subprocess needed)
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return name.strip()
        except Exception:
            pass

    # Linux/macOS fallback
    try:
        import platform

        return platform.processor()
    except Exception:
        return ""


def get_gpu_info() -> list[tuple[str, float]]:
    """Get a list of (name, vram_gb) tuples for display adapters on this system.

    On Windows, reads the display-adapter registry keys which contain a
    REG_QWORD value ``HardwareInformation.qwMemorySize`` with the accurate
    VRAM size (unlike Win32_VideoController.AdapterRAM which caps at 4 GB).

    On non-Windows platforms an empty list is returned — GPU detection is
    currently Windows-only.

    Returns:
        List of ``(name, vram_gb)`` tuples, empty on failure or non-Windows.
    """
    if sys.platform != "win32":
        return []

    results: list[tuple[str, float]] = []
    try:
        import winreg

        _DISPLAY_CLASS = (
            r"SYSTEM\CurrentControlSet\Control\Class"
            r"\{4d36e968-e325-11ce-bfc1-08002be10318}"
        )
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS) as root:
            idx = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(root, idx)
                    idx += 1
                    if subkey_name in ("Properties", "Configuration"):
                        continue
                    with winreg.OpenKey(root, subkey_name) as sk:
                        try:
                            desc, _ = winreg.QueryValueEx(sk, "DriverDesc")
                            try:
                                vram_bytes, _ = winreg.QueryValueEx(
                                    sk, "HardwareInformation.qwMemorySize"
                                )
                                vram_gb = vram_bytes / (1024**3)
                            except OSError:
                                vram_gb = 0.0
                            results.append((desc, vram_gb))
                        except OSError:
                            pass
                except OSError:
                    break
    except Exception:
        pass

    return results


def check_device_supported(log=None) -> tuple[bool, str]:
    """Check if the current device is supported for running the Agent UI.

    Supported configurations:

    - **AMD Ryzen AI Max** (Strix Halo) — unified HBM memory (64 GB+).
    - **AMD Radeon discrete GPU** with >= 24 GB VRAM to fit Qwen3-Coder-30B.

    When the processor name cannot be detected the function returns
    ``(True, "unknown")`` to avoid false-blocking unknown hardware.

    Returns:
        ``(supported, device_name)`` where ``device_name`` is the matched
        CPU or GPU name (includes VRAM for GPU matches), or the CPU name
        when the device is rejected.
    """
    processor_name = get_processor_name()
    if log:
        log.debug(f"Detected processor: {processor_name}")

    if not processor_name:
        # Can't detect — allow with a warning rather than blocking
        if log:
            log.warning("Could not detect processor name; skipping device check")
        return True, "unknown"

    upper_cpu = processor_name.upper()
    for keyword in _SUPPORTED_CPU_KEYWORDS:
        if keyword in upper_cpu:
            if log:
                log.debug(f"Supported CPU detected: {processor_name}")
            return True, processor_name

    # Not a supported CPU — check for a qualifying AMD Radeon discrete GPU
    gpu_info = get_gpu_info()
    if log:
        log.debug(f"Detected GPUs: {gpu_info}")
    for gpu_name, vram_gb in gpu_info:
        if _RADEON_GPU_KEYWORD in gpu_name.upper() and vram_gb >= _MIN_GPU_VRAM_GB:
            if log:
                log.debug(f"Supported GPU detected: {gpu_name} ({vram_gb:.0f} GB)")
            return True, f"{gpu_name} ({vram_gb:.0f} GB VRAM)"

    return False, processor_name
