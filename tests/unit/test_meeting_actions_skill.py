# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The meeting-actions skill: a lean body that links working reference files."""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[2] / "hub" / "skills" / "meeting-actions"


def _body() -> str:
    return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").split("\n---\n", 2)[-1]


def _python_blocks(text: str) -> list:
    return re.findall(r"```python\n(.*?)\n```", text, re.S)


def test_body_is_lean_and_keeps_the_hard_rules():
    body = _body()
    assert len(body) // 4 <= 1200
    for rule in (
        "Commitment test",
        "accepted requests",
        "informal promises",
        "Decisions stay separate",
        "Merge duplicates and chases",
    ):
        assert rule in body, rule


def test_every_linked_reference_exists():
    refs = re.findall(r"`(references/[\w-]+\.md)`", _body())
    assert len(refs) >= 5
    for ref in refs:
        assert (SKILL_DIR / ref).is_file(), ref


def test_reference_scripts_compile_and_the_duplicate_check_flags_a_chase():
    for path in (SKILL_DIR / "references").glob("*.md"):
        for block in _python_blocks(path.read_text(encoding="utf-8")):
            compile(block, str(path), "exec")

    script = _python_blocks(
        (SKILL_DIR / "references" / "merge-and-views.md").read_text(encoding="utf-8")
    )[0]
    script = script.replace(
        '{"id": "A-001", "task": "Send revised budget to finance", "owner": "Dan", "due_iso": "2024-05-10", "status": "Open"},',
        '{"id": "A-001", "task": "Send revised budget to finance", "owner": "Dan", "due_iso": "2024-05-10", "status": "Open"},\n'
        '    {"id": "A-002", "task": "Chase Dan for the revised budget to finance", "owner": "Priya", "due_iso": "", "status": "Open"},',
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(script, "merge-and-views", "exec"), {})
    assert "possible duplicate: A-001 / A-002" in out.getvalue()
