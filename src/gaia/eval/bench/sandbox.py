# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An OS-level fence around the agent under test, the same for every harness.

Both harnesses can reach the whole disk, and the disk holds answer keys: the
task definitions with their probes and reference answers, other tasks' work,
and earlier runs' results. A lab run once read an upstream fix out of a cache
and copied it byte for byte.

macOS ``sandbox-exec`` enforces the fence below the agent, so no tool, script
or shell redirect gets round it. Contents are fenced, not existence: a path can
be listed but not read. There is no equivalent here for Linux, and the fence is
refused there rather than pretended (see ``config.resolve``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, List, Sequence

SANDBOX_EXEC = "/usr/bin/sandbox-exec"


class FenceUnavailable(RuntimeError):
    """A fence was requested on a host that cannot enforce one."""


def _q(path: Path) -> str:
    return (
        '"' + str(Path(path).resolve()).replace("\\", "\\\\").replace('"', '\\"') + '"'
    )


def profile(
    fenced: Iterable[Path],
    read_write: Iterable[Path] = (),
    read_only: Iterable[Path] = (),
) -> str:
    """Deny reading and writing *fenced*, then reopen the paths the agent needs.

    Later rules win in SBPL. An allow rule must name ``file-read-data``
    explicitly: ``file-read*`` alone does not override a ``file-read-data``
    deny, and the agent then cannot read its own workdir.
    """
    lines = ["(version 1)", "(allow default)"]
    lines += [f"(deny file-read-data file-write* (subpath {_q(p)}))" for p in fenced]
    lines += [
        f"(allow file-read-data file-read* file-write* (subpath {_q(p)}))"
        for p in read_write
    ]
    lines += [f"(allow file-read-data file-read* (subpath {_q(p)}))" for p in read_only]
    return "\n".join(lines)


def wrap(
    cmd: Sequence[str],
    fenced: Iterable[Path],
    read_write: Iterable[Path] = (),
    read_only: Iterable[Path] = (),
) -> List[str]:
    """*cmd* run inside the fence."""
    if sys.platform != "darwin" or not Path(SANDBOX_EXEC).exists():
        raise FenceUnavailable(
            f"The fence needs macOS {SANDBOX_EXEC}; this host ({sys.platform}) has "
            "none. Run without --fence."
        )
    return [SANDBOX_EXEC, "-p", profile(fenced, read_write, read_only), *cmd]
