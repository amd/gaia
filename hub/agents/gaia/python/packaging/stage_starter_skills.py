"""Stage the repository starter skills into the flagship package for a build."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[5]
STARTER_SKILLS = REPO_ROOT / "hub" / "skills"
PACKAGE_SKILLS = PACKAGE_ROOT / "gaia_agent" / "skills"


def starter_skill_names() -> set[str]:
    """Return starter skills that contain a skill body."""
    if not STARTER_SKILLS.is_dir():
        raise FileNotFoundError(f"starter skill directory is missing: {STARTER_SKILLS}")
    return {
        path.name
        for path in STARTER_SKILLS.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }


@contextmanager
def staged_starter_skills() -> Iterator[tuple[Path, ...]]:
    """Temporarily copy starter skills into the package and always clean up.

    The package already owns a few skills of its own. Those directories win if
    a future starter skill uses the same name; only newly staged directories are
    removed on exit, so a local build cannot overwrite tracked package files.
    """
    PACKAGE_SKILLS.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    try:
        for name in sorted(starter_skill_names()):
            destination = PACKAGE_SKILLS / name
            if destination.exists():
                continue
            shutil.copytree(STARTER_SKILLS / name, destination)
            copied.append(destination)
        yield tuple(copied)
    finally:
        for destination in reversed(copied):
            shutil.rmtree(destination, ignore_errors=True)


def build_with_starter_skills(outdir: Path) -> None:
    """Build the wheel and sdist while the starter pack is staged."""
    outdir.mkdir(parents=True, exist_ok=True)
    with staged_starter_skills():
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--outdir",
                str(outdir),
                str(PACKAGE_ROOT),
            ],
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, metavar="OUTDIR")
    args = parser.parse_args()
    if args.build is None:
        parser.error("--build OUTDIR is required")
    build_with_starter_skills(args.build)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
