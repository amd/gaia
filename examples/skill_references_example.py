#!/usr/bin/env python3
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Use a GAIA skill's reference files from Python.

A skill is a folder with a SKILL.md, plus any files it wants to point at —
by convention `references/` for documents and `scripts/` for helpers. GAIA
loads SKILL.md when the skill is activated; reference files are read only
when they're needed, so they don't fill the model's context up front.

Run it:   python skill_references_example.py
It builds a demo skill in a temporary folder, so your own skills are untouched.
"""

import tempfile
from pathlib import Path

from gaia.skills import SkillManager

SKILL_MD = """\
---
name: query-helper
description: Write search queries using our query syntax. Use when the user asks for a search query.
---

# Query helper

Before writing a query, read `references/query-syntax.md` for the operators
we support, then build the query from them.
"""

QUERY_SYNTAX = """\
# Query syntax

- `AND`, `OR`, `NOT` combine terms (upper case).
- `"exact phrase"` matches a phrase.
- `site:example.com` limits results to one site.
"""


def make_demo_skill(skills_root: Path) -> None:
    skill_dir = skills_root / "query-helper"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (skill_dir / "references" / "query-syntax.md").write_text(
        QUERY_SYNTAX, encoding="utf-8"
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        skills_root = Path(tmp) / "skills"
        make_demo_skill(skills_root)

        # Point the manager at our demo folder. Without arguments it searches
        # the normal places (~/.gaia/skills, agent-bundled skills, .claude/skills).
        manager = SkillManager(user_skills_root=skills_root, include_claude_roots=False)

        skill = manager.load("query-helper")
        print("Skill:", skill.name)
        print("Folder:", skill.directory)
        print("\nInstructions (SKILL.md body):\n" + skill.body)

        # Paths in the instructions are relative to the skill's folder.
        ref = skill.directory / "references" / "query-syntax.md"
        print("Reference file:", ref.relative_to(skill.directory))
        print(ref.read_text(encoding="utf-8"))

        # List everything the skill ships alongside SKILL.md.
        bundled = sorted(
            p.relative_to(skill.directory).as_posix()
            for p in skill.directory.rglob("*")
            if p.is_file() and p.name != "SKILL.md"
        )
        print("Bundled files:", bundled)


if __name__ == "__main__":
    main()
