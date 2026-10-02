# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A full-access task lifts GAIA's shell guardrails, as Claude Code's run does.

Claude Code runs with --dangerously-skip-permissions; GAIA's --full-access only
lifted the path boundary, so its read-only shell policy still refused commands
the other agent ran freely.
"""

import types

import pytest

from gaia.eval import flagship_tasks


class _Console:
    auto_approve_gated_tools = False
    full_access = False


class _FakeAgent:
    built = []

    def __init__(self, config):
        self.config = config
        self.console = _Console()
        _FakeAgent.built.append(self)

    def process_query(self, prompt):
        return {"result": "done"}


@pytest.fixture
def fake_flagship(monkeypatch):
    _FakeAgent.built.clear()
    module = types.SimpleNamespace(
        GaiaAgent=_FakeAgent, GaiaAgentConfig=lambda **kw: kw
    )
    monkeypatch.setitem(__import__("sys").modules, "gaia_agent.agent", module)
    monkeypatch.setattr(flagship_tasks, "drain_memory_extraction", lambda agent: None)
    return _FakeAgent.built


@pytest.mark.parametrize("full_access", [True, False])
def test_full_access_also_lifts_the_shell_guardrails(
    fake_flagship, tmp_path, full_access
):
    flagship_tasks._run_agent(
        "fix it", "m", 5, tmp_path, tmp_path / "mem.db", full_access=full_access
    )
    (agent,) = fake_flagship
    assert agent.console.auto_approve_gated_tools is True
    assert agent.console.full_access is full_access
