# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""gaiacpp's tool calls, read as the flagship's so the UI words them the same.

gaiacpp names its tools and their arguments differently from the Python agent
(``bash_execute`` / ``file_edit`` with ``old_string``), and every card, label
and summary in the Agent UI is keyed on the Python names. This is a display
translation only: the sidecar's own ``confirm_id`` and ``always_scope`` are
never touched, so approvals and grants still reach the tool that asked.
"""

import re
from typing import Any, Dict, Optional, Tuple

_DENIED_BY_USER_RE = re.compile(r"^Tool '[^']+' execution denied by user$")

#: gaiacpp tool -> (flagship tool, {gaiacpp argument: flagship argument}).
_TWINS: Dict[str, Tuple[str, Dict[str, str]]] = {
    "bash_execute": ("run_shell_command", {}),
    "file_read": ("read_file", {"path": "file_path"}),
    "file_write": ("write_file", {"path": "file_path"}),
    "file_edit": (
        "edit_file",
        {
            "path": "file_path",
            "old_string": "old_content",
            "new_string": "new_content",
        },
    ),
    "web_fetch": ("fetch_page", {}),
    "web_search": ("search_web", {"max_results": "num_results"}),
    "transcribe_audio": ("transcribe_media", {"path": "file_path"}),
    "search_code": ("search_code_index", {}),
}

#: gaiacpp tools the Python agent has no twin for, in the profile's label style.
NATIVE_TOOL_LABELS: Dict[str, str] = {
    "git_status": "Checking git status",
    "git_diff": "Reading changes",
    "git_log": "Reading git history",
    "git_show": "Reading a commit",
    "env_inspect": "Checking this machine",
    "delegate_task": "Handing work to helpers",
    "tasks": "Tracking progress",
    "list_mcp_prompts": "Listing a connector's prompts",
    "get_mcp_prompt": "Reading a connector's prompt",
    "list_mcp_resources": "Listing a connector's resources",
    "read_mcp_resource": "Reading a connector's resource",
}

#: Tools both agents name the same way, whose gaiacpp argument names differ.
_SAME_NAME_ARG_RENAMES: Dict[str, Dict[str, str]] = {
    "index_document": {"path": "file_path"},
}

#: Every gaiacpp-only tool name this module words, for the drift test.
NATIVE_TOOL_NAMES = frozenset(_TWINS) | frozenset(NATIVE_TOOL_LABELS) | {"file_search"}


def as_flagship_call(tool: str, args: Any) -> Tuple[str, Any]:
    """*tool* and *args* under the flagship's names; unchanged for any other tool."""
    if tool == "file_search":
        return _file_search(args)
    twin = _TWINS.get(tool)
    if twin is None:
        renames = _SAME_NAME_ARG_RENAMES.get(tool)
        if not renames or not isinstance(args, dict):
            return tool, args
        return tool, {
            renames[key] if key in renames and renames[key] not in args else key: value
            for key, value in args.items()
        }
    # The Python agent's own search_code is a pattern search, not the code index.
    if tool == "search_code" and isinstance(args, dict) and "pattern" in args:
        return tool, args
    name, renames = twin
    if not isinstance(args, dict) or not renames:
        return name, args
    return name, {renames.get(key, key): value for key, value in args.items()}


def _file_search(args: Any) -> Tuple[str, Any]:
    """One gaiacpp tool finds files by name and, given ``content_pattern``, by text."""
    if not isinstance(args, dict):
        return "search_file", args
    out = {k: v for k, v in args.items() if k not in ("pattern", "path")}
    if "path" in args:
        out["directory"] = args["path"]
    if args.get("content_pattern"):
        out["pattern"] = out.pop("content_pattern")
        if "pattern" in args:
            out["file_pattern"] = args["pattern"]
        return "search_file_content", out
    out.pop("content_pattern", None)
    if "pattern" in args:
        out["file_pattern"] = args["pattern"]
    return "search_file", out


