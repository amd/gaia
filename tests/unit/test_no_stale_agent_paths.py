# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guard against stale agent-path references in the AI-instruction surfaces.

Most agents migrated out of ``src/gaia/agents/<id>/`` into standalone hub packages
at ``hub/agents/<id>/python/gaia_agent_<id>/``. The instruction files that steer
Claude (``CLAUDE.md`` and ``.claude/agents``/``.claude/skills``) repeatedly drifted
out of date by still pointing at the old in-core locations — sending agents to dead
paths. This test fails loudly the moment a migrated-agent path reappears in one of
those surfaces, the same way ``test_amd_gaia_urls.py`` guards the docs URL prefix.

Only the *migrated* ids are forbidden. The agents still living in-core
(``base``, ``tools``, ``chat``, ``docqa``, ``builder``, ``routing``, ``code_index``)
keep their ``src/gaia/agents/<id>/`` paths and are intentionally NOT flagged.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Agents that moved to hub/agents/<id>/python/ — their src/gaia/agents/<id>/ path is dead.
MIGRATED_AGENTS = (
    "code",
    "analyst",
    "browser",
    "fileio",
    "email",
    "summarize",
    "jira",
    "blender",
    "docker",
    "sd",
    "connectors_demo",
    "emr",
)

# Trailing slash keeps `code/` from matching the in-core `code_index/`.
STALE_PATH_RE = re.compile(r"src/gaia/agents/(?:" + "|".join(MIGRATED_AGENTS) + r")/")


def _instruction_files():
    files = []
    for root_doc in ("CLAUDE.md", "AGENTS.md"):
        path = REPO_ROOT / root_doc
        if path.exists():
            files.append(path)
    for sub in (".claude/agents", ".claude/skills"):
        base = REPO_ROOT / sub
        if base.exists():
            files.extend(p for p in base.rglob("*.md") if p.is_file())
    return files


def test_no_stale_migrated_agent_paths():
    """No instruction file may point at a migrated agent's old in-core path."""
    violations = []
    for path in _instruction_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in STALE_PATH_RE.finditer(line):
                rel = path.relative_to(REPO_ROOT)
                violations.append(f"{rel}:{lineno}: {match.group(0)}")

    assert not violations, (
        "Stale migrated-agent paths found in AI-instruction surfaces. These agents "
        "moved to hub/agents/<id>/python/gaia_agent_<id>/ — update the reference "
        "(or, for in-core agents like chat/docqa/builder/routing, this guard does not "
        "apply):\n  " + "\n  ".join(violations)
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
