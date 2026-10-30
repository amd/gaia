# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Every flagship wheel ships the hub/skills starter pack (#6078).

A plain ``pip install`` built a wheel holding only the package's own three
skills, because the pack lives at hub/skills, outside the package root. The
build hooks in ``gaia_agent.build`` copy it into the wheel (``build_py``) and
into the sdist (``sdist``), so pip-from-git, ``pip wheel`` and ``python -m
build`` (wheel built from the sdist) all carry it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from gaia_agent.build import copy_starter_skills

#: tests/ -> python/ -> gaia/ -> agents/ -> hub/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[5]
PACKAGE_ROOT = REPO_ROOT / "hub" / "agents" / "gaia" / "python"
HUB_SKILLS = REPO_ROOT / "hub" / "skills"
OWNED = {
    p.name for p in (PACKAGE_ROOT / "gaia_agent" / "skills").iterdir() if p.is_dir()
}
STARTERS = {
    p.name for p in HUB_SKILLS.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
}


def _skill(root: Path, name: str, body: str = "body") -> Path:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(body, encoding="utf-8")
    return root / name


def _skills_in(names) -> set[str]:
    prefix = "gaia_agent/skills/"
    return {
        n.split(prefix, 1)[1].split("/")[0]
        for n in names
        if prefix in n and n.endswith("/SKILL.md")
    }


@pytest.fixture
def tree(tmp_path):
    starter, package, dest = (tmp_path / d for d in ("starter", "package", "build"))
    for d in (starter, package):
        d.mkdir()
    return starter, package, dest


def test_copies_only_skill_directories(tree):
    starter, package, dest = tree
    _skill(starter, "alpha")
    (starter / "not-a-skill").mkdir()
    (starter / "README.md").write_text("x", encoding="utf-8")

    copied = copy_starter_skills(starter, dest, package)

    assert [p.name for p in copied] == ["alpha"]
    assert sorted(p.name for p in dest.iterdir()) == ["alpha"]


def test_package_owned_skill_wins_over_starter_of_same_name(tree):
    starter, package, dest = tree
    _skill(starter, "shared", "starter copy")
    _skill(package, "shared", "package copy")
    _skill(dest, "shared", "package copy")

    assert copy_starter_skills(starter, dest, package) == []
    assert (dest / "shared" / "SKILL.md").read_text(encoding="utf-8") == (
        "package copy"
    )


def test_stale_copy_in_a_reused_build_dir_is_replaced(tree):
    starter, package, dest = tree
    _skill(starter, "alpha", "new")
    stale = _skill(dest, "alpha", "old")
    (stale / "removed.py").write_text("x", encoding="utf-8")

    copy_starter_skills(starter, dest, package)

    assert (dest / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "new"
    assert not (dest / "alpha" / "removed.py").exists()


def test_bytecode_caches_are_not_shipped(tree):
    starter, package, dest = tree
    skill = _skill(starter, "alpha")
    (skill / "__pycache__").mkdir()
    (skill / "__pycache__" / "tools.cpython-312.pyc").write_bytes(b"\0")
    (skill / "tools.pyc").write_bytes(b"\0")
    (skill / "tools.py").write_text("x = 1\n", encoding="utf-8")

    copy_starter_skills(starter, dest, package)

    assert sorted(p.name for p in (dest / "alpha").iterdir()) == [
        "SKILL.md",
        "tools.py",
    ]


def test_missing_starter_root_copies_nothing(tree):
    _, package, dest = tree
    assert copy_starter_skills(package / "absent", dest, package) == []
    assert not dest.exists()


@pytest.fixture(scope="module")
def checkout(tmp_path_factory):
    """A copy of the package beside hub/skills, so builds never touch the repo."""
    root = tmp_path_factory.mktemp("checkout")
    shutil.copytree(HUB_SKILLS, root / "hub" / "skills")
    shutil.copytree(
        PACKAGE_ROOT,
        root / "hub" / "agents" / "gaia" / "python",
        ignore=shutil.ignore_patterns(
            "build", "dist", "*.egg-info", "tests", "__pycache__", "eval_baselines"
        ),
    )
    return root / "hub" / "agents" / "gaia" / "python"


def _backend(package: Path, call: str, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    name = (
        subprocess.run(
            [
                sys.executable,
                "-c",
                f"from setuptools import build_meta as b; print(b.{call}({str(out)!r}))",
            ],
            cwd=package,
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .splitlines()[-1]
    )
    return out / name


def test_wheel_from_checkout_ships_every_starter_skill(checkout, tmp_path):
    wheel = _backend(checkout, "build_wheel", tmp_path / "dist")
    with zipfile.ZipFile(wheel) as archive:
        assert _skills_in(archive.namelist()) == STARTERS | OWNED


def test_wheel_built_from_the_sdist_ships_every_starter_skill(checkout, tmp_path):
    sdist = _backend(checkout, "build_sdist", tmp_path / "sdist")
    with tarfile.open(sdist) as archive:
        assert _skills_in(archive.getnames()) == STARTERS | OWNED
        safe = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        archive.extractall(tmp_path / "unpacked", **safe)
    (unpacked,) = (tmp_path / "unpacked").iterdir()

    wheel = _backend(unpacked, "build_wheel", tmp_path / "dist")
    with zipfile.ZipFile(wheel) as archive:
        assert _skills_in(archive.namelist()) == STARTERS | OWNED
