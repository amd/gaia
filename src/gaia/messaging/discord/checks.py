# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Checks: the cheap half of a trigger.

"Alert me if main breaks" is not a summary on a timer. A check is one
read-only command the bridge runs on the schedule's interval with no model in
the loop; GAIA is woken only when what the check prints changes.

The bridge runs it unattended for as long as the trigger exists, so the set
is deliberately small: a GitHub read through ``gh`` (its policy's read tier,
naming the repository, never paginating) or one of ``gaia discord``'s own
reads such as ``ci-status``. Nothing that runs code, touches local files or
depends on the folder it starts in.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

#: A check that has not answered by then is reported as failing.
CHECK_TIMEOUT_SECONDS = 60
#: Kept, compared and shown to the agent; past it the output is clipped.
MAX_OUTPUT_CHARS = 4000
#: The CLIs a check may call, each only within its policy's read tier.
CHECK_BINARIES = frozenset({"gh", "gaia"})

_RULE = (
    "A check runs on its own every few minutes, so it must be one GitHub read "
    "— `gaia discord ci-status --repo OWNER/REPO --branch main`, or a gh read "
    "naming the repo, like `gh pr view 12 --repo OWNER/REPO --json "
    "reviewDecision --jq .reviewDecision`."
)


class CheckError(ValueError):
    """A check GAIA will not run unattended; the message says what to write."""


def check_argv(command: str) -> List[str]:
    """The argv of *command* if it may run as a check; raises ``CheckError``."""
    from gaia.agents.tools.shell_tools import _parse_line
    from gaia.skills.binaries import (
        BINARY_POLICIES,
        normalize_binary,
        policy_argv,
        validate_invocation,
    )

    command = (command or "").strip()
    if not command:
        raise CheckError(f"--check is empty. {_RULE}")
    steps, error = _parse_line(command)
    if error is not None or len(steps) != 1 or len(steps[0].segments) != 1:
        raise CheckError(f"`{command}` is not a single command. {_RULE}")
    argv = list(steps[0].segments[0])
    binary = normalize_binary(argv[0])
    if argv[0] != binary or binary not in CHECK_BINARIES:
        raise CheckError(f"`{command}` is not a GitHub read. {_RULE}")
    if validate_invocation(BINARY_POLICIES[binary], policy_argv(argv)) is not None:
        raise CheckError(f"`{command}` is not a read. {_RULE}")
    if binary == "gh":
        if "--paginate" in argv:
            raise CheckError(f"`{command}` pages through everything. {_RULE}")
        names_repo = any(
            a in ("-R", "--repo") or a.startswith("--repo=") for a in argv
        ) or (len(argv) > 2 and argv[1] == "api" and argv[2].startswith("repos/"))
        if not names_repo:
            raise CheckError(
                f"`{command}` does not name the repository, so it would depend on "
                f"the folder the bridge runs in. {_RULE}"
            )
    return argv


def _program(name: str, env: Optional[Dict[str, str]]) -> Optional[str]:
    program = shutil.which(name, path=(env or {}).get("PATH"))
    # A .cmd or .bat launcher runs through cmd.exe, which re-reads its arguments.
    if program and os.name == "nt" and not program.lower().endswith(".exe"):
        return None
    return program


def run_check(command: str, env: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
    """Run *command* as a check: ``(succeeded, output)``.

    A check that exits non-zero but prints something has still answered
    (``gh pr checks`` exits 1 while checks fail); one that prints nothing has
    failed, and the output is then what went wrong, for the user.
    """
    try:
        argv = check_argv(command)
    except CheckError as e:
        return False, str(e)
    program = _program(argv[0], env)
    if program is None:
        return False, f"`{argv[0]}` is not installed on this PC as a program."
    try:
        proc = subprocess.run(  # nosec B603 - argv from check_argv, no shell
            [program, *argv[1:]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CHECK_TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"`{command}` did not finish within {CHECK_TIMEOUT_SECONDS}s."
    except OSError as e:
        return False, f"`{command}` could not start: {e}"
    output = (proc.stdout or "").strip()
    if proc.returncode != 0 and not output:
        detail = (proc.stderr or "").strip()[:500]
        return False, f"`{command}` exited with {proc.returncode}: {detail}"
    return True, _clip(output)


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n… ({len(text) - MAX_OUTPUT_CHARS} more chars)"
