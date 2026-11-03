# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Start a child in its own process group, and kill the whole tree on timeout."""

import contextlib
import os
import signal
import subprocess

from gaia.logger import get_logger

log = get_logger(__name__)

#: taskkill itself must not become the hang it is ending.
_TASKKILL_TIMEOUT_S = 30

#: Popen kwargs that put the child at the root of a tree :func:`kill_tree` can end.
NEW_GROUP = (
    {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    if os.name == "nt"
    else {"start_new_session": True}
)


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill *proc* and every process it started.

    Killing only the child leaves its descendants (an MCP server, or the node
    process behind Windows' ``claude.cmd``) holding the output pipes, so
    reading them blocks and the timeout fires late or never.
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=_TASKKILL_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning(
                "taskkill did not end the tree under PID %s within %ss; "
                "killing that process only, its children may outlive it.",
                proc.pid,
                _TASKKILL_TIMEOUT_S,
            )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)  # pylint: disable=no-member
    with contextlib.suppress(OSError):
        proc.kill()
