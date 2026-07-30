# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Shared helpers for the skills unit tests (issue #888).

Every helper works off ``tmp_path`` so a test never reads or writes the
developer's real ``~/.gaia/skills`` or ``~/.claude/skills`` — the cold-state
rule from CLAUDE.md.
"""

from __future__ import annotations

import shutil
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "skills"


def copy_fixture(name: str, into: Path, *, as_name: str | None = None) -> Path:
    """Copy a fixture skill into ``into``, optionally under a different name."""
    source = FIXTURES / name
    if not source.is_dir():  # pragma: no cover - guards a typo in a test
        raise AssertionError(f"No skill fixture named {name!r} in {FIXTURES}")
    target = into / (as_name or source.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


def write_skill_dir(root: Path, name: str, text: str, tools: str | None = None) -> Path:
    """Write a one-off skill directory inline (for negative-path tests)."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(text, encoding="utf-8")
    if tools is not None:
        (directory / "tools.py").write_text(tools, encoding="utf-8")
    return directory


def isolated_manager(tmp_path: Path, **kwargs):
    """A :class:`SkillManager` whose roots are all inside ``tmp_path``.

    Claude-import roots are pointed at an empty tmp directory rather than
    disabled, so precedence tests exercise the real three-root ordering.
    """
    from gaia.skills.manager import SkillManager

    user_root = kwargs.pop("user_skills_root", tmp_path / "gaia-home" / "skills")
    claude_dirs = kwargs.pop("claude_skill_dirs", [tmp_path / "claude" / "skills"])
    return SkillManager(
        user_skills_root=user_root,
        claude_skill_dirs=claude_dirs,
        **kwargs,
    )
