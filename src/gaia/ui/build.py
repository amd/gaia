# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Shared utility for building the Agent UI frontend.

Extracted from cli.py so that init_command.py can call it without
creating a circular import through the full CLI module.
"""

import shutil
import subprocess
import sys
from pathlib import Path


def ensure_webui_built(log_fn=print, _webui_dir=None):
    """Rebuild the Agent UI frontend if source files are newer than dist.

    Only runs in dev mode (editable install) where the webui src/ directory
    exists.  Silently skips in installed-package mode or when node/npm are
    not available.

    Args:
        log_fn: Callable used for user-visible output.  Defaults to ``print``.
                Pass ``logger.info`` or ``self._print`` to integrate with your
                own output mechanism.
        _webui_dir: Override the webui directory path (used in tests only).
    """
    webui_dir = (
        _webui_dir
        if _webui_dir is not None
        else Path(__file__).resolve().parent.parent / "apps" / "webui"
    )
    src_dir = webui_dir / "src"
    dist_index = webui_dir / "dist" / "index.html"

    # Gate 1 — dev mode only (src/ absent in pip-installed package)
    if not src_dir.is_dir():
        return

    # Gate 2 — staleness check
    newest_src = 0.0
    for pattern in ("*.ts", "*.tsx", "*.css", "*.html"):
        for path in src_dir.rglob(pattern):
            mtime = path.stat().st_mtime
            if mtime > newest_src:
                newest_src = mtime
    for root_file in ("index.html", "vite.config.ts", "tsconfig.json"):
        p = webui_dir / root_file
        if p.exists():
            newest_src = max(newest_src, p.stat().st_mtime)

    if dist_index.exists() and newest_src <= dist_index.stat().st_mtime:
        return

    if dist_index.exists():
        log_fn("Agent UI frontend source is newer than built output")
    else:
        log_fn("Agent UI frontend has not been built yet")

    # Gate 3 — node/npm availability
    if not shutil.which("node"):
        log_fn("Warning: Node.js not found. Cannot auto-rebuild Agent UI frontend.")
        log_fn("  The UI may be stale. Install Node.js from https://nodejs.org/")
        return
    if not shutil.which("npm"):
        log_fn("Warning: npm not found. Cannot auto-rebuild Agent UI frontend.")
        return

    # On Windows, npm is a .cmd batch file requiring shell execution
    _shell = sys.platform == "win32"

    # Step 1 — npm install (only if node_modules/ missing)
    if not (webui_dir / "node_modules").is_dir():
        log_fn("Installing Agent UI frontend dependencies...")
        try:
            subprocess.run(
                ["npm", "install"],
                cwd=str(webui_dir),
                check=True,
                capture_output=True,
                text=True,
                shell=_shell,
            )
        except subprocess.CalledProcessError as e:
            log_fn(f"Warning: npm install failed: {e.stderr}")
            log_fn("  Continuing with existing dist/ (may be stale).")
            return
        except FileNotFoundError:
            log_fn("Warning: npm not found. Skipping frontend rebuild.")
            return

    # Step 2 — npm run build (stream output so user sees progress)
    log_fn("Building Agent UI frontend...")
    try:
        subprocess.run(
            ["npm", "run", "build"],
            cwd=str(webui_dir),
            check=True,
            shell=_shell,
        )
        log_fn("Agent UI frontend built successfully.")
    except subprocess.CalledProcessError as e:
        log_fn(f"Warning: Frontend build failed (exit code {e.returncode}).")
        if dist_index.exists():
            log_fn("  Continuing with existing (possibly stale) build.")
        else:
            log_fn("  No existing build found. The UI will show a build hint.")
    except FileNotFoundError:
        log_fn("Warning: npm not found. Skipping frontend rebuild.")