def _count(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def is_native_denied(tool: str, data: Any) -> bool:
    """Whether *data* is a user-declined result from a gaiacpp tool."""
    if not isinstance(data, dict):
        return False
    err = data.get("error")
    if isinstance(err, str) and _DENIED_BY_USER_RE.match(err.strip()):
        return True
    if tool not in NATIVE_TOOL_NAMES:
        return False
    return data.get("status") == "denied" or data.get("already_declined") is True


def normalize_native_result(tool: str, data: Any) -> Any:
    """Ensure a gaiacpp user-declined tool result carries ``status: "denied"``."""
    if not is_native_denied(tool, data) or data.get("status") == "denied":
        return data
    return {**data, "status": "denied"}


def native_result_summary(tool: str, data: Any) -> Optional[str]:
    """A gaiacpp tool's result in plain words, or None to summarize it generically.

    *tool* is gaiacpp's own name. Failures generally return None so the generic
    path shows them, except declined gaiacpp calls (whose raw error exposes the
    C++ tool name) and ``web_fetch`` (which returns its plain error text
    directly so the UI does not prepend ``error: `` or clip it mid-word).
    """
    if not isinstance(data, dict):
        return None
    if is_native_denied(tool, data):
        if data.get("already_declined") is True:
            return "Not run (declined earlier)"
        return "Declined by user"
    if tool in ("web_fetch", "web_search") and data.get("error"):
        err = str(data["error"]).strip()
        http_status = data.get("http_status")
        if (
            isinstance(http_status, int)
            and http_status > 0
            and str(http_status) not in err
        ):
            return f"HTTP {http_status}: {err}"
        return err
    if data.get("error"):
        return None
    path = data.get("path")
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1] if path else "the file"
    if tool == "file_edit" and isinstance(data.get("replacements"), int):
        return f"Changed {_count(data['replacements'], 'place', 'places')} in {name}"
    if tool == "file_write" and isinstance(data.get("bytes_written"), int):
        verb = "Replaced" if data.get("recreated") else "Saved"
        return f"{verb} {name} ({_count(data['bytes_written'], 'byte', 'bytes')})"
    if tool == "file_read" and isinstance(data.get("lines"), int):
        cut = ", shortened" if data.get("truncated") else ""
        return f"Read {_count(data['lines'], 'line', 'lines')} from {name}{cut}"
    if tool == "file_search" and isinstance(data.get("matches"), list):
        total = data.get("total")
        found = total if isinstance(total, int) else len(data["matches"])
        return f"Found {_count(found, 'match', 'matches')}"
    if tool == "bash_execute" and "exit_code" in data:
        return _shell_summary(data)
    if tool == "web_fetch" and isinstance(data.get("total_chars"), int):
        cut = ", shortened" if data.get("truncated") else ""
        read = _count(data["total_chars"], "character", "characters")
        return f"Read {read} from the page{cut}"
    if tool == "web_search" and isinstance(data.get("results"), list):
        found = len(data["results"])
        if found == 0:
            return "No results found"
        return f"Found {_count(found, 'result', 'results')}"
    return None


def _shell_summary(data: Dict[str, Any]) -> str:
    if data.get("cancelled"):
        return "Command stopped"
    if data.get("timed_out"):
        return "Command timed out"
    code = data.get("exit_code")
    stdout = str(data.get("stdout") or "").strip()
    if code not in (0, None) and isinstance(data.get("expected_exit"), str):
        return f"{data['expected_exit']} (exit {code})"
    if code not in (0, None):
        detail = (str(data.get("stderr") or "").strip() or stdout)[:150]
        return f"Command failed (exit {code})" + (f": {detail}" if detail else "")
    if not stdout:
        return "Command completed (no output)"
    lines = stdout.split("\n")
    preview = "\n".join(lines[:5])
    if len(lines) > 5:
        preview += f"\n... ({len(lines)} lines total)"
    return preview


def native_prompt_summary(tool: str, summary: str, label: str) -> str:
    """gaiacpp's prompt summary without its tool name: the command, or *label*."""
    if tool == "bash_execute" and summary.startswith("Run: "):
        return summary[len("Run: ") :]
    if summary == f"Use {tool}":
        return f"{label}?"
    return summary
