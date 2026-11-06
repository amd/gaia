# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep the system (not the display) awake while phone alerts are on and on AC power.

A sleeping PC can't place a call, so phone alerts hold an OS sleep inhibitor:
``SetThreadExecutionState`` on Windows, ``systemd-inhibit`` on Linux and
``caffeinate`` on macOS. It is released on battery and when phone is off.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from typing import Optional

from gaia.logger import get_logger

log = get_logger(__name__)

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def on_ac_power() -> bool:
    """True on AC power, or on a machine with no battery."""
    import psutil

    battery = psutil.sensors_battery()
    return battery is None or bool(battery.power_plugged)


class AwakeHold:
    """Hold or release the sleep inhibitor; :attr:`state` says which and why."""

    def __init__(self):
        self.held = False
        self.reason = "off"
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._release = threading.Event()

    def want(self, on: bool) -> None:
        if self.held and self._proc is not None and self._proc.poll() is not None:
            self._proc, self.held, self.reason = None, False, "unsupported"
        if on and not on_ac_power():
            on = False
            self.reason = "battery"
        if on and not self.held:
            self._hold()
        elif not on and self.held:
            self._drop()
            self.reason = self.reason if self.reason == "battery" else "off"
        elif not on and self.reason not in ("battery", "off", "unsupported"):
            self.reason = "off"

    @property
    def state(self) -> dict:
        return {"held": self.held, "reason": "held" if self.held else self.reason}

    def _hold(self) -> None:
        if sys.platform == "win32":
            self._release.clear()
            self._thread = threading.Thread(
                target=self._windows_hold, name="phone-awake", daemon=True
            )
            self._thread.start()
            self.held, self.reason = True, "held"
            return
        if sys.platform == "darwin":
            tool = shutil.which("caffeinate")
            cmd = [tool, "-i", "-w", str(os.getpid())] if tool else None
        else:
            tool = shutil.which("systemd-inhibit")
            cmd = (
                [
                    tool,
                    "--what=sleep",
                    "--who=GAIA",
                    "--why=Phone alerts are on",
                    "--mode=block",
                    # Ends with the daemon, even one that is killed.
                    "tail",
                    f"--pid={os.getpid()}",
                    "-f",
                    "/dev/null",
                ]
                if tool
                else None
            )
        if cmd is None:
            self.reason = "unsupported"
            log.warning(
                "phone: no sleep inhibitor on this system; calls need the PC awake"
            )
            return
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError as e:
            self.reason = "unsupported"
            log.warning("phone: could not keep the PC awake: %s", e)
            return
        try:
            code = self._proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            self.held, self.reason = True, "held"
            return
        # It exited at once: no logind, or not allowed to inhibit sleep.
        self._proc = None
        self.reason = "unsupported"
        log.warning("phone: the sleep inhibitor exited with %s", code)

    def _windows_hold(self) -> None:
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
        self._release.wait()
        kernel.SetThreadExecutionState(_ES_CONTINUOUS)

    def _drop(self) -> None:
        if self._thread is not None:
            self._release.set()
            self._thread.join(timeout=5)
            self._thread = None
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        self.held = False
