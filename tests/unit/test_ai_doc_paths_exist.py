# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Backtick-quoted repo paths in the AI-facing docs must exist on disk.

`util/check_doc_links.py` only walks `docs/` (see #3535, #4248), so a path a
subagent definition or a top-level contributor doc cites can rot silently —
sessions copy the example and go looking for a file that was deleted or
renamed. This test walks the paths a coding agent actually reads before
touching code: `.claude/agents/`, `.claude/skills/`, `AGENTS.md`,
`CONTRIBUTING.md`.

A doc bundled inside a skill may also cite a file next to it (a skill's
`README.md` naming its own `scripts/`, or a project template's `README.md`
naming its own `src/`), so those claims also resolve from the doc's directory.

Filesystem-only and offline, so it can't be flaky the way a network-checking
link test can.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_SKILLS = REPO_ROOT / ".claude" / "skills"

SOURCE_FILES = sorted(
    [*(REPO_ROOT / ".claude" / "agents").glob("*.md")]
    + [*(REPO_ROOT / ".claude" / "skills").glob("**/*.md")]
    + [REPO_ROOT / "AGENTS.md", REPO_ROOT / "CONTRIBUTING.md"]
)

# A backtick-quoted repo path: starts with a known top-level dir, contains at
# least one more path segment, and may carry a trailing :line or :line-line.
_PATH_RE = re.compile(
    r"`("
    r"(?:src|hub|tests|docs|tui|cpp|util|scripts|eval)/"
    r"[A-Za-z0-9_./-]+"
    r")(?::\d+(?:-\d+)?)?`"
)

# Template placeholders (`<id>`, `<agent>`, `...`) describe a *shape*, not a
# real path — e.g. `hub/agents/<id>/python/gaia_agent_<id>/agent.py` or
# `src/gaia/.../file.py`.
_PLACEHOLDER_RE = re.compile(r"[<>]|\.\.\.")


def _iter_path_claims():
    for path in SOURCE_FILES:
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _PATH_RE.finditer(line):
                claim = match.group(1)
                if _PLACEHOLDER_RE.search(claim):
                    continue
                yield (path, lineno, claim)


def _claim_exists(source: Path, claim: str) -> bool:
    if (REPO_ROOT / claim).exists():
        return True
    return source.is_relative_to(CLAUDE_SKILLS) and (source.parent / claim).exists()


@pytest.mark.parametrize(
    "source, lineno, claim",
    list(_iter_path_claims()),
    ids=lambda v: str(v) if not isinstance(v, Path) else v.name,
)
def test_path_exists(source, lineno, claim):
    assert _claim_exists(source, claim), (
        f"{source.relative_to(REPO_ROOT)}:{lineno} cites `{claim}`, "
        "which does not exist on disk"
    )


def test_a_skill_doc_resolves_files_beside_it(tmp_path, monkeypatch):
    """Only a skill's own docs get the doc-relative lookup, and only for real files."""
    skills = tmp_path / ".claude" / "skills"
    skill_doc = skills / "demo" / "README.md"
    (skill_doc.parent / "scripts").mkdir(parents=True)
    (skill_doc.parent / "scripts" / "run.py").write_text("")
    agent_doc = tmp_path / ".claude" / "agents" / "demo.md"
    (agent_doc.parent / "scripts").mkdir(parents=True)
    (agent_doc.parent / "scripts" / "run.py").write_text("")
    monkeypatch.setitem(globals(), "REPO_ROOT", tmp_path)
    monkeypatch.setitem(globals(), "CLAUDE_SKILLS", skills)

    assert _claim_exists(skill_doc, "scripts/run.py")
    assert not _claim_exists(skill_doc, "scripts/missing.py")
    assert not _claim_exists(skill_doc, "src/run.py")
    # Outside .claude/skills/ a path must still exist from the repo root.
    assert not _claim_exists(agent_doc, "scripts/run.py")


def test_something_was_checked():
    """Guard against the glob/regex silently matching nothing."""
    assert len(list(_iter_path_claims())) > 20
