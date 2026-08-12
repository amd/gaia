# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guard against stale agent-path references in the AI-instruction surfaces.

Most agents migrated out of ``src/gaia/agents/<id>/`` into standalone hub packages
at ``hub/agents/<id>/python/gaia_agent_<id>/``. The instruction files that steer
Claude (``CLAUDE.md`` and ``.claude/agents``/``.claude/skills``) repeatedly drifted
out of date by still pointing at the old in-core locations — sending agents to dead
paths. This test fails loudly the moment a migrated-agent path reappears in one of
those surfaces, the same way ``test_amd_gaia_urls.py`` guards the docs URL prefix.

Only the *migrated* ids are forbidden. What remains in-core is the framework —
``base/``, ``tools/``, ``builder/``, ``code_index/``, ``registry.py`` — plus
``install_hints.py``. Those keep their ``src/gaia/agents/`` paths and are not flagged.

Keep ``MIGRATED_AGENTS`` in step with ``hub/agents/``: an id that ships as a hub package
but is missing from this tuple is a hole in the guard. ``chat``, ``docqa`` and
``routing`` migrated after this test was written and went unguarded until #2862 — dead
``src/gaia/agents/chat/agent.py`` references reached ``prompt-engineer.md`` and
``test-engineer.md`` in the meantime.
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
    "chat",
    "docqa",
    "routing",
    # Hub-native — never lived in-core, so nothing can go stale. Listed anyway to keep
    # the guard exhaustive against hub/agents/ (see the coverage test below).
    "doc-search",
    "hello-world",
    "word-count",
)

# Trailing slash keeps `code/` from matching the in-core `code_index/`.
STALE_PATH_RE = re.compile(r"src/gaia/agents/(?:" + "|".join(MIGRATED_AGENTS) + r")/")


def _instruction_files():
    files = []
    for root_doc in ("CLAUDE.md", "AGENTS.md", "REVIEW.md"):
        path = REPO_ROOT / root_doc
        if path.exists():
            files.append(path)
    for sub in (".claude/agents", ".claude/skills", ".claude/commands"):
        base = REPO_ROOT / sub
        if base.exists():
            files.extend(p for p in base.rglob("*.md") if p.is_file())
    # The Claude workflows carry prompts and `paths:` filters that go just as stale —
    # a filter pointing at a dead directory silently stops triggering.
    workflows = REPO_ROOT / ".github" / "workflows"
    if workflows.exists():
        files.extend(sorted(workflows.glob("claude*.yml")))
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
        "Stale migrated-agent paths found in AI-instruction surfaces. These agents live "
        "at hub/agents/<id>/python/gaia_agent_<id>/ (id first, runtime second) — update "
        "the reference:\n  " + "\n  ".join(violations)
    )


def test_migrated_agents_list_covers_every_hub_agent():
    """The guard is only as good as its list — fail if a hub agent is unguarded.

    Without this, migrating an agent to hub/ leaves its old in-core path silently
    allowed in every instruction file. That is exactly how chat/docqa/routing slipped
    through.
    """
    hub = REPO_ROOT / "hub" / "agents"
    if not hub.exists():
        pytest.skip("hub/agents/ not present")

    # A hub agent is only *migrated* if it once lived in-core; ids that were born in
    # hub/ (hello-world, word-count, …) never had a src/gaia/agents/<id>/ path to go
    # stale. Treat a still-existing in-core dir as proof it has not migrated.
    guarded = {a.replace("_", "-") for a in MIGRATED_AGENTS}
    in_core = REPO_ROOT / "src" / "gaia" / "agents"

    unguarded = sorted(
        d.name
        for d in hub.iterdir()
        if d.is_dir()
        and d.name.replace("_", "-") not in guarded
        and not (in_core / d.name).exists()
    )

    assert not unguarded, (
        "These agents ship from hub/agents/ but are not in MIGRATED_AGENTS, so their "
        "old src/gaia/agents/<id>/ path would not be flagged. Add any that were once "
        "in-core; agents that were always hub-native can be added too (harmless):\n  "
        + "\n  ".join(unguarded)
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
