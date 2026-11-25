# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Auto-load through a real turn: the flagship, ``Agent.load_skill`` and skills
on disk, with only the model call scripted.

``test_skill_suggestions.py`` covers the decisions against a stub agent. This
drives what a stub cannot show: the body reaching the prompt, the skill's tool
being registered, the turn's own line, and the prefix staying put afterwards.
"""

from __future__ import annotations

import json
import logging

import pytest
from test_prompt_prefix_stability import _assert_each_extends_the_last, _turn

from gaia.agents.tools.shell_tools import skill_granted_binaries
from gaia.skills.manager import SkillManager

LEDGER_MARKER = "ZZ-LEDGER-BODY-ZZ"
RUNNER_MARKER = "ZZ-RUNNER-BODY-ZZ"

#: Shares most of its words with the skill's description, so the word-overlap
#: embedder the fixture installs scores it far above the auto-load floor.
LEDGER_REQUEST = "reconcile the quarterly ledger entries"
RUNNER_REQUEST = "collect the nightly regression failures"

LEDGER_SKILL = f"""---
name: ledger-notes
description: reconcile the quarterly ledger entries
license: MIT
metadata:
  gaia:
    tools:
      - name: tally
        description: Add up ledger entries.
        parameters:
          text: {{type: string, required: true}}
---

# ledger-notes

{LEDGER_MARKER}
"""

LEDGER_TOOLS = '''from gaia.agents.base.tools import tool


@tool
def tally(text: str) -> str:
    """Add up ledger entries."""
    return text
'''

RUNNER_SKILL = f"""---
name: test-runner
description: collect the nightly regression failures
license: MIT
metadata:
  gaia:
    permissions:
      - shell:execute:pytest
---

# test-runner

{RUNNER_MARKER}
"""


def _bundled(root) -> SkillManager:
    """Two skills in an agent-bundled root, the kind auto-load trusts."""
    for name, text in (("ledger-notes", LEDGER_SKILL), ("test-runner", RUNNER_SKILL)):
        directory = root / "bundled" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(text, encoding="utf-8")
    (root / "bundled" / "ledger-notes" / "tools.py").write_text(
        LEDGER_TOOLS, encoding="utf-8"
    )
    return SkillManager(
        agent_skill_dirs=[str(root / "bundled")],
        user_skills_root=root / "user",
        include_claude_roots=False,
    )


@pytest.fixture
def flagship(make_flagship, monkeypatch, tmp_path):
    """The flagship with auto-load on and the two skills above installed."""
    monkeypatch.setenv("GAIA_SKILL_AUTOLOAD", "1")
    # The grant needs its program on PATH; the test never runs it.
    monkeypatch.setattr(
        "gaia.skills.binaries.shutil.which", lambda name: f"/usr/bin/{name}"
    )
    agent, model, _doc = make_flagship()
    assert agent.skill_suggester.autoload_threshold is not None
    agent._skill_manager = _bundled(tmp_path / "skills")
    agent.rebuild_system_prompt()
    return agent, model


def _system(request) -> str:
    return request[0][0]["content"]


def _last_user(request) -> str:
    return [m for m in request[0] if m["role"] == "user"][-1]["content"]


def _sent(request) -> str:
    return json.dumps(request[0], ensure_ascii=False)


def test_a_matching_skill_is_loaded_before_the_first_model_call(flagship):
    agent, model = flagship
    assert LEDGER_MARKER not in agent.system_prompt

    _turn(agent, model, LEDGER_REQUEST, ["Done."])

    assert "ledger-notes" in agent.loaded_skills
    first = model.requests[0]
    # The body is in what the model read on its very first call of the turn.
    assert _sent(first).count(LEDGER_MARKER) == 1
    assert "Loaded ledger-notes because it matches this request" in _last_user(first)
    assert "load_skill(" not in _last_user(first)
    # Agent.load_skill registered the skill's own tool.
    assert "ledger-notes/tally" in agent._tools_registry
    assert {"name": "ledger-notes", "how": "auto"} in agent.describe_skills()


def test_the_prompt_stays_put_on_the_turn_after_an_auto_load(flagship):
    agent, model = flagship
    _turn(agent, model, LEDGER_REQUEST, ["Done."])
    composed = agent.system_prompt

    _turn(agent, model, "thanks, and the totals?", ["Here."])

    assert agent.system_prompt == composed
    assert _system(model.requests[-1]) == _system(model.requests[0])
    _assert_each_extends_the_last(model.requests)
    # Loaded once: the second turn neither reloads nor announces it again.
    assert "Loaded ledger-notes" not in _last_user(model.requests[-1])
    assert _sent(model.requests[-1]).count(LEDGER_MARKER) == 1


def test_a_skill_the_model_unloads_is_not_auto_loaded_again(flagship):
    agent, model = flagship
    _turn(
        agent,
        model,
        LEDGER_REQUEST,
        [{"id": "c1", "name": "unload_skill", "args": {"name": "ledger-notes"}}, "Ok."],
    )
    assert "ledger-notes" not in agent.loaded_skills
    assert agent._unloaded_skills == {"ledger-notes"}

    _turn(agent, model, LEDGER_REQUEST, ["Done."])

    assert "ledger-notes" not in agent.loaded_skills
    asked = _last_user(model.requests[-1])
    assert "call load_skill('ledger-notes') before you start" in asked
    assert "Loaded ledger-notes" not in asked


def _covers(agent, command) -> bool:
    return agent.skill_grant_covers_call("run_shell_command", {"command": command})


def test_in_ask_mode_a_skill_that_grants_a_program_is_only_suggested(flagship):
    agent, model = flagship
    assert agent.full_access_active() is False

    _turn(agent, model, RUNNER_REQUEST, ["Done."])

    assert "test-runner" not in agent.loaded_skills
    assert "pytest" not in skill_granted_binaries(agent)
    assert "call load_skill('test-runner')" in _last_user(model.requests[0])
    assert _covers(agent, "pytest tests -q") is False


def test_full_access_loads_it_and_switching_off_withdraws_the_grant(flagship, caplog):
    agent, model = flagship
    agent.console.full_access = True
    with caplog.at_level(logging.WARNING):
        _turn(agent, model, RUNNER_REQUEST, ["Done."])
    assert "test-runner" in agent.loaded_skills
    assert "pytest" in skill_granted_binaries(agent)
    assert "auto-loaded 'test-runner' under full access" in caplog.text
    assert "pre-approves pytest" in caplog.text

    agent.console.full_access = False
    # Until the next turn starts, the grant from the load still stands.
    assert _covers(agent, "pytest tests -q") is True
    _turn(agent, model, "and now something else entirely", ["Sure."])

    assert "test-runner" not in agent.loaded_skills
    assert "pytest" not in skill_granted_binaries(agent)
    assert _covers(agent, "pytest tests -q") is False
