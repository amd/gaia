# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stage what the gaia_* scenarios read, into the home the agent under eval uses.

Every workflow that runs a ``gaia_*`` category calls this, so they cannot drift
apart on what "staged" means:

    python tests/fixtures/gaia/stage_eval_env.py --home "$HOME"

It does three things:

1. Copies the agent-facing parts of ``tests/fixtures/gaia`` to
   ``<home>/gaia-eval`` (replacing any earlier copy), because scenario messages
   name files like ``~/gaia-eval/csv/sales.csv``. The eval backend runs from
   that folder, so it is the agent's file scope; nothing that holds an answer
   is copied there.
2. Copies the starter skills under ``hub/skills`` into ``<home>/.gaia/skills``,
   except those the corpus contract says must start uninstalled.
3. Builds and trusts the fixture hub with ``prepare_fixture_hub.py``.

``<home>/.gaia-eval-bin`` (the fake ``gh``) must go first on the backend's
``PATH``; the github-triage scenarios are written against it, never a real
``gh``.

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

#: What the scenarios name under ``~/gaia-eval`` (GAIA_FIXTURE_VALUES.md). Only
#: these are staged: the agent can be granted that folder, so it must hold the
#: files a user would have and nothing the scorer knows — no ground truth,
#: generators, canned gh data or harness scripts.
AGENT_FACING = ("csv", "mini_repo", "media", "capture")
_NOT_AGENT_FACING = shutil.ignore_patterns("_gen_*", "ground_truth.json", "__pycache__")

#: Loose files scenarios expect under ``~/gaia-eval``, staged from the corpus.
_CORPUS_DOCS = REPO_ROOT / "eval" / "corpus" / "documents"
LOOSE_FILES = {
    _CORPUS_DOCS / "meeting_notes_q3.txt": Path("documents", "meeting_notes_q3.txt"),
}

#: Install scenarios download these from the fixture hub, so they must start
#: uninstalled (GAIA_FIXTURE_VALUES.md, "Environment preconditions").
NOT_PRE_INSTALLED = frozenset({"rss-digest"})


def _clear_readonly(func, path, _exc):
    # Staged fixtures can hold read-only files, which rmtree cannot delete on
    # Windows.
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


def install_fake_gh(home: Path) -> Path:
    """Write ``gh`` launchers for the fake gh into ``<home>/.gaia-eval-bin``.

    They run this checkout's ``fake_gh/gh.py``, so its canned answers stay out
    of the staged ``gaia-eval`` folder the agent may read. The flagship runs a
    skill-granted CLI as argv, and on Windows CreateProcess then finds only
    ``gh.exe`` — a ``gh.cmd`` alone loses to the runner's real GitHub CLI — so
    Windows also gets the bench stand-in's ``.exe`` launcher.

    Returns:
        The directory to put first on the backend's ``PATH``.
    """
    script = HERE / "fake_gh" / "gh.py"
    bin_dir = home / ".gaia-eval-bin"
    bin_dir.mkdir(exist_ok=True)
    sh = bin_dir / "gh"
    sh.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
    )
    sh.chmod(0o755)
    (bin_dir / "gh.cmd").write_text(
        f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
    )
    if sys.platform == "win32":
        src = str(REPO_ROOT / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from gaia.eval.bench.ghstub import _exe_launcher

        (bin_dir / "gh.exe").write_bytes(_exe_launcher(sys.executable, script))
    return bin_dir


def stage(home: Path) -> Path:
    """Stage fixtures, starter skills and the fixture hub under ``home``.

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
    for name in AGENT_FACING:
        shutil.copytree(HERE / name, fixtures / name, ignore=_NOT_AGENT_FACING)
    for source, dest in LOOSE_FILES.items():
        (fixtures / dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, fixtures / dest)
    print(f"staged fixtures -> {fixtures}")
    print(f"fake gh -> {install_fake_gh(home)} (prepend it to PATH)")

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
