"""Setuptools build hooks for the flagship agent package."""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools.command.build_py import build_py as _build_py


class build_py(_build_py):
    """Include repository starter skills in wheels built from a checkout."""

    def run(self) -> None:
        super().run()
        self._copy_starter_skills()

    def _copy_starter_skills(self) -> None:
        # Source distributions do not have the repository skills beside them.
        starter_root = Path(__file__).resolve().parents[5] / "hub" / "skills"
        if not starter_root.is_dir():
            return

        destination_root = Path(self.build_lib) / "gaia_agent" / "skills"
        destination_root.mkdir(parents=True, exist_ok=True)
        for source in sorted(starter_root.iterdir()):
            if not source.is_dir() or not (source / "SKILL.md").is_file():
                continue
            destination = destination_root / source.name
            if destination.exists():
                continue
            shutil.copytree(source, destination)


__all__ = ["build_py"]
