# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``learnings.log`` — append-only plain-text log of candidate ``GAIA.md`` edits.

Backs §4.6 and §15.1 of ``docs/plans/coder-agent.mdx``. Each line is a single
human-readable observation that the agent flagged as *possibly* worth
promoting to her always-present identity document. The weekly summary (§4.4)
walks this log and proposes the top-3 candidates to the EM.

Canonical line format (§15.1):

    <ISO-8601 UTC> [task=<task_id>] Observed: "<one-line observation>" → <promotion_candidate: GAIA.md | skill:<name> | dismissed>

Newlines in the observation are rejected — each observation must fit on a
single line so grep/awk stay useful.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Optional

from pydantic import BaseModel, field_validator

# Promotion candidate vocabulary per §4.6:
# - ``GAIA.md`` — propose promoting into the always-present identity doc.
# - ``skill:<name>`` — demote into a context-loaded skill file.
# - ``dismissed`` — recorded but no promotion proposed.
_SKILL_RE = re.compile(r"^skill:[A-Za-z0-9._\-]+$")


class LearningEntry(BaseModel):
    """One line of ``learnings.log``."""

    ts: str  # ISO-8601 UTC
    task_id: str  # task identifier (``t_01H...``)
    observation: str  # one-line observation
    promotion_candidate: str  # "GAIA.md" | "skill:<name>" | "dismissed"

    @field_validator("observation")
    @classmethod
    def _no_newlines(cls, v: str) -> str:
        if "\n" in v or "\r" in v:
            raise ValueError("observation must be a single line")
        return v

    @field_validator("promotion_candidate")
    @classmethod
    def _known_candidate(cls, v: str) -> str:
        if v in {"GAIA.md", "dismissed"}:
            return v
        if _SKILL_RE.match(v):
            return v
        raise ValueError(
            "promotion_candidate must be 'GAIA.md', 'dismissed', or 'skill:<name>'"
        )


# Pattern for parsing:
#   <ts> [task=<task_id>] Observed: "<obs>" → <candidate>
_LINE_RE = re.compile(
    r"^(?P<ts>\S+)\s+\[task=(?P<task_id>[^\]]+)\]\s+Observed:\s+"
    r"\"(?P<observation>.*)\"\s+→\s+(?P<candidate>\S.*)$"
)


def format_entry(entry: LearningEntry) -> str:
    """Render a :class:`LearningEntry` to its canonical single-line form."""
    return (
        f"{entry.ts} [task={entry.task_id}] Observed: "
        f'"{entry.observation}" → {entry.promotion_candidate}'
    )


def parse_line(line: str) -> Optional[LearningEntry]:
    """Parse one canonical line into a :class:`LearningEntry`.

    Returns ``None`` for blank lines. Raises :class:`ValueError` if the line
    does not match the canonical format — callers decide whether to tolerate
    or fail loudly.
    """
    stripped = line.strip()
    if not stripped:
        return None
    match = _LINE_RE.match(stripped)
    if not match:
        raise ValueError(
            f"learnings.log line does not match canonical format: {line!r}"
        )
    return LearningEntry(
        ts=match["ts"],
        task_id=match["task_id"],
        observation=match["observation"],
        promotion_candidate=match["candidate"].strip(),
    )


def append(log_path: str | Path, entry: LearningEntry) -> None:
    """Append ``entry`` to ``log_path`` as one canonical line."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(format_entry(entry) + "\n")


def iter_entries(log_path: str | Path) -> Iterator[LearningEntry]:
    """Yield every entry in ``log_path``. Skips blank lines."""
    path = Path(log_path)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            parsed = parse_line(line)
            if parsed is not None:
                yield parsed


def read_all(log_path: str | Path) -> list[LearningEntry]:
    """Read every entry into a list."""
    return list(iter_entries(log_path))
