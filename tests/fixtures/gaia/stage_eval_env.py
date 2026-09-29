# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stage what the gaia_* scenarios read, into the home the agent under eval uses.

Every workflow that runs a ``gaia_*`` category calls this, so they cannot drift
apart on what "staged" means:

    python tests/fixtures/gaia/stage_eval_env.py --home "$HOME"

It does four things:

1. Copies ``tests/fixtures/gaia`` to ``<home>/gaia-eval`` (replacing any earlier
   copy), because scenario messages name files like ``~/gaia-eval/csv/sales.csv``
   and the agent's path sandbox refuses repo paths.
2. Builds the git workspaces a commit cannot carry (a nested ``.git``) into that
   staged copy.
3. Copies the starter skills under ``hub/skills`` into ``<home>/.gaia/skills``,
   except those the corpus contract says must start uninstalled.
4. Builds and trusts the fixture hub with ``prepare_fixture_hub.py``.

``--home`` is required: defaulting to the developer's real home would overwrite
their installed skills and add a throwaway key to their trust store.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
HUB_SKILLS = REPO_ROOT / "hub" / "skills"

#: Install scenarios download these from the fixture hub, so they must start
#: uninstalled (GAIA_FIXTURE_VALUES.md, "Environment preconditions").
NOT_PRE_INSTALLED = frozenset({"rss-digest"})

#: Staged directories whose repositories are built after the copy.
GIT_BUILDERS = ("tiers_git_code", "tiers_resilience")


def _clear_readonly(func, path, _exc):
    # git writes its objects read-only, which rmtree cannot delete on Windows.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _run(argv: list[str], what: str) -> None:
    # Import this checkout's gaia, not whichever one is editable-installed.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(REPO_ROOT / "src"), env.get("PYTHONPATH", "")) if p
    )
    result = subprocess.run(argv, check=False, env=env)
    if result.returncode != 0:
        raise SystemExit(
            f"{what} failed (exit {result.returncode}); its output is above. "
            "The scenarios that read it cannot run without it."
        )


def stage(home: Path) -> Path:
    """Stage fixtures, workspaces, starter skills and the fixture hub under ``home``.

    Returns:
        The skills root the fixture hub was trusted into.
    """
    if not home.is_dir():
        raise SystemExit(f"--home {home} is not a directory.")

    fixtures = home / "gaia-eval"
    if fixtures.exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(fixtures, onexc=_clear_readonly)
        else:
            shutil.rmtree(fixtures, onerror=_clear_readonly)
    shutil.copytree(HERE, fixtures)
    print(f"staged fixtures -> {fixtures}")

    for name in GIT_BUILDERS:
        staged = fixtures / name
        _run(
            [
                sys.executable,
                str(staged / "build_fixtures.py"),
                "--dest",
                str(staged),
            ],
            f"Building the {name} workspaces",
        )

    skills_root = home / ".gaia" / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    for skill in sorted(p for p in HUB_SKILLS.iterdir() if p.is_dir()):
        if skill.name in NOT_PRE_INSTALLED:
            continue
        shutil.copytree(skill, skills_root / skill.name, dirs_exist_ok=True)
    print(f"pre-seeded starter skills -> {skills_root}")

    _run(
        [
            sys.executable,
            str(HERE / "prepare_fixture_hub.py"),
            "--skills-root",
            str(skills_root),
        ],
        "prepare_fixture_hub.py",
    )
    return skills_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--home",
        required=True,
        type=Path,
        help="The home directory of the agent under eval (the runner's profile in CI).",
    )
    args = parser.parse_args(argv)
    stage(args.home.expanduser().resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
