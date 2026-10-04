# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Turn the staged ``journal/`` into a git repo with no usable identity.

A nested ``.git`` cannot be committed, so this runs once per staging, on the
staged copy (never on the checkout):

    python tests/fixtures/gaia/tiers_resilience/build_fixtures.py --dest ~/gaia-eval/tiers_resilience

The repo's LOCAL config blanks ``user.name`` and ``user.email``. Local config
beats global, so ``git commit`` there fails with "Author identity unknown" on
every machine, including one with a global identity configured — which is the
state ``res_git_identity_unset`` needs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def _checked_git(repo: Path, *args: str) -> None:
    result = _git(repo, *args)
    if result.returncode != 0:
        raise SystemExit(
            f"git {' '.join(args)} failed in {repo} (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )


def build(dest: Path) -> Path:
    """Initialise ``dest/journal`` with a blank identity; return the repo path."""
    journal = dest / "journal"
    if not journal.is_dir():
        raise SystemExit(
            f"{journal} is missing. --dest must be the staged copy of "
            "tests/fixtures/gaia/tiers_resilience (run stage_eval_env.py first)."
        )
    # Nesting a repo inside any enclosing one (the checkout, or a dotfiles
    # repo at $HOME) would make it uncommittable.
    toplevel = _git(dest, "rev-parse", "--show-toplevel")
    if toplevel.returncode == 0:
        raise SystemExit(
            f"{dest} is inside the git repository at {toplevel.stdout.strip()} "
            "(the checkout, or an enclosing one such as a dotfiles repo at "
            "$HOME). --dest must not be inside any git repository."
        )

    _checked_git(journal, "init", "--quiet")
    _checked_git(journal, "config", "user.name", "")
    _checked_git(journal, "config", "user.email", "")
    print(f"journal repo with blank git identity -> {journal}")
    return journal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dest",
        required=True,
        type=Path,
        help="The staged tiers_resilience directory (~/gaia-eval/tiers_resilience).",
    )
    args = parser.parse_args(argv)
    build(args.dest.expanduser().resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
