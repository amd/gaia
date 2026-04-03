# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Day-0 system context collector for GAIA agent memory.

Gathers non-personal system information (OS, hardware, installed software)
and returns it as a list of facts suitable for storage in the memory system.
This gives a freshly-initialized agent immediate awareness of the host
environment so it can tailor responses from the very first interaction.

Design constraints:
- NO imports from gaia (except gaia.version, inside try/except)
- Every collection step is wrapped in try/except — partial collection is fine
- subprocess calls always use timeout= to avoid blocking
- No personal data is collected (no usernames, documents, browsing history)
"""

import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


def collect_system_info() -> List[Dict[str, str]]:
    """Collect system context facts for day-0 memory initialization.

    Returns a list of dicts, each with:
        - ``content`` (str): Human-readable fact string.
        - ``domain``  (str): Domain tag for categorization.

    Every collection step is independently guarded — a failure in one
    step does not prevent the others from succeeding.
    """
    facts: List[Dict[str, str]] = []

    # 1. OS
    try:
        os_info = f"{platform.system()} {platform.version()}"
        if len(os_info) > 200:
            os_info = os_info[:200]
        facts.append({"content": f"Operating system: {os_info}", "domain": "system:os"})
    except Exception:
        pass

    # 2. Architecture
    try:
        arch = platform.machine()
        if arch:
            facts.append(
                {"content": f"CPU architecture: {arch}", "domain": "system:hardware"}
            )
    except Exception:
        pass

    # 3. Hostname
    try:
        hostname = platform.node()
        if hostname:
            facts.append(
                {"content": f"Hostname: {hostname}", "domain": "system:identity"}
            )
    except Exception:
        pass

    # 4. CPU
    try:
        cpu_name = None
        # Try Windows registry first
        if platform.system() == "Windows":
            try:
                import winreg

                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                )
                cpu_name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                winreg.CloseKey(key)
            except Exception:
                pass
        # Fallback to platform.processor()
        if not cpu_name:
            cpu_name = platform.processor()
        cores = os.cpu_count()
        parts = []
        if cpu_name:
            parts.append(cpu_name.strip())
        if cores:
            parts.append(f"{cores} logical cores")
        if parts:
            facts.append(
                {
                    "content": f"CPU: {', '.join(parts)}",
                    "domain": "system:hardware",
                }
            )
    except Exception:
        pass

    # 5. RAM (requires psutil)
    try:
        import psutil

        total_bytes = psutil.virtual_memory().total
        total_gb = total_bytes / (1024**3)
        facts.append(
            {
                "content": f"RAM: {total_gb:.1f} GB total",
                "domain": "system:hardware",
            }
        )
    except ImportError:
        pass
    except Exception:
        pass

    # 6. Disk
    try:
        if platform.system() == "Windows":
            disk_root = "C:\\"
            label = "C:"
        else:
            disk_root = "/"
            label = "/"
        usage = shutil.disk_usage(disk_root)
        total_gb = usage.total / (1024**3)
        free_gb = usage.free / (1024**3)
        facts.append(
            {
                "content": (
                    f"Main disk ({label}) — "
                    f"{total_gb:.0f} GB total, {free_gb:.0f} GB free"
                ),
                "domain": "system:hardware",
            }
        )
    except Exception:
        pass

    # 7. GPU
    try:
        gpu_names: List[str] = []
        _sys = platform.system()
        if _sys == "Windows":
            # Use PowerShell/CIM (wmic is deprecated on Windows 11)
            try:
                output = subprocess.check_output(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        "Get-CimInstance -ClassName Win32_VideoController | "
                        "Select-Object -ExpandProperty Name",
                    ],
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                gpu_names = [l.strip() for l in output.splitlines() if l.strip()]
            except Exception:
                pass
        elif _sys == "Darwin":
            # macOS: system_profiler gives GPU/display info
            try:
                output = subprocess.check_output(
                    ["system_profiler", "SPDisplaysDataType"],
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                for line in output.splitlines():
                    line = line.strip()
                    if line.startswith("Chipset Model:"):
                        name = line.split(":", 1)[1].strip()
                        if name:
                            gpu_names.append(name)
            except Exception:
                pass
        elif _sys == "Linux":
            try:
                output = subprocess.check_output(
                    ["lspci", "-mm"],
                    timeout=3,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                gpu_names = [
                    l.strip()
                    for l in output.splitlines()
                    if "vga" in l.lower() and l.strip()
                ][:3]
            except Exception:
                pass
        if gpu_names:
            facts.append(
                {
                    "content": f"GPU: {'; '.join(gpu_names)}",
                    "domain": "system:hardware",
                }
            )
    except Exception:
        pass

    # 8. Timezone
    try:
        tz_name = datetime.now(timezone.utc).astimezone().tzname()
        tz_offset = time.strftime("%z")
        facts.append(
            {
                "content": f"Timezone: {tz_name} (UTC{tz_offset})",
                "domain": "system:locale",
            }
        )
    except Exception:
        pass

    # 9. Python version
    try:
        py_ver = sys.version.split()[0]
        facts.append(
            {
                "content": f"Python version: {py_ver}",
                "domain": "system:software",
            }
        )
    except Exception:
        pass

    # 10. GAIA version + Lemonade version
    try:
        from gaia.version import LEMONADE_VERSION, __version__

        facts.append(
            {
                "content": f"GAIA version: {__version__}",
                "domain": "system:software",
            }
        )
        facts.append(
            {
                "content": f"Lemonade Server version: {LEMONADE_VERSION}",
                "domain": "system:software",
            }
        )
    except Exception:
        pass

    # 11. Installed applications (existence check only, no personal data)
    try:
        found_apps: List[str] = []
        home = Path.home()
        _sys = platform.system()

        if _sys == "Windows":
            _native_apps: dict = {
                "Chrome": Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                "Firefox": Path("C:/Program Files/Mozilla Firefox/firefox.exe"),
                "Edge": Path(
                    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
                ),
                "Spotify": home / "AppData/Roaming/Spotify/Spotify.exe",
                "Slack": home / "AppData/Local/slack/slack.exe",
                "Discord": home / "AppData/Local/Discord/Update.exe",
                "VS Code": home / "AppData/Local/Programs/Microsoft VS Code/Code.exe",
                "Cursor": home / "AppData/Local/Programs/cursor/Cursor.exe",
            }
            for name, path in _native_apps.items():
                try:
                    if path.exists():
                        found_apps.append(name)
                except Exception:
                    pass

        elif _sys == "Darwin":
            # macOS: most GUI apps live in /Applications/
            _mac_apps: dict = {
                "Chrome": Path("/Applications/Google Chrome.app"),
                "Firefox": Path("/Applications/Firefox.app"),
                "Safari": Path("/Applications/Safari.app"),
                "Spotify": Path("/Applications/Spotify.app"),
                "Slack": Path("/Applications/Slack.app"),
                "Discord": Path("/Applications/Discord.app"),
                "VS Code": Path("/Applications/Visual Studio Code.app"),
                "Cursor": Path("/Applications/Cursor.app"),
                "Xcode": Path("/Applications/Xcode.app"),
                "iTerm2": Path("/Applications/iTerm.app"),
            }
            for name, path in _mac_apps.items():
                try:
                    if path.exists():
                        found_apps.append(name)
                except Exception:
                    pass

        elif _sys == "Linux":
            # Linux: check common binary paths and .desktop launchers
            _linux_bins: dict = {
                "Chrome": [
                    Path("/usr/bin/google-chrome"),
                    Path("/usr/bin/chromium-browser"),
                    Path("/usr/bin/chromium"),
                ],
                "Firefox": [Path("/usr/bin/firefox")],
                "Spotify": [
                    Path("/usr/bin/spotify"),
                    home / ".local/share/applications/spotify.desktop",
                ],
                "Discord": [
                    Path("/usr/bin/discord"),
                    home / ".local/share/applications/discord.desktop",
                ],
                "Slack": [Path("/usr/bin/slack")],
                "VS Code": [Path("/usr/bin/code"), Path("/usr/share/code/code")],
                "Cursor": [Path("/usr/bin/cursor")],
            }
            for name, paths in _linux_bins.items():
                try:
                    if any(p.exists() for p in paths):
                        found_apps.append(name)
                except Exception:
                    pass

        # Cross-platform: shutil.which() for CLI tools.
        # Use the same display names as the platform checks to avoid duplicates.
        _cli_tools: dict = {
            "git": "git",
            "code": "VS Code",
            "cursor": "Cursor",
            "node": "Node.js",
            "docker": "Docker",
            "brew": "Homebrew",
            "npm": "npm",
        }
        for cmd, label in _cli_tools.items():
            try:
                if shutil.which(cmd) and label not in found_apps:
                    found_apps.append(label)
            except Exception:
                pass

        if found_apps:
            facts.append(
                {
                    "content": f"Installed applications: {', '.join(found_apps)}",
                    "domain": "system:software",
                }
            )
    except Exception:
        pass

    # 12. Init date
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        facts.append(
            {
                "content": f"System profile first captured on: {today}",
                "domain": "system:meta",
            }
        )
    except Exception:
        pass

    return facts
