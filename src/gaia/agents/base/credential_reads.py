# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Refuse Python code that names a credential file before it runs.

``security.is_read_blocked`` keeps ``.env``, private keys and the rest out of
the read tools. ``run_python`` and ``execute_python_file`` run arbitrary code,
so the paths that code spells out are held to the same rule. A path the code
only computes when it runs is beyond this check.
"""

import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from gaia.agents.base.denied_effects import python_strings
from gaia.agents.base.verification import NOT_EXECUTED
from gaia.logger import get_logger

log = get_logger(__name__)

#: Tools that run Python, and the argument carrying the code or the script.
PYTHON_TOOLS = {"run_python": "code", "execute_python_file": "file_path"}


def credential_in_python(code: str, cwds: Iterable[str]) -> Optional[str]:
    """The first credential file *code* names, judged from each of *cwds*."""
    from gaia import security

    folders = list(dict.fromkeys(str(c) for c in cwds if c)) or [os.getcwd()]
    for text in python_strings(code, folders[0]):
        for cwd in folders:
            found = security.credential_file_read(text, cwd)
            if found is not None:
                return found
    return None


def python_credential_refusal(
    tool_name: str, tool_args: Optional[Dict[str, Any]], cwds: Iterable[str]
) -> Optional[Dict[str, Any]]:
    """The hard refusal for a Python call that reads a credential file, or None.

    No tier: neither an approval nor full access lifts it.
    """
    key = PYTHON_TOOLS.get(tool_name)
    value = (tool_args or {}).get(key) if key else None
    if not isinstance(value, str) or not value.strip():
        return None
    cwds = list(cwds)
    if key == "code":
        found = credential_in_python(value, cwds)
    else:
        found = _script_credential(value, cwds)
    if found is None:
        return None
    log.info("Refused %s: its code reads the credential file %s", tool_name, found)
    return {
        **NOT_EXECUTED,
        "status": "error",
        "error": (
            f"Not run: this Python code reads '{found}', which holds credentials, "
            "keys or secrets. Python tools may not read it, even with full access "
            "or after approval. Ask the user to open the file themselves."
        ),
        "has_errors": True,
    }


def _script_credential(file_path: str, cwds: list) -> Optional[str]:
    """The credential file a script on disk names, or the script itself."""
    from gaia import security

    cwds = list(dict.fromkeys(str(c) for c in cwds if c)) or [os.getcwd()]
    for cwd in cwds:
        found = security.credential_file_read(file_path, cwd)
        if found is not None:
            return found
    for cwd in cwds:
        script = Path(cwd).joinpath(os.path.expanduser(file_path))
        if not script.is_file():
            continue
        try:
            source = script.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            # The tool's own read fails the same way and reports it.
            log.warning("Could not read %s to check what it reads: %s", script, exc)
            return None
        return credential_in_python(source, [str(script.parent), *cwds])
    return None
