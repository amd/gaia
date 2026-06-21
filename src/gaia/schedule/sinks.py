"""Output sinks for scheduled runs.

A sink decides *where* a scheduled run's output goes. Per the GAIA no-fallback
rule, a sink that cannot deliver raises an actionable error — it never silently
swallows the failure.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict

import requests

TELEGRAM_API = "https://api.telegram.org"


def dispatch(sink: str, sink_args: Dict[str, Any], output: str) -> None:
    """Route ``output`` to the named sink.

    Raises on any delivery failure with a message naming what failed, what the
    caller should do, and where to look.
    """
    if sink == "stdout":
        _to_stdout(output)
    elif sink == "notification":
        _to_notification(output, sink_args)
    elif sink == "telegram":
        _to_telegram(output, sink_args)
    elif sink.startswith("file:"):
        _to_file(sink[len("file:") :], output)
    elif sink == "file":
        path = sink_args.get("path")
        if not path:
            raise ValueError(
                "file sink requires a path: use --sink file:/path/to/log.md "
                "or pass sink_args.path in schedules.toml"
            )
        _to_file(path, output)
    else:
        raise ValueError(
            f"unknown sink {sink!r}; valid sinks are: stdout, file:<path>, "
            f"notification, telegram"
        )


def _to_stdout(output: str) -> None:
    print(output, flush=True)


def _to_file(path: str, output: str) -> None:
    target = Path(os.path.expanduser(path))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(output.rstrip("\n") + "\n")
    except OSError as e:
        raise OSError(
            f"file sink could not append to {target}: {e}. "
            f"Check the path exists and is writable."
        ) from e


def _to_notification(output: str, sink_args: Dict[str, Any]) -> None:
    title = sink_args.get("title", "GAIA schedule")
    system = platform.system()
    try:
        if system == "Darwin":
            script = f"display notification {_osa(output)} with title {_osa(title)}"
            subprocess.run(["osascript", "-e", script], check=True)
        elif system == "Linux":
            subprocess.run(["notify-send", title, output], check=True)
        else:
            raise NotImplementedError(
                f"notification sink is not implemented for {system!r}; "
                f"use --sink stdout, file:<path>, or telegram instead"
            )
    except FileNotFoundError as e:
        tool = "osascript" if system == "Darwin" else "notify-send"
        raise FileNotFoundError(
            f"notification sink needs {tool!r} on PATH ({system}): {e}. "
            f"Install it or choose a different --sink."
        ) from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"notification sink failed to post (exit {e.returncode}). "
            f"Choose a different --sink or check the desktop notification daemon."
        ) from e


def _osa(text: str) -> str:
    """Quote a string for embedding inside an AppleScript literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _to_telegram(output: str, sink_args: Dict[str, Any]) -> None:
    token = sink_args.get("token") or os.environ.get("GAIA_TELEGRAM_TOKEN")
    to = sink_args.get("to")
    if not token:
        raise ValueError(
            "telegram sink needs a bot token: set GAIA_TELEGRAM_TOKEN or pass "
            "sink_args.token in schedules.toml"
        )
    if not to:
        raise ValueError(
            "telegram sink needs a recipient: pass --to <user_id> "
            "(stored as sink_args.to)"
        )
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": to, "text": output}, timeout=30)
    except requests.RequestException as e:
        raise RuntimeError(
            f"telegram sink could not reach {TELEGRAM_API}: {e}. "
            f"Check network connectivity."
        ) from e
    if resp.status_code != 200:
        raise RuntimeError(
            f"telegram sink got HTTP {resp.status_code} from sendMessage: "
            f"{resp.text[:200]}. Verify the bot token and that chat_id {to!r} "
            f"has started a conversation with the bot."
        )
