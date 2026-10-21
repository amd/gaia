# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship exposes ``log-triage/triage_logs`` once the skill loads.

The tool ships in the skill's own ``tools.py``, not in the core agent, so the
default surface (and the count test_capability_matrix.py pins) is unchanged until the model or the
user loads ``log-triage``; loading adds exactly that one tool, and unloading
takes it away again.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.tools import _TOOL_REGISTRY

TOOL = "log-triage/triage_logs"


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    saved = dict(_TOOL_REGISTRY)
    try:
        yield GaiaAgent(config=GaiaAgentConfig(silent_mode=True))
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_loading_the_skill_adds_exactly_the_triage_tool(agent):
    before = set(agent._tools_registry)
    assert TOOL not in before

    skill = agent.load_skill("log-triage")

    after = set(agent._tools_registry)
    assert after - before == {TOOL}
    assert all(t in after for t in skill.gaia.tools_required)
    assert agent.unload_skill("log-triage")
    assert TOOL not in set(agent._tools_registry)


def test_the_loaded_tool_runs_through_the_agent(agent, tmp_path):
    t0 = datetime(2025, 3, 4, tzinfo=timezone.utc).timestamp()
    lines = []
    for i in range(240):
        ts = datetime.fromtimestamp(t0 + i * 60, tz=timezone.utc)
        lines.append(f"{ts:%Y-%m-%d %H:%M:%S},000 app-0{i % 3} INFO served request {i}")
    log = tmp_path / "app.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    agent.load_skill("log-triage")
    try:
        result = agent._execute_tool(TOOL, {"paths": [str(log)]})
    finally:
        agent.unload_skill("log-triage")
    assert result["status"] == "success", result
    assert result["coverage"]["entries"] == 240
    assert result["checks"]["ok"]
