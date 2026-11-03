# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A non-Claude model's system prompt in a repo with guidance matches a golden.

The golden was rendered by the base agent, before repositories' CLAUDE.md,
AGENTS.md and ``.claude/skills`` could reach the prompt. A repo carrying all
three must still give a model that does not get the project context
(gaia.project_context) exactly that prompt. A mismatch means either guidance
leaked into the prompt, or the base prompt changed on purpose: then
regenerate with

    GAIA_UPDATE_PROMPT_GOLDEN=1 python -m pytest hub/agents/gaia/python/tests/test_non_claude_prompt_golden.py

and check the diff shows only the intended change.
"""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path

import pytest
from test_prompt_prefix_stability import flagship  # noqa: F401 - fixture

from gaia.agents.base import project_map, system_context
from gaia.agents.base.project_map import PlatformQuirks

# A literal, not gaia.project_context's constant, so this file also runs on a
# checkout from before that module, which is how the golden is rendered.
PROJECT_CONTEXT_ENV = "GAIA_PROJECT_CONTEXT"

GOLDEN = Path(__file__).parent / "fixtures" / "non_claude_system_prompt.txt"
MODEL = "Gemma-4-E4B-it-GGUF"
UPDATE_ENV = "GAIA_UPDATE_PROMPT_GOLDEN"

_QUIRKS = PlatformQuirks(
    path_separator="/",
    path_quoting="wrap in single quotes: '/home/me/My Docs'",
    shell_dialect="bash",
)


@pytest.fixture
def guided_repo(monkeypatch, tmp_path):
    """A repo with every kind of guidance a non-Claude model must not see."""
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("Run `make check` first.\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("Use tabs.\n", encoding="utf-8")
    skill = root / ".claude" / "skills" / "release-steps"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: release-steps\ndescription: How this repo cuts a release.\n"
        "---\n\nTag, then publish.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(project_map.PROJECT_ROOT_ENV, str(root))
    monkeypatch.setenv("GAIA_SKILL_DISCOVERY", "1")
    monkeypatch.delenv(PROJECT_CONTEXT_ENV, raising=False)
    # Older checkouts find ~/.gaia/skills through this, not GAIA_HOME.
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "home"))
    # Pin what differs between machines: the host's shell and its PATH.
    monkeypatch.setattr(project_map, "detect_platform_quirks", lambda: _QUIRKS)
    monkeypatch.setattr(
        project_map, "probe_binaries", lambda names: {n: False for n in names}
    )
    monkeypatch.setattr(
        system_context, "probe_binaries", lambda names: {n: False for n in names}
    )
    project_map.clear_project_map_cache()
    yield root
    project_map.clear_project_map_cache()


#: The host's OS block (name, version, home, OS-specific commands). One per OS,
#: so it is replaced whole rather than pinned to one platform's text.
_ENVIRONMENT_BLOCK = re.compile(r"\*\*ENVIRONMENT:\*\*.*?\n\n", re.DOTALL)


def _normalized(prompt: str, agent, tmp_path: Path) -> str:
    """*prompt* with what differs per run and per machine replaced by markers.

    The pytest folder becomes ``<TMP>`` (paths under it written with ``/``),
    the scratch folder (named from a hash of the user) ``<SCRATCH>``, and the
    OS block ``<ENVIRONMENT>``.
    """
    out = _ENVIRONMENT_BLOCK.sub("<ENVIRONMENT>\n\n", prompt)
    scratch = getattr(agent, "scratch_dir", None)
    if scratch is not None:
        out = out.replace(str(scratch), "<SCRATCH>")
    for form in {str(tmp_path), tmp_path.as_posix(), os.path.realpath(tmp_path)}:
        out = out.replace(form, "<TMP>")
    return re.sub(r"<TMP>[^\s\"'`),;]*", lambda m: m.group(0).replace("\\", "/"), out)


def test_a_non_claude_prompt_matches_the_base_golden(guided_repo, flagship, tmp_path):
    agent, _model, _doc = flagship
    agent.config.project_context = "auto"
    agent.model_id = MODEL
    agent._use_claude = False
    agent.rebuild_system_prompt()
    prompt = _normalized(agent.system_prompt, agent, tmp_path)

    assert "Root: <TMP>/repo" in prompt, "the project map must name the pinned repo"
    assert "<ENVIRONMENT>" in prompt and "<SCRATCH>" in prompt
    if os.environ.get(UPDATE_ENV):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(prompt, encoding="utf-8", newline="\n")
        pytest.skip(f"wrote {GOLDEN}")
    golden = GOLDEN.read_text(encoding="utf-8")
    if prompt != golden:
        diff = "".join(
            list(
                difflib.unified_diff(
                    golden.splitlines(keepends=True),
                    prompt.splitlines(keepends=True),
                    "golden",
                    "rendered",
                )
            )[:80]
        )
        pytest.fail(
            "A non-Claude model's system prompt in a repo with CLAUDE.md, "
            "AGENTS.md and .claude/skills differs from the golden. If project "
            "guidance leaked in, fix that; if the base prompt changed on purpose, "
            f"rerun with {UPDATE_ENV}=1 and review the golden's diff.\n{diff}"
        )


def test_the_golden_catches_guidance_leaking_in(guided_repo, flagship, tmp_path):
    """Forcing the guidance on must break the match, or the golden proves nothing."""
    agent, _model, _doc = flagship
    agent.config.project_context = "full"
    agent.model_id = MODEL
    agent._use_claude = False
    agent.rebuild_system_prompt()
    prompt = _normalized(agent.system_prompt, agent, tmp_path)
    assert "Run `make check` first." in prompt and "release-steps" in prompt
    assert prompt != GOLDEN.read_text(encoding="utf-8")
