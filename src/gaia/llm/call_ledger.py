# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What this process last asked Lemonade to do, kept in memory for a fault record.

A failure record taken after the fact can see the server, but not the request
that failed: which window a load asked for and why, how big the prompt and
tool schema were. ``LemonadeClient`` notes each load and chat request here as
it sends it, and ``failure_record`` copies the notes into the record. Nothing
is written to disk or sent anywhere by this module; message text is never kept,
only its size.

Loads are also kept in ``<GAIA home>/failures/recent-loads.json``: in the Agent
UI the backend loads a model and the agent's own process then chats with it,
so the process that fails often isn't the one that loaded.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger
from gaia.utils.file_lock import file_lock, write_text_atomic

log = get_logger(__name__)

#: Calls kept; a failure needs the load and the first chat after it.
KEEP_CALLS = 8

_lock = threading.Lock()
_calls: deque = deque(maxlen=KEEP_CALLS)
_ctx_choices: Dict[str, Dict[str, Any]] = {}
_memory_checks: Dict[str, Dict[str, Any]] = {}
#: Loads kept on disk, across every GAIA process.
KEEP_SHARED_LOADS = 6
#: A load's note gives up on the shared file's lock after this, rather than stall the load.
_SHARE_LOCK_TIMEOUT_S = 5


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def note_ctx_choice(model: str, ctx: int, how: str, **inputs: Any) -> None:
    """How the window GAIA asks *model* to load with was chosen, and from what."""
    with _lock:
        _ctx_choices[model] = {"ctx_size": ctx, "how": how, **inputs, "at": _now()}


def note_memory_check(model: str, **fields: Any) -> None:
    """The free-memory check a load of *model* passed or failed."""
    with _lock:
        _memory_checks[model] = {**fields, "at": _now()}


def begin(kind: str, **fields: Any) -> Dict[str, Any]:
    """Note a request about to go out; pass the result to :func:`end`."""
    entry = {"kind": kind, **fields, "started": _now(), "_t0": time.monotonic()}
    with _lock:
        _calls.append(entry)
    return entry


def end(
    entry: Dict[str, Any], error: Optional[BaseException] = None, outcome: str = ""
) -> None:
    """Close *entry*: how long it took and how it ended."""
    with _lock:
        entry["ended"] = _now()
        entry["seconds"] = round(time.monotonic() - entry.pop("_t0", 0.0), 3)
        if error is None:
            entry["outcome"] = outcome or "ok"
            return
        entry["outcome"] = outcome or "failed"
        entry["error"] = f"{type(error).__name__}: {str(error)[:1000]}"
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        if status is not None:
            entry["http_status"] = status
            entry["http_body"] = (getattr(response, "text", "") or "")[:500]
    if entry["kind"] == "load":
        _share_load(entry)


def shared_loads_path() -> Path:
    from gaia.home import gaia_home

    return gaia_home() / "failures" / "recent-loads.json"


def _share_load(entry: Dict[str, Any]) -> None:
    """Add a finished load to the file every GAIA process's fault record reads."""
    with _lock:
        model = (entry.get("request") or {}).get("model_name")
        shared = {
            **entry,
            "pid": os.getpid(),
            "ctx_choice": _ctx_choices.get(model),
            "memory_check": _memory_checks.get(model),
        }
    path = shared_loads_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Locked: the UI backend and the agent can finish a load at the same moment.
        with file_lock(path, timeout=_SHARE_LOCK_TIMEOUT_S):
            loads = (shared_loads() + [shared])[-KEEP_SHARED_LOADS:]
            write_text_atomic(path, json.dumps(loads, default=str))
    except OSError as e:
        log.warning("Couldn't note the model load in %s: %s", path, e)


def shared_loads() -> List[Dict[str, Any]]:
    """The last loads any GAIA process made, oldest first."""
    path = shared_loads_path()
    if not path.exists():
        return []
    try:
        with file_lock(path, timeout=_SHARE_LOCK_TIMEOUT_S):
            loads = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        log.warning("Couldn't read %s: %s", shared_loads_path(), e)
        return []
    return loads if isinstance(loads, list) else []


def chat_shape(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    max_tokens: Optional[int],
    stream: bool,
) -> Dict[str, Any]:
    """A chat request's size, never its words."""
    chars = sum(len(json.dumps(m.get("content", ""), default=str)) for m in messages)
    schema_bytes = len(json.dumps(tools, default=str)) if tools else 0
    return {
        "messages": len(messages),
        "prompt_chars": chars,
        # ~4 characters a token; Lemonade's own count is in its log.
        "prompt_tokens_estimate": (chars + schema_bytes) // 4,
        "tools": len(tools or []),
        "tool_schema_bytes": schema_bytes,
        "max_tokens": max_tokens,
        "stream": stream,
    }


def snapshot() -> Dict[str, Any]:
    """Every note this process holds, oldest call first."""
    with _lock:
        calls = [{k: v for k, v in c.items() if k != "_t0"} for c in _calls]
        mine = {
            "pid": os.getpid(),
            "calls": calls,
            "ctx_choices": dict(_ctx_choices),
            "memory_checks": dict(_memory_checks),
        }
    mine["loads_by_other_processes"] = [
        load for load in shared_loads() if load.get("pid") != os.getpid()
    ]
    return mine


def clear() -> None:
    with _lock:
        _calls.clear()
        _ctx_choices.clear()
        _memory_checks.clear()
