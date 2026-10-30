"""Setuptools build hooks for the flagship agent package."""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist

#: hub/agents/gaia/python/gaia_agent/build.py -> parents[4] is hub/.
STARTER_SKILLS = Path(__file__).resolve().parents[4] / "skills"
PACKAGE_SKILLS = Path(__file__).resolve().parent / "skills"

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def copy_starter_skills(
    starter_root: Path, destination_root: Path, package_root: Path = PACKAGE_SKILLS
) -> list[Path]:
    """Copy every starter skill into ``destination_root``.

    A name the package already owns wins. Any other existing copy is replaced,
    so a reused ``build/`` directory cannot ship a stale skill.
    """
    if not starter_root.is_dir():
        return []
    destination_root.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in sorted(starter_root.iterdir()):
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            continue
        if (package_root / source.name).exists():
            continue
        destination = destination_root / source.name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, ignore=_IGNORE)
        copied.append(destination)
    return copied


class build_py(_build_py):
    """Include repository starter skills in wheels built from a checkout."""

    def run(self) -> None:
        super().run()
        # An sdist has no hub/skills beside it; its tree already carries the pack.
        copy_starter_skills(
            STARTER_SKILLS, Path(self.build_lib) / "gaia_agent" / "skills"
        )


class sdist(_sdist):
    """Carry the starter skills in the sdist so a wheel built from it has them."""

    def make_release_tree(self, base_dir, files) -> None:
        super().make_release_tree(base_dir, files)
        copy_starter_skills(STARTER_SKILLS, Path(base_dir) / "gaia_agent" / "skills")


__all__ = [
    "PACKAGE_SKILLS",
    "STARTER_SKILLS",
    "build_py",
    "copy_starter_skills",
    "sdist",
]
