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

import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from gaia_agent.build import checkout_starter_skills, copy_starter_skills

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


def _repo_markers(root: Path) -> None:
    (root / "setup.py").write_text("", encoding="utf-8")
    (root / ".git").mkdir()


def _build_py_at(root: Path) -> Path:
    path = root / "hub" / "agents" / "gaia" / "python" / "gaia_agent" / "build.py"
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    (root / "hub" / "skills").mkdir()
    return path


def test_checkout_layout_with_repo_markers_finds_hub_skills(tmp_path):
    _repo_markers(tmp_path)
    here = _build_py_at(tmp_path)
    assert checkout_starter_skills(here) == (tmp_path / "hub" / "skills").resolve()


def test_matching_layout_without_repo_markers_is_not_a_checkout(tmp_path):
    assert checkout_starter_skills(_build_py_at(tmp_path)) is None


def test_other_layout_is_not_a_checkout(tmp_path):
    _repo_markers(tmp_path)
    (tmp_path / "skills").mkdir()
    here = tmp_path / "a" / "b" / "c" / "pkg" / "gaia_agent" / "build.py"
    here.parent.mkdir(parents=True)
    here.write_text("", encoding="utf-8")
    assert checkout_starter_skills(here) is None


def _symlink(link: Path, target: Path, is_dir: bool) -> None:
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except OSError as e:
        pytest.skip(f"cannot create symlinks here: {e}")


def test_symlinked_file_in_a_skill_is_refused(tree, tmp_path):
    starter, package, dest = tree
    skill = _skill(starter, "alpha")
    outside = tmp_path / "outside.py"
    outside.write_text("x", encoding="utf-8")
    _symlink(skill / "tools.py", outside, is_dir=False)

    with pytest.raises(RuntimeError, match="symlink"):
        copy_starter_skills(starter, dest, package)


def test_symlinked_skill_directory_is_refused(tree, tmp_path):
    starter, package, dest = tree
    elsewhere = _skill(tmp_path / "elsewhere", "beta")
    _symlink(starter / "beta", elsewhere, is_dir=True)

    with pytest.raises(RuntimeError, match="symlink"):
        copy_starter_skills(starter, dest, package)


@pytest.fixture(scope="module")
def checkout(tmp_path_factory):
    """A copy of the package beside hub/skills, so builds never touch the repo."""
    root = tmp_path_factory.mktemp("checkout")
    _repo_markers(root)
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


def _plant_skill(skills: Path) -> None:
    skill = skills / "planted"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("planted", encoding="utf-8")
    (skill / "tools.py").write_text("x = 1\n", encoding="utf-8")


@pytest.mark.parametrize("layout", ["generic", "checkout-shaped"])
def test_wheel_from_an_unpacked_sdist_never_reads_skills_above_it(
    checkout, tmp_path, layout
):
    sdist = _backend(checkout, "build_sdist", tmp_path / "sdist")
    if layout == "generic":
        top = tmp_path / "g"
        parent = top / "h" / "i"
    else:
        top = tmp_path / "r" / "hub"
        parent = top / "agents" / "gaia"
    parent.mkdir(parents=True)
    if layout != "generic":
        _repo_markers(top.parent)
    with tarfile.open(sdist) as archive:
        safe = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        archive.extractall(parent, **safe)
    (unpacked,) = parent.iterdir()
    if layout != "generic":
        unpacked = unpacked.rename(parent / "python")
    # build.py's parents[4] is ``top``: the folder a naive lookup would read.
    assert (unpacked / "gaia_agent" / "build.py").resolve().parents[4] == top.resolve()
    _plant_skill(top / "skills")

    wheel = _backend(unpacked, "build_wheel", tmp_path / "dist")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    assert "planted" not in _skills_in(names)
    assert not any("/planted/" in n for n in names)
    assert _skills_in(names) == STARTERS | OWNED
