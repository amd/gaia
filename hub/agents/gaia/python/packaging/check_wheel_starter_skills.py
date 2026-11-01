# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check that a built flagship wheel contains the complete starter pack."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

STARTER_SKILLS = Path(__file__).resolve().parents[4] / "skills"


def starter_skill_names() -> set[str]:
    """Starter skills in the checkout: every hub/skills/<name>/SKILL.md."""
    if not STARTER_SKILLS.is_dir():
        raise FileNotFoundError(f"starter skill directory is missing: {STARTER_SKILLS}")
    return {
        path.name
        for path in STARTER_SKILLS.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    }


def wheel_skill_names(wheel: Path) -> set[str]:
    prefix = "gaia_agent/skills/"
    with zipfile.ZipFile(wheel) as archive:
        return {
            name[len(prefix) : -len("/SKILL.md")]
            for name in archive.namelist()
            if name.startswith(prefix) and name.endswith("/SKILL.md")
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    wheel = parser.parse_args().wheel
    expected = starter_skill_names()
    actual = wheel_skill_names(wheel)
    missing = sorted(expected - actual)
    if missing:
        raise SystemExit(
            f"{wheel.name} is missing starter skills: {', '.join(missing)}"
        )
    print(
        f"{wheel.name}: verified {len(expected)} starter skills in "
        "gaia_agent/skills/"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
