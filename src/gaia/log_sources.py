# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Every log GAIA writes, grouped the way a person troubleshooting looks for it.

The Agent UI's log viewer lists these. Paths come from the resolver each
writer uses, so ``GAIA_HOME``, ``GAIA_LOG_FILE`` and ``GAIA_DAEMON_HOME`` are
honoured; ``gaia diagnostics`` bundles the same files.

``model_server`` is Lemonade's log and is read by :mod:`gaia.llm.lemonade_logs`
(a file for GAIA's own server, a stream for the user's); it lists no files here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

#: In the order the viewer shows them.
SOURCES = ("model_server", "gaia", "service", "agent", "desktop", "other")

#: Sidecar logs kept per agent: one per run, so the newest few are the useful ones.
_SIDECAR_LOGS_PER_AGENT = 5


@dataclass
class LogFile:
    """One log file; ``name`` is what the viewer shows and asks for."""

    name: str
    path: Path

    def to_dict(self) -> Dict[str, object]:
        try:
            stat = self.path.stat()
            size, modified = stat.st_size, stat.st_mtime
        except OSError:
            size, modified = None, None
        return {
            "name": self.name,
            "path": str(self.path),
            "size_bytes": size,
            "modified": modified,
        }


class UnknownLogError(ValueError):
    """The viewer asked for a source or file GAIA doesn't have."""


def _user_gaia_dir() -> Path:
    # The desktop shell writes here whatever GAIA_HOME says, as `gaia diagnostics` reads it.
    from gaia.home import default_gaia_home

    return default_gaia_home()


def _newest_first(paths) -> List[Path]:
    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(paths, key=mtime, reverse=True)


def gaia_log_path() -> Path:
    """The log every GAIA process writes (``gaia.log``)."""
    from gaia.home import gaia_home
    from gaia.logger import LOG_FILE_ENV

    override = os.environ.get(LOG_FILE_ENV)
    return Path(override) if override else gaia_home() / "gaia.log"


def log_files(source: str) -> List[LogFile]:
    """The files of *source* that exist, the one to show first at the front.

    Raises:
        UnknownLogError: *source* is not one of :data:`SOURCES`.
    """
    from gaia.config import gaia_agents_dir
    from gaia.daemon import paths as daemon_paths
    from gaia.home import gaia_home

    if source not in SOURCES:
        raise UnknownLogError(
            f"No log source named {source!r}; GAIA has {', '.join(SOURCES)}."
        )
    files: List[LogFile] = []
    if source == "gaia":
        files.append(LogFile("gaia.log", gaia_log_path()))
    elif source == "service":
        files.append(LogFile("daemon.log", daemon_paths.log_path()))
    elif source == "agent":
        files.append(LogFile("gaia-agent.log", gaia_home() / "logs" / "gaia-agent.log"))
        for agent_dir in sorted(p for p in gaia_agents_dir().glob("*") if p.is_dir()):
            sidecars = _newest_first((agent_dir / "logs").glob("*.log"))
            files += [
                LogFile(f"{agent_dir.name}/{p.name}", p)
                for p in sidecars[:_SIDECAR_LOGS_PER_AGENT]
            ]
    elif source == "desktop":
        base = _user_gaia_dir()
        files += [
            LogFile(name, base / name)
            for name in (
                "electron-main.log",
                "electron-install.log",
                "electron-updater.log",
            )
        ]
    elif source == "other":
        files += [
            LogFile(p.name, p)
            for p in sorted((gaia_home() / "logs").glob("*.log"))
            if p.name != "gaia-agent.log"
        ]
    return [f for f in files if f.path.is_file()]


def find_file(source: str, name: Optional[str]) -> Optional[LogFile]:
    """*source*'s file called *name*, or its first when *name* is None.

    None when the source has no files yet.

    Raises:
        UnknownLogError: Unknown source, or a *name* that isn't one of its files
            (never a path: the viewer can only read what GAIA lists).
    """
    files = log_files(source)
    if name is None:
        return files[0] if files else None
    for f in files:
        if f.name == name:
            return f
    raise UnknownLogError(f"The {source} logs have no file named {name!r}.")


def describe_sources() -> List[Dict[str, object]]:
    """Each source with its files, for the viewer's tabs."""
    return [
        {"id": source, "files": [f.to_dict() for f in log_files(source)]}
        for source in SOURCES
    ]
