# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The allowed-folders check every file tool goes through.

No rule lives here. Each function asks the host's ``PathValidator`` — the same
``validate_read`` / ``validate_write`` that ``read_file`` and ``write_file``
use — so a tool that lists, searches, indexes or saves cannot drift from them.
The validator expands ``~`` and resolves ``..`` and symlinks before it decides.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from gaia.agents.base.verification import NOT_EXECUTED
from gaia.agents.tools.search_scope import path_validator_of


def read_access_error(
    host: Any, path: Any, *, prompt_user: bool = True
) -> Optional[Dict[str, Any]]:
    """Refusal for reading or listing *path*, or None when it is allowed.

    Out-of-scope paths go through the validator's approval prompt, exactly as
    ``read_file`` does. A host with no validator is library use with no
    sandbox declared, and is not checked.

    Args:
        host: The agent (or mixin host) whose validator applies.
        path: The path the tool was given.
        prompt_user: Ask the user when *path* is out of scope.

    Returns:
        A tool error dict, or None.
    """
    validator = path_validator_of(host)
    if validator is None:
        return None
    allowed, reason = validator.validate_read(str(path), prompt_user=prompt_user)
    if allowed:
        return None
    return {**NOT_EXECUTED, "status": "error", "error": reason}


def missing_file_error(path: Any) -> Dict[str, Any]:
    """The error for a file that isn't there, with when to stop looking.

    Without the stop, a model handed a wrong absolute path searched folder
    after folder across the whole drive for minutes before telling the user.
    """
    path = str(path)
    parent = os.path.dirname(path)
    name = os.path.basename(path)
    if parent and not os.path.exists(parent):
        where = f" The folder '{parent}' does not exist either."
    elif parent:
        where = f" The folder '{parent}' exists, but the file is not in it."
    else:
        where = ""
    return {
        **NOT_EXECUTED,
        "status": "error",
        "error": (
            f"File not found: {path}.{where} If the user may have the location "
            f"wrong, run one file search for '{name}'. If that finds nothing, stop "
            "and tell the user the file is not on this PC and ask where it is; "
            "do not keep searching other folders or drives."
        ),
    }


def write_access_error(
    host: Any, path: Any, *, content_size: int = 0
) -> Optional[Dict[str, Any]]:
    """Refusal for writing *path*, or None when it is allowed.

    Args:
        host: The agent (or mixin host) whose validator applies.
        path: The file the tool will create or replace.
        content_size: Bytes about to be written, when known.

    Returns:
        A tool error dict, or None.
    """
    validator = path_validator_of(host)
    if validator is None:
        return None
    allowed, reason = validator.validate_write(str(path), content_size=content_size)
    if allowed:
        return None
    return {**NOT_EXECUTED, "status": "error", "error": reason}


def readable_entry(host: Any, path: Any) -> bool:
    """Whether a file met while walking an approved folder may be read.

    Never prompts: the folder was approved, and a symlink or secret inside it
    is skipped rather than turned into a dialog per file.

    Args:
        host: The agent (or mixin host) whose validator applies.
        path: A file found by the walk.

    Returns:
        True when the file is in scope and not a secret.
    """
    return read_access_error(host, path, prompt_user=False) is None
