# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``execute_python_file`` runs what its approval prompt showed, or not at all."""

import pytest
from gaia_agent.chat_agent import ChatAgent, ChatAgentConfig

from gaia.agents.base.call_risk import SCRIPT_ARG
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.security import PathValidator


class _Recorder(AgentConsole):
    """Approves every prompt, after an optional step taken while it is open."""

    def __init__(self):
        super().__init__()
        self.prompts = []
        self.before_answer = None

    def confirm_tool_execution(self, tool_name, tool_args):
        self.prompts.append((tool_name, tool_args))
        if self.before_answer:
            self.before_answer()
        return True


@pytest.fixture
def chat(tmp_path, monkeypatch):
    workspace = tmp_path / "chat"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        PathValidator, "_prompt_user_for_access", lambda self, path: False
    )
    saved = dict(_TOOL_REGISTRY)
    try:
        agent = ChatAgent(
            ChatAgentConfig(
                silent_mode=True,
                allowed_paths=[str(workspace)],
                working_directory=str(workspace),
            )
        )
        agent.console = _Recorder()
        yield agent, workspace
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_the_prompt_shows_the_script_and_the_approved_script_runs(chat):
    agent, workspace = chat
    (workspace / "job.py").write_text("print('ran as shown')\n", encoding="utf-8")

    result = agent._execute_tool("execute_python_file", {"file_path": "job.py"})

    [(name, shown)] = agent.console.prompts
    assert name == "execute_python_file"
    assert shown[SCRIPT_ARG] == {"text": "print('ran as shown')", "hidden_lines": 0}
    assert result["status"] == "success", result
    assert "ran as shown" in result["stdout"]


def test_a_script_swapped_while_the_prompt_is_open_does_not_run(chat):
    agent, workspace = chat
    script = workspace / "job.py"
    marker = workspace / "ran.txt"
    script.write_text("print('harmless')\n", encoding="utf-8")
    swapped = f"open({str(marker)!r}, 'w').write('x')\n"
    agent.console.before_answer = lambda: script.write_text(swapped, encoding="utf-8")

    result = agent._execute_tool("execute_python_file", {"file_path": "job.py"})

    [(_, shown)] = agent.console.prompts
    assert shown[SCRIPT_ARG]["text"] == "print('harmless')"
    assert result["status"] == "error", result
    assert "changed after the approval prompt" in result["error"]
    assert not marker.exists()

    # Asked again, the prompt shows the file as it now is, and that is what runs.
    agent.console.before_answer = None
    again = agent._execute_tool("execute_python_file", {"file_path": "job.py"})

    assert agent.console.prompts[-1][1][SCRIPT_ARG]["text"] == swapped.strip()
    assert again["status"] == "success", again
    assert marker.exists()
