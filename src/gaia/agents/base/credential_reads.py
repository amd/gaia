# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Refuse Python code that names a credential file before it runs.

``PathValidator.is_read_blocked`` keeps ``.env``, private keys and the rest out of
the read tools. ``run_python`` and ``execute_python_file`` run arbitrary code,
so the paths that code spells out are held to the same rule. A path the code
only computes when it runs is beyond this check.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from gaia.agents.base.denied_effects import PYTHON_READ_CALLS, python_file_reads
from gaia.agents.base.verification import NOT_EXECUTED
from gaia.logger import get_logger

log = get_logger(__name__)

#: Tools that run Python, and the argument carrying the code or the script.
PYTHON_TOOLS = {"run_python": "code", "execute_python_file": "file_path"}

#: Code that spells none of these names cannot make a read the check finds.
_READ_CALLS = re.compile(
    "|".join(re.escape(name) for name in sorted(PYTHON_READ_CALLS, key=len))
)

#: Past this size, code that names no credential file is not parsed. A link
#: to one behind an ordinary name is then beyond the check.
_FULL_CHECK_LIMIT = 64 * 1024


def _may_read_credentials(code: str) -> bool:
    """False when *code* cannot read a credential file the check would find."""
    if not _READ_CALLS.search(code):
        return False
    if len(code) <= _FULL_CHECK_LIMIT:
        return True
    from gaia import security

    lowered = code.lower()
    return any(n.lower() in lowered for n in security.SENSITIVE_FILE_NAMES) or any(
        e in lowered for e in security.SENSITIVE_EXTENSIONS
    )


def credential_in_python(code: str, cwds: Iterable[str]) -> Optional[str]:
    """The first credential file *code* reads, judged from each of *cwds*.

    A file it opens, copies or reads through ``Path``, or one a shell command
    it runs reads; a string it only prints is not a read.
    """
    from gaia import security
    from gaia.agents.tools.shell_tools import credential_read_in_words

    if not _may_read_credentials(code):
        return None
    folders = list(dict.fromkeys(str(c) for c in cwds if c)) or [os.getcwd()]
    paths, commands = python_file_reads(code, folders[0])
    for cwd in folders:
        for text in paths:
            found = security.credential_file_read(text, cwd)
            if found is not None:
                return found
        for argv in commands:
            hit = credential_read_in_words(argv, cwd)
            if hit is not None:
                return hit[1]
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
        found = script_credential(value, cwds)
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


def script_credential(file_path: str, cwds: list) -> Optional[str]:
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
            log.warning(
                "Could not read a script to check what it reads (errno %s)", exc.errno
            )
            return None
        return credential_in_python(source, [str(script.parent), *cwds])
    return None
