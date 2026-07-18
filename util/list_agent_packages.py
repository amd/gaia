# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""List the AMD production agent packages that ship as ``gaia-agent-<id>`` wheels.

Single source of truth = the module-level ``AGENT_PACKAGES`` constant in
``setup.py``. This script reads that list *statically* (``ast``, never
executing ``setup.py``), maps each ``gaia-agent-<id>`` distribution name to
its package directory under ``hub/agents/python/<id>/``, and verifies the
directory exists.

Issue #2240: this used to read ``extras_require["agents"]`` — the list
backing a real ``pip install "amd-gaia[agents]"`` extra. That extra named
packages that were never published, which made it unsatisfiable; pip/uv
handled that by silently backtracking to an old version that lacked the
extra, *downgrading* a working install. The inventory moved to a plain
constant so it stops being installable syntax while remaining the single
source of truth for CI (see setup.py's removal comment for the full story;
re-adding an extra is tracked by #1179 / #1513).

Consumers:

* ``.github/workflows/publish_agents.yml`` calls ``--format matrix`` to generate
  the build/publish matrix, so adding an agent to ``setup.py``'s ``AGENT_PACKAGES`` is all it
  takes to start publishing its wheel — no second list to keep in sync.
* ``tests/unit/test_agent_pypi_publish.py`` calls :func:`list_agent_packages`
  to assert the published set, the on-disk packages, and their ``amd-gaia``
  dependency all agree.

Per ``CLAUDE.md`` (No Silent Fallbacks): a distribution listed in the constant with
no matching ``hub/agents/python/<id>/`` directory, or a malformed ``setup.py``,
raises rather than silently dropping the agent from the publish set.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

# ``gaia-agent-chat`` -> id ``chat`` -> dir ``hub/agents/python/chat``.
DIST_PREFIX = "gaia-agent-"
REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_PY = REPO_ROOT / "setup.py"
PYTHON_AGENTS_DIR = REPO_ROOT / "hub" / "agents" / "python"


class AgentListError(Exception):
    """Raised when the production-agent list cannot be derived or is invalid."""


@dataclass(frozen=True)
class AgentPackage:
    """A production agent wheel and where its source package lives."""

    dist_name: str  # e.g. "gaia-agent-summarize"
    agent_id: str  # e.g. "summarize"
    path: Path  # e.g. <repo>/hub/agents/python/summarize

    @property
    def rel_path(self) -> str:
        """Repo-relative POSIX path (stable across OSes, for CI matrices)."""
        return self.path.relative_to(REPO_ROOT).as_posix()


def _read_agent_packages_constant(setup_py: Path) -> List[str]:
    """Return the string list assigned to ``AGENT_PACKAGES`` in *setup_py*.

    Parses statically with :mod:`ast` — ``setup.py`` is never imported or run.
    """
    if not setup_py.exists():
        raise AgentListError(
            f"setup.py not found at {setup_py}. Run this from the gaia repo, or "
            f"check the checkout."
        )
    tree = ast.parse(setup_py.read_text(encoding="utf-8"), filename=str(setup_py))

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "AGENT_PACKAGES"
        ):
            continue
        try:
            dists = ast.literal_eval(node.value)
        except ValueError as exc:
            raise AgentListError(
                f"setup.py: AGENT_PACKAGES is not a literal list of strings: "
                f"{exc}."
            ) from exc
        if not isinstance(dists, list) or not all(isinstance(d, str) for d in dists):
            raise AgentListError(
                "setup.py: AGENT_PACKAGES must be a list of distribution-name "
                "strings."
            )
        return dists
    raise AgentListError(
        "setup.py: no module-level 'AGENT_PACKAGES' assignment found. Add it "
        "(the static list of production agent wheels; see issue #2240 — it "
        "is deliberately not a pip extra)."
    )


def list_agent_packages(setup_py: Path = SETUP_PY) -> List[AgentPackage]:
    """Return the production agent packages declared by ``setup.py``'s ``AGENT_PACKAGES``.

    Raises:
        AgentListError: For a malformed list, a distribution name that does not
            start with ``gaia-agent-``, or a missing package directory.
    """
    packages: List[AgentPackage] = []
    for dist in _read_agent_packages_constant(setup_py):
        if not dist.startswith(DIST_PREFIX):
            raise AgentListError(
                f"distribution {dist!r} in setup.py's AGENT_PACKAGES does not follow the "
                f"'{DIST_PREFIX}<id>' naming convention required by issue #1179."
            )
        agent_id = dist[len(DIST_PREFIX) :]
        path = PYTHON_AGENTS_DIR / agent_id
        if not (path / "pyproject.toml").exists():
            raise AgentListError(
                f"{dist}: no package at {path}/pyproject.toml. Every entry in "
                f"setup.py's AGENT_PACKAGES must have a wheel source under "
                f"hub/agents/python/<id>/ (or remove it from the list)."
            )
        packages.append(AgentPackage(dist_name=dist, agent_id=agent_id, path=path))
    return packages


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=["ids", "paths", "matrix"],
        default="ids",
        help="ids: one agent id per line; paths: one repo-relative path per "
        "line; matrix: a GitHub Actions matrix JSON object (default: ids)",
    )
    parser.add_argument(
        "--only",
        metavar="AGENT_ID",
        help="filter output to a single agent by id (e.g. email); fails loudly "
        "if the id is not found in setup.py's AGENT_PACKAGES",
    )
    args = parser.parse_args(argv)

    try:
        packages = list_agent_packages()
    except AgentListError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.only is not None:
        filtered = [p for p in packages if p.agent_id == args.only]
        if not filtered:
            valid = ", ".join(p.agent_id for p in packages)
            print(
                f"error: agent id {args.only!r} not found in setup.py's AGENT_PACKAGES. "
                f"Valid ids: {valid}",
                file=sys.stderr,
            )
            return 1
        packages = filtered

    if args.format == "ids":
        print("\n".join(p.agent_id for p in packages))
    elif args.format == "paths":
        print("\n".join(p.rel_path for p in packages))
    else:  # matrix
        include = [
            {"id": p.agent_id, "dist": p.dist_name, "path": p.rel_path}
            for p in packages
        ]
        print(json.dumps({"include": include}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
