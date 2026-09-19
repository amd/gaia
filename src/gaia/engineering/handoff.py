# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Supported native app launch is separate from MCP context access."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode

_APP_PATHS = {
    "claude": (Path("/Applications/Claude.app"),),
    "codex": (Path("/Applications/Codex.app"), Path("/Applications/ChatGPT.app")),
}


def detect_apps() -> dict:
    result = {}
    for backend, paths in _APP_PATHS.items():
        app = next((path for path in paths if path.is_dir()), None)
        cli = shutil.which(backend)
        if backend == "codex" and not cli and app:
            bundled = app / "Contents" / "Resources" / "codex"
            if bundled.is_file():
                cli = str(bundled)
        result[backend] = {
            "app": str(app) if app else None,
            "cli": cli,
            "available": app is not None,
            "authenticated": "not_checked",
        }
    return result


def connection_config(backend: str, python: str, root: Path, client_token: str) -> dict:
    if backend not in _APP_PATHS:
        raise ValueError("Select claude or codex")
    return {
        "mcpServers": {
            "gaia-engineering": {
                "command": python,
                "args": [
                    "-m",
                    "gaia.mcp.servers.engineering_mcp",
                    "--developer-mode",
                    "--root",
                    str(root.resolve()),
                    "--backend",
                    backend,
                ],
                "env": {
                    "GAIA_ENGINEERING_TOKEN": client_token,
                    "GAIA_DEVELOPER_MODE": "1",
                },
            }
        }
    }


def open_app(backend: str, job_id: str, working_directory: Path) -> dict:
    if backend not in _APP_PATHS or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Invalid backend or job ID")
    if not working_directory.is_dir():
        raise ValueError("Handoff directory does not exist")
    prompt = f"Investigate GAIA engineering job {job_id} using gaia-engineering MCP. Call get_context first; diagnose before editing. Keep private context out of public artifacts."
    if platform.system() != "Darwin":
        return {
            "state": "unsupported",
            "detail": "Native app opening is supported on macOS. Open your configured coding client manually.",
            "prompt": prompt,
            "directory": str(working_directory),
        }
    app = detect_apps()[backend]["app"]
    if not app:
        raise FileNotFoundError(
            f"Install and configure the {backend} Mac app before opening a handoff"
        )
    if backend == "claude":
        url = "claude://code/new?" + urlencode(
            {"q": prompt, "folder": str(working_directory.resolve())}
        )
        command = ["open", "-a", app, url]
    else:
        command = ["open", "-a", app]
    subprocess.run(
        command, check=True, timeout=15, stdin=subprocess.DEVNULL, capture_output=True
    )
    return {
        "state": "requires_user_action",
        "backend": backend,
        "task_created": False,
        "prompt_prefilled": backend == "claude",
        "directory_selected": False,
        "connection_verified": False,
        "detail": (
            "Requested Claude composer prefill. Confirm the folder and submit the prompt; no task was started automatically."
            if backend == "claude"
            else "Opened Codex only. No task was created, no prompt was prefilled, and no folder was selected. Create a new task manually, select the directory below, paste the exact prompt below and send it. Verify gaia-engineering MCP is configured first. Do not tell the user it was posted or that a composer is prefilled."
        ),
        "prompt": prompt,
        "directory": str(working_directory.resolve()),
    }
