# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Processor-name detection for the Agent UI's Settings panel.

Lightweight module with no heavy dependencies — safe to import from both
``gaia.cli`` and ``gaia.ui`` without triggering circular imports.
"""

import functools
import platform
import subprocess
import sys

from gaia.logger import get_logger

log = get_logger(__name__)


@functools.lru_cache(maxsize=1)
def get_processor_name() -> str:
    """Return the human-readable CPU name, e.g. "AMD RYZEN AI MAX+ 395 w/ Radeon 8060S".

    Returns an empty string when the OS does not expose one; the name is
    display-only, so callers hide the row rather than show a guess.
    """
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        except OSError as e:
            log.debug("processor name: registry read failed: %s", e)
            return ""
        return str(name).strip()

    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.debug("processor name: sysctl failed: %s", e)
            return ""
        return result.stdout.strip()

    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    key, _, value = line.partition(":")
                    if key.strip() == "model name":
                        return value.strip()
        except OSError as e:
            log.debug("processor name: /proc/cpuinfo unreadable: %s", e)
        return ""

    return platform.processor()
