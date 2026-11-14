# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep Python tools inside the folders the chat may use.

``run_python`` and ``execute_python_file`` run arbitrary code, so the paths that
code spells out — files it reads or changes, folders it lists, and where any
link it makes points — are held to the allowed folders the file tools use,
after following links. A path the code only works out when it runs is beyond
this check, except a link's target: a link nobody can see the end of is refused.
An outside file or folder is asked about first, as read_file asks.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from gaia.agents.base.credential_reads import PYTHON_TOOLS
from gaia.agents.base.denied_effects import (
    COMPUTED_PATH,
    PATH,
    effects_of_python_code,
    python_file_reads,
    python_link_targets,
    python_listed_folders,
)
from gaia.agents.base.verification import NOT_EXECUTED
from gaia.logger import get_logger

log = get_logger(__name__)

READ = "reads"
WRITE = "changes"
LINK = "makes a link to"

#: A script bigger than this is not parsed; the ledger reads no further either.
_MAX_SCRIPT_BYTES = 512 * 1024

_ROUTES = {
    READ: "Use read_file or browse_directory on it instead; they ask the user "
    "for access.",
    WRITE: "Use write_file or edit_file instead; they ask the user for access.",
    LINK: "Work on the files with read_file, write_file or edit_file instead; "
    "they ask the user for access.",
}


def _validator(agent: Any) -> Any:
    reader = getattr(agent, "_read_validator", None)
    return reader() if callable(reader) else None


def _code_and_folders(
    tool_name: str, value: str, cwds: List[str]
) -> Optional[Tuple[str, List[str]]]:
    """The code a Python call runs and the folders its relative paths start in."""
    if PYTHON_TOOLS[tool_name] == "code":
        return value, cwds
    for cwd in cwds:
        script = Path(cwd).joinpath(os.path.expanduser(value))
        if not script.is_file():
            continue
        try:
            if script.stat().st_size > _MAX_SCRIPT_BYTES:
                log.info("Not checking a script's paths: it is over the size limit")
                return None
            source = script.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            # The tool's own read fails the same way and reports it.
            log.warning(
                "Could not read a script to check its paths (errno %s)", exc.errno
            )
            return None
        return source, [str(script.parent), *cwds]
    return None


def _touched(code: str, cwd: str) -> List[Tuple[str, Optional[str]]]:
    """``(how, path)`` for each path *code* names, relative ones joined to *cwd*.

    A link's path is None when the code works it out at run time.
    """
    from gaia.agents.tools.shell_tools import read_targets_in_words

    reads, commands = python_file_reads(code, cwd)
    reads += python_listed_folders(code, cwd)
    reads += [path for argv in commands for path in read_targets_in_words(argv)]
    found: List[Tuple[str, Optional[str]]] = [(READ, path) for path in reads]
    found += [
        (WRITE, effect.target)
        for effect in effects_of_python_code(code, cwd)
        if effect.kind == PATH and effect.target != COMPUTED_PATH
    ]
    found += [(LINK, target) for target in python_link_targets(code, cwd)]
    return [
        (how, os.path.join(cwd, os.path.expanduser(path)) if path else None)
        for how, path in found
    ]


def _in_temp(real: str) -> bool:
    from gaia import security

    return any(
        security._path_is_within(Path(real), Path(os.path.realpath(root)))
        for root in security._system_temp_roots()
    )


def _in_scope(validator: Any, how: str, real: str, ask: bool) -> bool:
    if how == READ:
        return bool(validator.validate_read(real, prompt_user=ask)[0])
    return bool(validator.is_path_allowed(real, prompt_user=ask))


def _allowed(agent: Any, validator: Any, how: str, real: str) -> Tuple[bool, str]:
    """Whether *real* may be reached, and why no approval could grant it if not.

    An outside file or folder is asked about through the same access prompt
    ``read_file`` uses, which refuses without asking when nobody is watching.
    A link pointing outside is never asked about.
    """
    if _in_scope(validator, how, real, ask=False) or _in_temp(real):
        return True, ""
    final = validator.grant_refusal_reason(real)
    if final:
        return False, final
    if how == LINK:
        return False, ""
    grants = getattr(agent, "_path_access_grants_unasked", None)
    if callable(grants) and grants():
        return True, ""
    return _in_scope(validator, how, real, ask=True), ""


def _refusal(error: str) -> Dict[str, Any]:
    return {**NOT_EXECUTED, "status": "error", "error": error, "has_errors": True}


def python_scope_refusal(
    agent: Any,
    tool_name: str,
    tool_args: Optional[Dict[str, Any]],
    cwds: Iterable[str],
) -> Optional[Dict[str, Any]]:
    """The refusal for Python code that reaches outside the allowed folders, or None.

    Paths are resolved with ``os.path.realpath`` first, so a link inside an
    allowed folder is judged by where it leads. A path that can't be resolved
    is refused. No tier: approving the call does not lift it.
    """
    key = PYTHON_TOOLS.get(tool_name)
    value = (tool_args or {}).get(key) if key else None
    if not isinstance(value, str) or not value.strip():
        return None
    validator = _validator(agent)
    if validator is None:
        return None
    folders = list(dict.fromkeys(str(c) for c in cwds if c)) or [os.getcwd()]
    loaded = _code_and_folders(tool_name, value, folders)
    if loaded is None:
        return None
    code, folders = loaded
    # A relative path is judged from every allowed folder it may run in.
    starts = [f for f in folders if validator.is_path_allowed(f, prompt_user=False)]
    for cwd in starts or folders[:1]:
        for how, path in _touched(code, cwd):
            if path is None:
                log.info("Refused %s: it makes a link to a computed path", tool_name)
                return _refusal(
                    "Not run: this Python code makes a link whose target it works "
                    "out when it runs, so where the link leads can't be checked. "
                    + _ROUTES[LINK]
                )
            try:
                real = os.path.realpath(path)
                allowed, final = _allowed(agent, validator, how, real)
            except (OSError, ValueError) as exc:
                log.info(
                    "Refused %s: a path it %s could not be resolved (%s)",
                    tool_name,
                    how,
                    type(exc).__name__,
                )
                return _refusal(
                    f"Not run: this Python code {how} '{path}', and where that "
                    f"leads could not be checked ({exc}). " + _ROUTES[how]
                )
            if allowed:
                continue
            log.info(
                "Refused %s: it %s a path outside the allowed folders", tool_name, how
            )
            return _refusal(_outside_message(validator, how, path, real, final))
    return None


def _outside_message(validator: Any, how: str, path: str, real: str, final: str) -> str:
    same = os.path.normcase(real) == os.path.normcase(os.path.abspath(path))
    leads = "" if same else f", which leads to '{real}'"
    where = (
        f"Not run: this Python code {how} '{path}'{leads}, outside the folders "
        "this chat may use."
    )
    if final:
        return (
            f"{where} It is a protected location: no approval or full access lifts "
            "this, so tell the user it was not done."
        )
    declined = getattr(validator, "_declined_by_user", None)
    if how != LINK and callable(declined) and declined(real):
        return (
            f"{where} The user declined access to it when asked. Don't reach it, "
            "or what it holds, another way."
        )
    return f"{where} {_ROUTES[how]}"
