# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Give an eval category a fresh, empty GAIA home, and refuse to run if it is not.

    python util/eval_gaia_home.py env   --home <dir>   # KEY=VALUE lines to export
    python util/eval_gaia_home.py check --home <dir>   # exit 1 unless isolated + empty

Agent state under the runner's real ``~/.gaia`` outlives the job on the
self-hosted pool, so memory one run wrote was recalled as fact by a later run
(v0.25.0-rc16). Every category therefore gets its own home under the job's temp
dir, with the variables below pointing every state root GAIA resolves from the
environment into it. Lemonade and its models are not GAIA-home state: the eval
reaches the shared server through ``LEMONADE_BASE_URL``.

``check`` runs in the environment the backend is about to start with. It fails
when any state root resolves outside the home, or when the memory database
already holds a row.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Memory tables whose rows are what one run could hand the next.
MEMORY_TABLES = ("knowledge", "conversations", "procedures", "tool_history")


def env_for(home: Path) -> dict[str, str]:
    """The variables that move GAIA's on-disk state into *home*.

    ``GAIA_CONFIG_DIR`` is needed beside ``GAIA_HOME``: the skills root, the RAG
    cache and ChatAgent sessions follow it and ignore ``GAIA_HOME``.
    """
    home = Path(home)
    return {
        "GAIA_HOME": str(home),
        "GAIA_CONFIG_DIR": str(home),
        "GAIA_MEMORY_DB": str(home / "memory.db"),
        "GAIA_DAEMON_HOME": str(home / "host"),
    }


def _within(path: Path, home: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(os.path.abspath(home))
        return True
    except ValueError:
        return False


def _memory_rows(db: Path) -> dict[str, int]:
    """Row counts of the memory tables that exist in *db* (read-only)."""
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        present = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in MEMORY_TABLES
            if table in present
        }
    finally:
        conn.close()


def check(home: Path) -> list[str]:
    """Problems that make *home* unfit to start a category in; empty when fit."""
    src = str(REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from gaia import config
    from gaia.agents.base.memory_store import resolve_memory_db_path
    from gaia.daemon import paths
    from gaia.skills.manager import user_skills_dir

    problems = []
    for name, want in env_for(home).items():
        have = os.environ.get(name)
        if have != want:
            problems.append(f"{name} is {have!r}, expected {want!r}")

    roots = {
        "GAIA home": config.gaia_home(),
        "config file": config.GAIA_CONFIG_FILE,
        "memory database": resolve_memory_db_path(),
        "skills root": user_skills_dir(),
        "MCP server list": config.mcp_servers_path(),
        "agents folder": config.gaia_agents_dir(),
        "daemon host dir": paths.host_dir(),
    }
    for what, path in roots.items():
        if not _within(path, home):
            problems.append(f"the {what} resolves to {path}, outside {home}")

    db = Path(os.environ.get("GAIA_MEMORY_DB") or home / "memory.db")
    if db.exists():
        rows = {t: n for t, n in _memory_rows(db).items() if n}
        if rows:
            problems.append(
                f"the memory database {db} is not empty ({rows}); "
                "a category must start with no memory"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["env", "check"])
    parser.add_argument("--home", required=True, type=Path)
    args = parser.parse_args(argv)
    home = Path(os.path.abspath(args.home))
    if args.action == "env":
        for name, value in env_for(home).items():
            print(f"{name}={value}")
        return 0
    problems = check(home)
    if problems:
        for problem in problems:
            print(f"NOT ISOLATED: {problem}", file=sys.stderr)
        print(
            "Refusing to run: state from an earlier run would change this "
            "category's score. Set the variables `util/eval_gaia_home.py env` "
            "prints for this home before starting the backend.",
            file=sys.stderr,
        )
        return 1
    print(f"GAIA home {home} is isolated and its memory is empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
