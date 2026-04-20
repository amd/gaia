# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GAIA-Internal-20 suite loader (§10.2).

Discovers ``T*.md`` task files on disk, parses their YAML-ish
front-matter, and returns structured :class:`TaskMeta` objects the
runner can feed into ``gaia-coder ask``. Also exposes the fixtures
directory so seeded-bug tasks (T09, T14, T17, T18) can apply their
patches before the agent starts.

Task-file format (from §10.2):

.. code-block:: markdown

    ---
    id: T04
    title: Migrate ChatAgent references to GaiaAgent under src/gaia/apps/webui/
    expected_fix_class: architectural
    max_diff_loc: 400
    max_wall_clock_min: 20
    setup:
      git_reset: "a1b2c3d"
      required_checks: ["pytest tests/test_webui.py", "python util/lint.py --all"]
      fixture: "fixtures/T04.patch"     # optional
    scoring:
      - name: compiles
        check: "python -c 'from gaia.apps.webui import *'"
        weight: 0.2
      - name: tests_pass
        check: "pytest tests/test_webui.py -x"
        weight: 0.4
      - name: no_lint_regression
        check: "python util/lint.py --all"
        weight: 0.2
      - name: pr_mergeable
        check: "diff_applies_to_coder_cleanly"
        weight: 0.2
    ---

    # Task body...

The front-matter parser uses :mod:`yaml` (already a GAIA dependency),
so the format is portable and diffable. We deliberately do **not**
eagerly run the check commands at load time — the scorer is the only
component that runs them, and only against the post-task sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import yaml

# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------

SUITE_ID = "gaia-internal-20"
SUITE_DIR = Path(__file__).resolve().parent
TASKS_DIR = SUITE_DIR / "tasks"
FIXTURES_DIR = SUITE_DIR / "fixtures"

# Default weights per §10.2 rubric. A task may override individual
# weights via its ``scoring`` block, but the names must match.
DEFAULT_CHECK_WEIGHTS: dict[str, float] = {
    "compiles": 0.2,
    "tests_pass": 0.4,
    "no_lint_regression": 0.2,
    "pr_mergeable": 0.2,
}

# Task is considered passing when weighted score >= this threshold
# (§10.2: "Task passes if score >= 0.8").
PASS_THRESHOLD = 0.8


# ---------------------------------------------------------------------------
# Data classes.
# ---------------------------------------------------------------------------


@dataclass
class CheckSpec:
    """One row from the task's ``scoring`` front-matter block."""

    name: str
    check: str
    weight: float
    baseline: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "CheckSpec":
        missing = [k for k in ("name", "check") if k not in data]
        if missing:
            raise ValueError(
                f"CheckSpec missing required field(s): {missing}; got {data!r}"
            )
        weight = float(data.get("weight", DEFAULT_CHECK_WEIGHTS.get(data["name"], 0.0)))
        return cls(
            name=data["name"],
            check=data["check"],
            weight=weight,
            baseline=data.get("baseline"),
        )


@dataclass
class TaskMeta:
    """Parsed front-matter + raw body for one task file."""

    id: str
    title: str
    expected_fix_class: str
    max_diff_loc: int
    max_wall_clock_min: int
    body: str
    path: Path
    git_reset: Optional[str] = None
    required_checks: list[str] = field(default_factory=list)
    fixture: Optional[str] = None
    checks: list[CheckSpec] = field(default_factory=list)

    @property
    def has_fixture(self) -> bool:
        return bool(self.fixture)

    def fixture_path(self) -> Optional[Path]:
        """Resolve the fixture path relative to the suite dir."""
        if not self.fixture:
            return None
        return (SUITE_DIR / self.fixture).resolve()

    def checks_by_name(self) -> dict[str, CheckSpec]:
        return {c.name: c for c in self.checks}


# ---------------------------------------------------------------------------
# Loader.
# ---------------------------------------------------------------------------


def _split_front_matter(text: str) -> tuple[dict, str]:
    """Return ``(front_matter_dict, body_text)``.

    Raises :class:`ValueError` if the file does not start with a
    ``---`` delimiter.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("task file must start with '---' front-matter delimiter")
    # Find the closing ``---`` delimiter.
    end_idx: Optional[int] = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("unterminated front-matter block (missing closing '---')")
    fm_block = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :])
    parsed = yaml.safe_load(fm_block) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"front-matter must be a mapping, got {type(parsed).__name__}")
    return parsed, body


def parse_task_file(path: Path) -> TaskMeta:
    """Parse a single ``T##.md`` task file into a :class:`TaskMeta`."""
    text = Path(path).read_text(encoding="utf-8")
    fm, body = _split_front_matter(text)

    required = ("id", "title", "expected_fix_class")
    missing = [k for k in required if k not in fm]
    if missing:
        raise ValueError(
            f"{path.name}: front-matter missing required fields: {missing}"
        )

    setup = fm.get("setup") or {}
    if not isinstance(setup, dict):
        raise ValueError(f"{path.name}: 'setup' must be a mapping")

    scoring_raw = fm.get("scoring") or []
    if not isinstance(scoring_raw, list):
        raise ValueError(f"{path.name}: 'scoring' must be a list")
    checks = [CheckSpec.from_dict(row) for row in scoring_raw if isinstance(row, dict)]

    required_checks = setup.get("required_checks") or []
    if not isinstance(required_checks, list):
        raise ValueError(f"{path.name}: 'setup.required_checks' must be a list")

    return TaskMeta(
        id=str(fm["id"]),
        title=str(fm["title"]),
        expected_fix_class=str(fm["expected_fix_class"]),
        max_diff_loc=int(fm.get("max_diff_loc", 0)),
        max_wall_clock_min=int(fm.get("max_wall_clock_min", 20)),
        body=body,
        path=path,
        git_reset=setup.get("git_reset"),
        required_checks=[str(x) for x in required_checks],
        fixture=setup.get("fixture"),
        checks=checks,
    )


def iter_task_files(tasks_dir: Path = TASKS_DIR) -> Iterator[Path]:
    """Yield task files in sorted order (``T01.md`` before ``T02.md``…)."""
    if not tasks_dir.is_dir():
        return iter(())
    return iter(sorted(tasks_dir.glob("T*.md")))


def load_all_tasks(tasks_dir: Path = TASKS_DIR) -> list[TaskMeta]:
    """Load and parse every task file under ``tasks_dir``."""
    return [parse_task_file(p) for p in iter_task_files(tasks_dir)]


def load_task(task_id: str, tasks_dir: Path = TASKS_DIR) -> TaskMeta:
    """Load a single task by id (``T01``, ``T02``, …)."""
    candidate = tasks_dir / f"{task_id}.md"
    if not candidate.exists():
        raise FileNotFoundError(
            f"{SUITE_ID}: no task file for id {task_id!r} " f"(expected {candidate})"
        )
    return parse_task_file(candidate)


__all__ = [
    "SUITE_ID",
    "SUITE_DIR",
    "TASKS_DIR",
    "FIXTURES_DIR",
    "DEFAULT_CHECK_WEIGHTS",
    "PASS_THRESHOLD",
    "CheckSpec",
    "TaskMeta",
    "parse_task_file",
    "iter_task_files",
    "load_all_tasks",
    "load_task",
]
