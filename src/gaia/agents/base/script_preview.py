# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The content of a script, shown on the prompt that asks to run it.

``execute_python_file`` names a file, and a path says nothing about what the
file does. The prompt therefore carries the script itself, read when the prompt
is built, and the run is refused when the file no longer holds what was shown.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from gaia.agents.base.call_risk import SCRIPT_ARG
from gaia.agents.base.verification import NOT_EXECUTED
from gaia.logger import get_logger

logger = get_logger(__name__)

#: The one tool whose prompt shows a file's content.
SCRIPT_TOOL = "execute_python_file"

#: Most of a script a prompt carries; the rest is counted, never dropped silently.
SCRIPT_SHOWN_LINES = 400
SCRIPT_SHOWN_CHARS = 48_000
#: Past this a file is not read into the prompt at all.
SCRIPT_MAX_BYTES = 1_000_000

#: Why a script's content is not on its prompt (the ``unreadable`` value).
MISSING = "missing"
NOT_TEXT = "not_text"
TOO_LARGE = "too_large"
UNREADABLE = "unreadable"


@dataclass(frozen=True)
class ScriptPreview:
    """What a prompt shows of a script, and the digest of the file it was read from."""

    shown: Dict[str, Any]
    digest: str


def _digest_and_head(path: str) -> "tuple[str, bytes, bool]":
    """``(sha256, first SCRIPT_MAX_BYTES, whether there was more)`` of *path*."""
    hasher = hashlib.sha256()
    head = b""
    more = False
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1 << 16)
            if not chunk:
                break
            hasher.update(chunk)
            room = SCRIPT_MAX_BYTES - len(head)
            head += chunk[:room]
            more = more or len(chunk) > room
    return hasher.hexdigest(), head, more


def read_script(path: str) -> ScriptPreview:
    """*path* as its prompt shows it; an unreadable file says why instead."""
    try:
        digest, data, more = _digest_and_head(path)
    except FileNotFoundError:
        return ScriptPreview({"unreadable": MISSING}, "")
    except OSError as e:
        logger.warning("could not read %s for its approval prompt: %s", path, e)
        return ScriptPreview({"unreadable": UNREADABLE}, "")
    if more:
        return ScriptPreview({"unreadable": TOO_LARGE}, digest)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ScriptPreview({"unreadable": NOT_TEXT}, digest)
    if "\x00" in text:
        return ScriptPreview({"unreadable": NOT_TEXT}, digest)
    lines = text.splitlines()
    shown = []
    chars = 0
    for line in lines[:SCRIPT_SHOWN_LINES]:
        chars += len(line) + 1
        if chars > SCRIPT_SHOWN_CHARS:
            break
        shown.append(line)
    if lines and not shown:
        return ScriptPreview({"unreadable": TOO_LARGE}, digest)
    return ScriptPreview(
        {"text": "\n".join(shown), "hidden_lines": len(lines) - len(shown)}, digest
    )


def _record_key(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def script_prompt_args(agent: Any, tool_name: str, prompt_args: Any) -> Any:
    """*prompt_args* with the script this call runs, remembered for the run."""
    if tool_name != SCRIPT_TOOL or not isinstance(prompt_args, dict):
        return prompt_args
    named = prompt_args.get("file_path")
    if not isinstance(named, str) or not named.strip():
        return prompt_args
    # Imported here: the tools package imports this one's.
    from gaia.agents.tools.workspace_paths import _resolve_target, _workspace_default

    # Resolved as the tool body resolves it, so the file shown is the file run.
    try:
        path = str(_resolve_target(named, None, _workspace_default(agent)))
    except (OSError, ValueError) as e:
        # Not a path the tool can open either; its body reports that.
        logger.warning("could not resolve %r for its approval prompt: %s", named, e)
        return {**prompt_args, SCRIPT_ARG: {"unreadable": UNREADABLE}}
    preview = read_script(path)
    records = getattr(agent, "_shown_scripts", None)
    if records is None:
        records = agent._shown_scripts = {}
    records[_record_key(path)] = preview.digest
    return {**prompt_args, SCRIPT_ARG: preview.shown}


def changed_script_refusal(
    agent: Any, path: str, data: bytes
) -> Optional[Dict[str, Any]]:
    """A refusal when *data*, about to run, is not what the prompt for *path* showed.

    Consumes the record, so one prompt covers one run. None when no prompt was
    built for *path* (a tool body called directly) or the file is unchanged.
    """
    records = getattr(agent, "_shown_scripts", None) or {}
    shown = records.pop(_record_key(path), None)
    if shown is None or shown == hashlib.sha256(data).hexdigest():
        return None
    logger.warning("%s changed between its approval prompt and its run", path)
    return {
        **NOT_EXECUTED,
        "status": "error",
        "has_errors": True,
        "error": (
            f"{SCRIPT_TOOL} was NOT run: {path} changed after the approval "
            "prompt showed it, so what was approved is not what would run. "
            f"Call {SCRIPT_TOOL} again so the user sees the current file."
        ),
    }


__all__ = [
    "MISSING",
    "NOT_TEXT",
    "SCRIPT_MAX_BYTES",
    "SCRIPT_SHOWN_CHARS",
    "SCRIPT_SHOWN_LINES",
    "SCRIPT_TOOL",
    "TOO_LARGE",
    "UNREADABLE",
    "ScriptPreview",
    "changed_script_refusal",
    "read_script",
    "script_prompt_args",
]
