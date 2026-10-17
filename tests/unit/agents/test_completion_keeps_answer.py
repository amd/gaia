# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A finished answer that names its output folder and a script-written file
reaches the user as written. On rc13 the completion check replaced it with
"I can't confirm this is done" plus a raw dump (agent UI test, kw-13)."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool


class _LoopAgent(Agent):
    def _get_system_prompt(self):
        return "Use the tools."

    def _register_tools(self):
        @tool
        def read_file(file_path: str) -> dict:
            """Read a file."""
            return {
                "status": "success",
                "file_path": file_path,
                "content": Path(file_path).read_text(),
            }

        @tool
        def run_python(code: str) -> dict:
            """Execute Python in a real child process."""
            done = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                check=False,
            )
            return {
                "status": "success" if done.returncode == 0 else "error",
                "stdout": done.stdout,
                "stderr": done.stderr,
                "return_code": done.returncode,
            }


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _LoopAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *a, **kw: False
    agent.console = MagicMock()
    agent.console.cancelled = None
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _script(agent, *turns):
    sent = []
    left = list(turns)

    def send(messages, *a, **kw):
        sent.append([dict(m) for m in messages])
        assert left, "unexpected model call"
        return MagicMock(text=json.dumps(left.pop(0)), stats={}, finish_reason="stop")

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    return sent


_BUILD = (
    "import os\n"
    "out = os.path.join(os.getcwd(), 'track' + 'er')\n"
    "os.makedirs(out, exist_ok=True)\n"
    "open(os.path.join(out, 'action_tracker.xlsx'), 'wb').write(b'PK\\x03\\x04')\n"
    "open(os.path.join(out, 'followup_email.md'), 'w').write('Hi all,\\n18 items.\\n')\n"
)

_ANSWER = (
    "Both files are in place and verified.\n\n"
    "### `action_tracker.xlsx` - 18 rows\n"
    "Columns: Owner | Task | Due | Status | Source Meeting.\n\n"
    "The tracker and `followup_email.md` are in tracker/."
)


def test_a_verified_answer_naming_a_folder_and_script_output_is_kept(agent, tmp_path):
    sent = _script(
        agent,
        {"tool": "run_python", "tool_args": {"code": _BUILD}},
        {
            "tool": "read_file",
            "tool_args": {"file_path": str(tmp_path / "tracker" / "followup_email.md")},
        },
        {"answer": _ANSWER},
    )
    result = agent.process_query(
        "Build the action tracker and draft the follow-up email into tracker/"
    )
    assert "[check:completion]" not in str(sent)
    assert result["status"] == "success", result["result"]
    assert result["result"].startswith("Both files are in place and verified.")
    assert "I can't confirm this is done" not in result["result"]
