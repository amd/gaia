# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CLI for ``gaia agent-program status`` and ``gaia agent-program download``.

Getting gaiacpp for the terminal UI.

The terminal UI shells out here rather than repeating the release lookup, the
checksum check and the install in Go; the Agent UI calls the same
:mod:`gaia.daemon.sidecars.gaiacpp_fetch` through its own route.

``status --json`` prints one JSON object. ``download --json`` prints one JSON
object per line: ``progress`` events, then ``done`` or ``failed``. Exit codes:
``0`` ok, ``1`` gaiacpp cannot be downloaded or the download failed (the reason
is in the output).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, Optional

#: Seconds between two progress lines; the last byte is always reported.
_PROGRESS_EVERY_S = 0.2


def add_subparser(subparsers) -> None:
    parser = subparsers.add_parser(
        "agent-program",
        help="Check for or download gaiacpp, the native GAIA agent (Beta)",
    )
    actions = parser.add_subparsers(dest="agent_program_action", help="Action")
    status = actions.add_parser(
        "status", help="Say whether this GAIA release has a gaiacpp for this PC"
    )
    status.add_argument("--json", action="store_true", help="Print JSON")
    download = actions.add_parser(
        "download", help="Download, verify and install this release's gaiacpp"
    )
    download.add_argument("--json", action="store_true", help="Print JSON lines")
    parser.set_defaults(action="agent-program")


def _print(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload), flush=True)


def status_report() -> Dict[str, Any]:
    """Whether gaiacpp is here, and whether this release can supply it."""
    from gaia.daemon.sidecars import fetch, gaiacpp_fetch
    from gaia.daemon.sidecars.errors import IntegrityError, PlatformError

    installed = fetch.native_flagship()
    report: Dict[str, Any] = {
        "installed": installed is not None,
        "path": str(installed) if installed else None,
        "target": str(gaiacpp_fetch.install_target()),
        "can_download": False,
        "size_bytes": None,
        "version": None,
        "reason": None,
        # True when asking again may answer differently: the network, not the release.
        "retry": False,
    }
    try:
        info = gaiacpp_fetch.download_info()
    except (PlatformError, IntegrityError) as e:
        report["reason"] = str(e)
        return report
    except RuntimeError as e:
        report.update(reason=str(e), retry=True)
        return report
    report.update(can_download=True, size_bytes=info.size, version=info.version)
    return report


def _download(as_json: bool) -> int:
    from gaia.daemon.sidecars import gaiacpp_fetch
    from gaia.daemon.sidecars.errors import SidecarError

    last = {"at": 0.0}

    def on_progress(done: int, total: Optional[int]) -> None:
        now = time.monotonic()
        if done != total and now - last["at"] < _PROGRESS_EVERY_S:
            return
        last["at"] = now
        if as_json:
            _print({"event": "progress", "bytes_done": done, "bytes_total": total})

    try:
        target = gaiacpp_fetch.download(on_progress)
    except (SidecarError, RuntimeError) as e:
        if as_json:
            _print({"event": "failed", "error": str(e)})
        else:
            print(f"gaiacpp was not installed: {e}", file=sys.stderr)
        return 1
    if as_json:
        _print({"event": "done", "path": str(target)})
    else:
        print(f"gaiacpp installed at {target}")
    return 0


def handle(args) -> int:
    action = getattr(args, "agent_program_action", None)
    as_json = bool(getattr(args, "json", False))
    if action == "status":
        report = status_report()
        if as_json:
            _print(report)
        elif report["can_download"]:
            print(f"gaiacpp {report['version']} can be downloaded for this PC.")
        else:
            print(f"gaiacpp cannot be downloaded: {report['reason']}")
        return 0 if report["can_download"] else 1
    if action == "download":
        return _download(as_json)
    print("usage: gaia agent-program {status|download} [--json]", file=sys.stderr)
    return 2
