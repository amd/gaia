# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Shared utility for building the Agent UI frontend.

Extracted from cli.py so that init_command.py can call it without
creating a circular import through the full CLI module. Deliberately
stdlib-only (no `gaia.*` imports) to preserve that property.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class WebuiBuildStatus(Enum):
    """Outcome of an :func:`ensure_webui_built` call."""

    OK = "ok"  # built, or dist is already current (incl. stale-but-usable)
    SKIPPED = "skipped"  # not a dev install, or GAIA_SKIP_WEBUI_BUILD is set
    TOOLCHAIN_ABSENT = "absent"  # node and/or npm missing entirely
    NODE_TOO_OLD = "too_old"  # node present but below engines.node
    BUILD_FAILED = "failed"  # build attempted, failed, no usable dist


@dataclass(frozen=True)
class WebuiBuildResult:
    """Structured outcome of :func:`ensure_webui_built`.

    ``bool(result)`` is truthy iff a usable dist/ exists (status OK), so a
    caller that only does ``if ensure_webui_built(...):`` keeps behaving
    sanely -- it never mistakes a hard failure for success.
    """

    status: WebuiBuildStatus
    message: str = ""
    found_version: Optional[str] = None
    required_range: Optional[str] = None
    node_path: Optional[str] = None

    def __bool__(self) -> bool:
        return self.status is WebuiBuildStatus.OK


def _parse_version(text):
    """Parse the first `MAJOR[.MINOR[.PATCH]]` run out of *text*.

    Returns a numeric tuple, or None if no version-shaped substring is
    found (e.g. nvm's ``N/A``, asdf's ``No version is set``). Callers
    must treat None as "skip the check", never as "too old".
    """
    if not text:
        return None
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text)
    if not match:
        return None
    major, minor, patch = match.groups()
    return (int(major), int(minor or 0), int(patch or 0))


def _parse_range_floor(range_spec):
    """Parse a `>=MAJOR[.MINOR[.PATCH]]` engines.node range into a numeric
    floor tuple. Only the plain `>=` form is understood; anything else
    (compound ranges, caret/tilde, exact pins, malformed strings) returns
    None so the caller skips the check rather than guessing.
    """
    if not range_spec or not isinstance(range_spec, str):
        return None
    match = re.fullmatch(r"\s*>=\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?\s*", range_spec)
    if not match:
        return None
    major, minor, patch = match.groups()
    return (int(major), int(minor or 0), int(patch or 0))


