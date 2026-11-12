# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which eval scenarios exercise a hub skill.

A scenario covers skill ``X`` when its file is ``skill_<x>_*.yaml`` anywhere under
``eval/scenarios/``, or when it is a ``gaia_skills_tasks`` scenario whose first
turn is exactly "Load the X skill." and asserts ``load_skill``. The scenario
schema has no "exercises skill X" field, and adding one would change the judge
prompt, so these two conventions carry it.

``tests/unit/eval/test_skill_scenario_coverage.py`` uses this to require every
hub skill to have scenarios. ``skill_audit.yml`` uses it to tell a PR that
changed a skill which scenarios to dispatch on ``eval_flagship.yml``. It never
dispatches anything itself: the eval pool is too scarce for per-PR runs.

Usage::

    python util/skill_eval_scenarios.py hub/skills/price-watch hub/skills/coding
    python util/skill_eval_scenarios.py --from-file skill-dirs.txt --markdown
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = REPO_ROOT / "eval" / "scenarios"
TASKS_DIR = SCENARIOS_DIR / "gaia_skills_tasks"

_LOAD_ONE_SKILL = re.compile(r"Load the ([a-z0-9-]+) skill\.")


def filename_prefix(skill: str) -> str:
    return f"skill_{skill.replace('-', '_')}_"


def task_skill(path: Path) -> Optional[str]:
    """The one skill a ``gaia_skills_tasks`` scenario loads on turn 1, if any."""
    first = yaml.safe_load(path.read_text(encoding="utf-8"))["turns"][0]
    match = _LOAD_ONE_SKILL.fullmatch(first.get("user_message") or "")
    if match and "load_skill" in (first.get("success_criteria") or ""):
        return match.group(1)
    return None


def task_skills(tasks_dir: Path = TASKS_DIR) -> Dict[Path, Optional[str]]:
    return {p: task_skill(p) for p in sorted(tasks_dir.glob("*.yaml"))}


def scenarios_for(
    skill: str,
    scenarios_dir: Path = SCENARIOS_DIR,
    tasks: Optional[Dict[Path, Optional[str]]] = None,
) -> List[Path]:
    tasks = task_skills(scenarios_dir / "gaia_skills_tasks") if tasks is None else tasks
    named = scenarios_dir.rglob(f"{filename_prefix(skill)}*.yaml")
    loaded = (p for p, s in tasks.items() if s == skill)
    return sorted({*named, *loaded})


def scenario_id(path: Path) -> str:
    return str(yaml.safe_load(path.read_text(encoding="utf-8"))["id"])


def markdown(skills: Sequence[str]) -> str:
    """A job-summary block naming each skill's scenarios and the dispatch inputs."""
    tasks = task_skills()
    rows, ids = [], []
    for skill in skills:
        found = [scenario_id(p) for p in scenarios_for(skill, tasks=tasks)]
        ids += found
        rows.append(f"| `{skill}` | {', '.join(f'`{i}`' for i in found) or '**none**'} |")
    lines = [
        "### Eval scenarios for the changed skills",
        "",
        "| Skill | Scenarios |",
        "|---|---|",
        *rows,
        "",
    ]
    if ids:
        lines += [
            "To measure the change before the next RC, dispatch **Agent Eval — flagship** "
            "(`eval_flagship.yml`) on this branch with `scenarios` set to:",
            "",
            "```",
            ",".join(ids),
            "```",
        ]
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("skill_dirs", nargs="*", help="hub/skills/<name> paths")
    parser.add_argument("--from-file", type=Path, help="One skill directory per line")
    parser.add_argument("--markdown", action="store_true", help="Job-summary output")
    args = parser.parse_args(argv)

    dirs = list(args.skill_dirs)
    if args.from_file:
        dirs += [l.strip() for l in args.from_file.read_text("utf-8").splitlines()]
    # Only shipped hub skills have eval scenarios; fixtures and other roots don't.
    skills = sorted({Path(d).name for d in dirs if d and Path(d).parent.as_posix() == "hub/skills"})
    if not skills:
        return 0
    if args.markdown:
        print(markdown(skills), end="")
    else:
        tasks = task_skills()
        for skill in skills:
            ids = [scenario_id(p) for p in scenarios_for(skill, tasks=tasks)]
            print(f"{skill}: {','.join(ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
