# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Refuse Python code that runs ``gaia skill`` before it runs.

Promoting, installing or trusting a skill is the user's own terminal step, and
the commands that do it refuse an agent themselves
(:mod:`gaia.skills.human_only`). This is the early, readable refusal for the
common spellings: ``gaia skill`` launched through ``subprocess``,
``os.system``, ``os.popen``, ``pty`` or ``os.exec*`` / ``os.spawn*``; a
module that changes skill trust imported or run in-process; and code that
clears the agent marker.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, Iterable, List, Optional

from gaia.agents.base.credential_reads import PYTHON_TOOLS
from gaia.agents.base.denied_effects import python_file_reads
from gaia.agents.base.python_scope import _code_and_folders
from gaia.agents.base.verification import NOT_EXECUTED
from gaia.logger import get_logger

log = get_logger(__name__)

#: ``runpy.run_module('gaia', ...)`` runs the CLI's ``__main__``.
_RUNS_GAIA = re.compile(r"""run_module\(\s*['"]gaia['"]""")

#: Code that names none of these launches no process this check could follow.
_LAUNCHES = re.compile(r"subprocess|\bsystem\b|popen|\bpty\b|\bexec|\bspawn", re.I)


def _literal_words(code: str) -> List[str]:
    """Every string literal in *code*, split into words, in source order."""
    from gaia.agents.tools.shell_tools import _SCRIPT_WORD_SPLIT

    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        # Python refuses to run it too.
        return []
    literals = sorted(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    return [
        piece
        for node in literals
        for piece in _SCRIPT_WORD_SPLIT.split(node.value)
        if piece
    ]


def _imports_trust_module(code: str) -> bool:
    """Whether *code* imports or runs a module that changes skill trust."""
    from gaia.skills.binaries import TRUST_MODULES

    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names = [node.value]
        else:
            continue
        if any(TRUST_MODULES.match(name) for name in names):
            return True
    return bool(_RUNS_GAIA.search(code))


def human_only_in_python(code: str, cwd: str) -> str:
    """The ``gaia skill`` call *code* would make, or ``""``.

    An early, readable refusal: the trust-changing functions refuse an agent
    themselves (:func:`gaia.skills.human_only.require_user_terminal`).
    """
    from gaia.agents.tools.shell_tools import human_only_in_argv
    from gaia.skills.binaries import HUMAN_ONLY_CALL, human_only_gaia_call
    from gaia.skills.human_only import AGENT_CHILD_ENV

    # Clearing the marker is the one way past the command-side refusal.
    if AGENT_CHILD_ENV.lower() in code.lower():
        return AGENT_CHILD_ENV
    if _imports_trust_module(code):
        return HUMAN_ONLY_CALL
    if not _LAUNCHES.search(code):
        return ""
    _, commands = python_file_reads(code, cwd)
    for argv in commands:
        found = human_only_in_argv(argv)
        if found:
            return found
    return human_only_gaia_call(_literal_words(code))


def python_human_only_refusal(
    tool_name: str, tool_args: Optional[Dict[str, Any]], cwds: Iterable[str]
) -> Optional[Dict[str, Any]]:
    """The final refusal for a Python call that runs ``gaia skill``, or None.

    No tier: neither an approval nor full access lifts it.
    """
    from gaia.agents.tools.shell_tools import NOT_APPROVABLE
    from gaia.skills.binaries import human_only_message

    key = PYTHON_TOOLS.get(tool_name)
    value = (tool_args or {}).get(key) if key else None
    if not isinstance(value, str) or not value.strip():
        return None
    folders = list(dict.fromkeys(str(c) for c in cwds if c))
    if key == "code":
        loaded = (value, folders)
    else:
        loaded = _code_and_folders(tool_name, value, folders)
    if loaded is None:
        return None
    code, folders = loaded
    found = human_only_in_python(code, folders[0] if folders else ".")
    if not found:
        return None
    log.warning(
        "Refused %s: its code runs %s, which is the user's to run", tool_name, found
    )
    return {
        **NOT_EXECUTED,
        "status": "error",
        "error": (
            f"Not run: this Python code launches `{found}` — "
            f"{human_only_message(found)}."
        ),
        "hint": (
            "Tell the user plainly what was asked for and the exact command, so "
            f"they can decide whether to run it themselves. {NOT_APPROVABLE}"
        ),
        "has_errors": True,
    }
