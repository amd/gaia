"""Check that a built flagship wheel contains the complete starter pack."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

from stage_starter_skills import starter_skill_names


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
