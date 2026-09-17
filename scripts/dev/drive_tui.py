# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Drive a running TUI through its control API — a test client, not a feature.

The TUI exposes a loopback control API under ``--control`` so an assistant can
type into a real session. This wraps it so a demo or a regression check can be
scripted end to end instead of someone watching a terminal for twelve minutes
and reporting "looked fine".

    gaia-tui --control &                # writes ~/.gaia/tui/control.json
    python scripts/dev/drive_tui.py screen
    python scripts/dev/drive_tui.py ask "transcribe C:/clip.mp4" --wait-idle 900

``wait`` caps at ten minutes per call, which a long transcription outruns, so
``--wait-idle`` polls instead and reports progress as it goes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

CONTROL_FILE = Path.home() / ".gaia" / "tui" / "control.json"
API_PREFIX = "/control/v1"

# The TUI draws in box characters and emoji; a Windows console defaults to
# cp1252 and a plain print() of a captured frame dies on the first border.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


class ControlError(RuntimeError):
    pass


def _info() -> Dict[str, Any]:
    if not CONTROL_FILE.exists():
        raise ControlError(
            f"No TUI control file at {CONTROL_FILE}.\n"
            "Start one with:  gaia-tui --control\n"
            "(bare `gaia-tui` does not expose the API)"
        )
    return json.loads(CONTROL_FILE.read_text(encoding="utf-8"))


def call(path: str, payload: Optional[dict] = None, timeout: float = 60.0) -> Any:
    """POST (or GET when payload is None) one control endpoint."""
    info = _info()
    url = f"http://{info.get('host', '127.0.0.1')}:{info['port']}{API_PREFIX}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method="POST" if data else "GET",
        headers={
            "Authorization": f"Bearer {info['token']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise ControlError(f"{path} -> HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise ControlError(
            f"{path} unreachable at {url}: {e.reason}. Is the TUI still running?"
        ) from e
    try:
        return json.loads(body)
    except ValueError:
        return body


def screen() -> str:
    out = call("/screen")
    if isinstance(out, dict):
        return out.get("screen") or out.get("text") or json.dumps(out, indent=2)
    return str(out)


def status() -> dict:
    out = call("/status")
    return out if isinstance(out, dict) else {"raw": out}


def ask(text: str) -> None:
    call("/text", {"text": text})
    call("/keys", {"keys": ["enter"]})


# The spinner is the only reliable "still working" signal: the transcript tool
# runs for minutes without printing, so an idle screen-diff would call it done.
_BUSY = re.compile(r"esc to (interrupt|stop)|thinking|working|⏳|▰|⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏")


def wait_idle(limit: float, quiet_for: float = 6.0, verbose: bool = True) -> bool:
    """Block until the agent stops working. True if it finished inside limit."""
    started = time.time()
    last_change = time.time()
    previous = ""
    calm_since: Optional[float] = None

    while time.time() - started < limit:
        current = screen()
        if current != previous:
            last_change = time.time()
            previous = current
            if verbose:
                tail = [ln for ln in current.splitlines() if ln.strip()][-1:]
                elapsed = int(time.time() - started)
                print(f"  [{elapsed:>4}s] {tail[0][:110] if tail else ''}", flush=True)

        busy = bool(_BUSY.search(current.lower()))
        if busy:
            calm_since = None
        elif calm_since is None:
            calm_since = time.time()

        # Settled means: not spinning AND the screen stopped changing. Either
        # alone lies — a spinner can sit on an unchanging frame, and streaming
        # output changes constantly while still mid-answer.
        if (
            calm_since is not None
            and time.time() - calm_since >= quiet_for
            and time.time() - last_change >= quiet_for
        ):
            return True
        time.sleep(2.0)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("screen", help="print the current screen")
    sub.add_parser("status", help="print the session status")
    p_ask = sub.add_parser("ask", help="type a prompt and press enter")
    p_ask.add_argument("text")
    p_ask.add_argument(
        "--wait-idle",
        type=float,
        default=0,
        metavar="SECONDS",
        help="after sending, block until the agent settles, then print the screen",
    )
    p_wait = sub.add_parser("wait", help="block until the agent settles")
    p_wait.add_argument("--limit", type=float, default=900)
    p_keys = sub.add_parser("keys", help="send raw keys, e.g. enter ctrl+c")
    p_keys.add_argument("keys", nargs="+")

    args = parser.parse_args()
    try:
        if args.cmd == "screen":
            print(screen())
        elif args.cmd == "status":
            print(json.dumps(status(), indent=2))
        elif args.cmd == "keys":
            call("/keys", {"keys": args.keys})
        elif args.cmd == "wait":
            if not wait_idle(args.limit):
                print(f"still busy after {args.limit}s", file=sys.stderr)
                return 2
            print(screen())
        elif args.cmd == "ask":
            ask(args.text)
            if args.wait_idle:
                if not wait_idle(args.wait_idle):
                    print(f"still busy after {args.wait_idle}s", file=sys.stderr)
                    print(screen())
                    return 2
                print(screen())
    except ControlError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
