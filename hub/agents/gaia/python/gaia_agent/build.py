# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Setuptools build hooks for the flagship agent package."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist

PACKAGE_SKILLS = Path(__file__).resolve().parent / "skills"

#: build.py's parents[1..4] in a checkout: hub/agents/gaia/python/gaia_agent/.
_CHECKOUT_LAYOUT = ("python", "gaia", "agents", "hub")

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def checkout_starter_skills(here: Optional[Path] = None) -> Optional[Path]:
    """The repository's hub/skills, or None when not building from a checkout.

    Only the repo's own skills folder is ever read: the layout must match and
    the repo root must carry its markers, so an unpacked sdist never reaches
    whatever directory happens to sit a few levels above it.
    """
    here = (here or Path(__file__)).resolve()
    parents = here.parents
    if len(parents) < 6:
        return None
    if tuple(parents[i].name for i in range(1, 5)) != _CHECKOUT_LAYOUT:
        return None
    root = parents[5]
    if not (root / "setup.py").is_file() or not (root / ".git").exists():
        return None
    skills = parents[4] / "skills"
    return skills if skills.is_dir() and not skills.is_symlink() else None


def _refuse_symlinks(source: Path) -> None:
    for path in [source, *source.rglob("*")]:
        if path.is_symlink():
            raise RuntimeError(
                f"starter skill contains a symlink, refusing to package it: {path}. "
                "Replace it with a regular file in hub/skills and rebuild."
            )


def copy_starter_skills(
    starter_root: Optional[Path],
    destination_root: Path,
    package_root: Path = PACKAGE_SKILLS,
) -> list[Path]:
    """Copy every starter skill into ``destination_root``.

    A name the package already owns wins. Any other existing copy is replaced,
    so a reused ``build/`` directory cannot ship a stale skill.
    """
    if starter_root is None or not starter_root.is_dir():
        return []
    destination_root.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in sorted(starter_root.iterdir()):
        if not source.is_dir():
            continue
        if not (source / "SKILL.md").is_file():
            continue
        if (package_root / source.name).exists():
            continue
        _refuse_symlinks(source)
        destination = destination_root / source.name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, symlinks=True, ignore=_IGNORE)
        copied.append(destination)
    return copied


class build_py(_build_py):
    """Include repository starter skills in wheels built from a checkout."""

    def run(self) -> None:
        super().run()
        project = Path(self.distribution.src_root or os.getcwd())
        # An unpacked sdist (it has PKG-INFO) already carries the pack.
        if (project / "PKG-INFO").is_file():
            return
        copy_starter_skills(
            checkout_starter_skills(), Path(self.build_lib) / "gaia_agent" / "skills"
        )


class sdist(_sdist):
    """Carry the starter skills in the sdist so a wheel built from it has them."""

    def make_release_tree(self, base_dir, files) -> None:
        super().make_release_tree(base_dir, files)
        copy_starter_skills(
            checkout_starter_skills(), Path(base_dir) / "gaia_agent" / "skills"
        )


__all__ = [
    "PACKAGE_SKILLS",
    "build_py",
    "checkout_starter_skills",
    "copy_starter_skills",
    "sdist",
]
