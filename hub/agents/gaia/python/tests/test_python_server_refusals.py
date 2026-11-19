# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""run_python and execute_python_file refuse a script that serves forever.

The shell-side refusals live in tests/unit/test_server_refusals.py; these need
the flagship's ChatAgent, so they ship with the hub package.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

SERVE_FOREVER = (
    "import socketserver, http.server\n"
    "with socketserver.TCPServer(('', 8000), http.server.SimpleHTTPRequestHandler) as s:\n"
    "    s.serve_forever()\n"
)


@pytest.fixture(scope="module")
def python_tools(tmp_path_factory):
    """One ChatAgent's run_python and execute_python_file (building one is slow)."""
    from gaia_agent.chat_agent import ChatAgent, ChatAgentConfig

    from gaia.agents.base.tools import _TOOL_REGISTRY

    root = tmp_path_factory.mktemp("project").resolve()
    (root / "pyproject.toml").write_text("[project]\nname = 'toy'\n")
    saved = dict(_TOOL_REGISTRY)
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(root)
        # Not this machine's ~/.gaia: its embedded Lemonade record is not the test's.
        mp.setenv("GAIA_HOME", str(tmp_path_factory.mktemp("gaia-home")))
        with (
            patch("gaia_agent.chat_agent.RAGSDK"),
            patch("gaia_agent.chat_agent.RAGConfig"),
        ):
            agent = ChatAgent(
                ChatAgentConfig(silent_mode=True, allowed_paths=[str(root)])
            )
        tools = {
            n: _TOOL_REGISTRY[n]["function"]
            for n in ("run_python", "execute_python_file")
        }
        yield root, tools, agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def test_run_python_refuses_a_server_before_any_process(python_tools, monkeypatch):
    root, tools, _agent = python_tools
    monkeypatch.chdir(root)
    with patch("gaia_agent.chat_agent.run_python_job") as job:
        result = tools["run_python"](code=SERVE_FOREVER)
    job.assert_not_called()
    assert result["status"] == "error"
    assert "never exits" in result["error"]


def test_run_python_still_runs_a_normal_snippet(python_tools, monkeypatch):
    root, tools, _agent = python_tools
    monkeypatch.chdir(root)
    result = tools["run_python"](code="print(6 * 7)")
    assert result["status"] == "success", result
    assert result["stdout"].strip() == "42"


def test_execute_python_file_reads_the_script_and_refuses_a_server(
    python_tools, monkeypatch
):
    root, tools, agent = python_tools
    monkeypatch.chdir(root)
    # As in GAIA desktop, where the chat can load the browser-pane skill.
    monkeypatch.setattr(agent, "_browser_pane_attached", True, raising=False)
    site = root / "site"
    site.mkdir(exist_ok=True)
    script = site / "temp_server.py"
    script.write_text(SERVE_FOREVER)
    with patch("gaia_agent.chat_agent.run_python_job") as job:
        result = tools["execute_python_file"](file_path=str(script))
    job.assert_not_called()
    assert result["status"] == "error"
    assert f'dev_server(action="start", project="{site}")' in result["error"]


def test_run_python_outside_gaia_desktop_says_to_ask_the_user(python_tools):
    _root, tools, _agent = python_tools
    with patch("gaia_agent.chat_agent.run_python_job") as job:
        result = tools["run_python"](code="import uvicorn\nuvicorn.run(app)")
    job.assert_not_called()
    assert "Ask the user to run the server" in result["error"]


def test_execute_python_file_still_runs_a_normal_script(python_tools, monkeypatch):
    root, tools, _agent = python_tools
    monkeypatch.chdir(root)
    script = root / "hello.py"
    script.write_text("print('hello')\n")
    result = tools["execute_python_file"](file_path=str(script))
    assert result["status"] == "success", result
    assert result["stdout"].strip() == "hello"