def _read_required_node_range(webui_dir):
    """Read `engines.node` from the webui's own package.json.

    Returns the raw range string, or None if package.json is missing,
    unreadable, malformed, or doesn't declare a string `engines.node` --
    all of which are repo-integrity issues, not user-environment ones.
    """
    try:
        with open(webui_dir / "package.json", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    engines = data.get("engines")
    if not isinstance(engines, dict):
        return None
    node_range = engines.get("node")
    return node_range if isinstance(node_range, str) else None


def _check_node_version(node_path, webui_dir, warn_fn=None):
    """Compare the resolved node binary's version against `engines.node`.

    Returns (status, found_version, required_range) where status is
    "ok" (satisfies the floor, or the check couldn't be performed and was
    skipped) or "too_old" (definitively below the floor). Never raises --
    a hanging or unparseable `node --version` degrades to "ok"/skip.
    """
    required_range = _read_required_node_range(webui_dir)
    required_floor = _parse_range_floor(required_range)
    if required_floor is None:
        # A declared-but-unparseable range (e.g. a compound range like
        # "^20 || >=22") is a silent no-op otherwise -- say so once, so the
        # preflight going dark is visible instead of indistinguishable from
        # "nothing to check".
        if required_range is not None and warn_fn is not None:
            warn_fn(
                f"Could not parse engines.node {required_range!r}; skipping "
                "the Node version preflight."
            )
        return "ok", None, required_range

    try:
        proc = subprocess.run(
            [node_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return "ok", None, required_range

    if proc.returncode != 0:
        return "ok", None, required_range

    found_version = _parse_version(proc.stdout)
    if found_version is None:
        return "ok", None, required_range

    found_str = ".".join(str(part) for part in found_version)
    if found_version < required_floor:
        return "too_old", found_str, required_range
    return "ok", found_str, required_range


def ensure_webui_built(log_fn=print, warn_fn=None, _webui_dir=None):
    """Rebuild the Agent UI frontend if source files are newer than dist.

    Only runs in dev mode (editable install) where the webui src/ directory
    exists.  Silently skips in installed-package mode or when node/npm are
    not available.

    Args:
        log_fn: Callable used for informational output.  Defaults to ``print``.
                Pass ``logger.info`` or ``self._print`` to integrate with your
                own output mechanism.
        warn_fn: Callable used for warning/error output.  Defaults to
                 ``log_fn`` when not provided.  Pass ``logger.warning`` or
                 ``self._print_warning`` to route warnings separately from
                 informational messages.
        _webui_dir: Override the webui directory path (used in tests only).

    Returns:
        A :class:`WebuiBuildResult`. Never raises for an expected build/
        toolchain/version failure -- those are reported via the returned
        status, not an exception. ``bool(result)`` is truthy only when a
        usable dist/ exists.
    """
    if warn_fn is None:
        warn_fn = log_fn

    if os.environ.get("GAIA_SKIP_WEBUI_BUILD"):
        return WebuiBuildResult(
            status=WebuiBuildStatus.SKIPPED,
            message="Agent UI frontend build skipped (GAIA_SKIP_WEBUI_BUILD is set).",
        )

    webui_dir = (
        _webui_dir
        if _webui_dir is not None
        else Path(__file__).resolve().parent.parent / "apps" / "webui"
    )
    src_dir = webui_dir / "src"
    dist_index = webui_dir / "dist" / "index.html"

    # Gate 1 — dev mode only (src/ absent in pip-installed package)
    if not src_dir.is_dir():
        return WebuiBuildResult(status=WebuiBuildStatus.SKIPPED)

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
        return WebuiBuildResult(status=WebuiBuildStatus.OK)

    if dist_index.exists():
        log_fn("Agent UI frontend source is newer than built output")
    else:
        log_fn("Agent UI frontend has not been built yet")

    # Gate 3 — node/npm availability
    node_path = shutil.which("node")
    if not node_path:
        message = "Warning: Node.js not found. Cannot auto-rebuild Agent UI frontend."
        warn_fn(message)
        warn_fn("  The UI may be stale. Install Node.js from https://nodejs.org/")
        return WebuiBuildResult(
            status=WebuiBuildStatus.TOOLCHAIN_ABSENT, message=message
        )
    if not shutil.which("npm"):
        message = "Warning: npm not found. Cannot auto-rebuild Agent UI frontend."
        warn_fn(message)
        return WebuiBuildResult(
            status=WebuiBuildStatus.TOOLCHAIN_ABSENT, message=message
        )

    # Gate 3b — node version preflight, resolved via the exact path above so
    # the reported binary matches the one actually invoked (a PATH shim
    # earlier than node_path could otherwise disagree with a bare `node`).
    version_status, found_version, required_range = _check_node_version(
        node_path, webui_dir, warn_fn=warn_fn
    )
    if version_status == "too_old":
        message = (
            f"Agent UI frontend requires Node {required_range}, but "
            f"{node_path} reports v{found_version}. If a newer Node is "
            "already installed via nvm/fnm/volta, make sure it comes first "
            "on PATH (a system Node can shadow it in non-interactive "
            f"shells); otherwise install a Node satisfying {required_range} "
            "from https://nodejs.org/."
        )
        warn_fn(message)
        return WebuiBuildResult(
            status=WebuiBuildStatus.NODE_TOO_OLD,
            message=message,
            found_version=found_version,
            required_range=required_range,
            node_path=node_path,
        )

    # On Windows npm is a .cmd batch file, which CreateProcess can't launch
    # directly; invoke via `cmd /c` (args are static) so we avoid shell=True.
    def _npm(*args):
        if sys.platform == "win32":
            return ["cmd", "/c", "npm", *args]
        return ["npm", *args]

    # Step 1 — npm install (only if node_modules/ missing)
    if not (webui_dir / "node_modules").is_dir():
        log_fn("Installing Agent UI frontend dependencies...")
        try:
            subprocess.run(
                _npm("install"),
                cwd=str(webui_dir),
                check=True,
                capture_output=True,
                text=True,
                shell=False,
            )
        except subprocess.CalledProcessError as e:
            message = f"Warning: npm install failed: {e.stderr}"
            warn_fn(message)
            if dist_index.exists():
                warn_fn("  Continuing with existing dist/ (may be stale).")
                return WebuiBuildResult(
                    status=WebuiBuildStatus.OK, message=message, node_path=node_path
                )
            warn_fn("  No existing build found. The UI will show a build hint.")
            return WebuiBuildResult(
                status=WebuiBuildStatus.BUILD_FAILED,
                message=message,
                node_path=node_path,
            )
        except FileNotFoundError:
            message = "Warning: npm not found. Skipping frontend rebuild."
            warn_fn(message)
            return WebuiBuildResult(
                status=WebuiBuildStatus.TOOLCHAIN_ABSENT, message=message
            )

    # Step 2 — npm run build (stream output so user sees progress)
    log_fn("Building Agent UI frontend...")
    try:
        subprocess.run(
            _npm("run", "build"),
            cwd=str(webui_dir),
            check=True,
            shell=False,
        )
        log_fn("Agent UI frontend built successfully.")
        return WebuiBuildResult(status=WebuiBuildStatus.OK, node_path=node_path)
    except subprocess.CalledProcessError as e:
        message = f"Warning: Frontend build failed (exit code {e.returncode})."
        warn_fn(message)
        if dist_index.exists():
            warn_fn("  Continuing with existing (possibly stale) build.")
            return WebuiBuildResult(
                status=WebuiBuildStatus.OK, message=message, node_path=node_path
            )
        warn_fn("  No existing build found. The UI will show a build hint.")
        return WebuiBuildResult(
            status=WebuiBuildStatus.BUILD_FAILED, message=message, node_path=node_path
        )
    except FileNotFoundError:
        message = "Warning: npm not found. Skipping frontend rebuild."
        warn_fn(message)
        return WebuiBuildResult(
            status=WebuiBuildStatus.TOOLCHAIN_ABSENT, message=message
        )
