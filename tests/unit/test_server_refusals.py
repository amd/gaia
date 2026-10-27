# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools that wait for a command to finish refuse to start a server, and name dev_server.

A small model asked to "launch the page" ran ``python3 -m http.server`` and a
``serve_forever`` script through tools that wait for the command to end, so the
turn hung for minutes and the folder was served to the whole network.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.tools import shell_tools
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.browser.dev_servers import server_in_command, server_in_python

SHELL_SERVERS = [
    "python3 -m http.server 8000",
    "python -m SimpleHTTPServer",
    'cd "Projects/Browser Test Gaia" && python3 -m http.server 8000',
    "python3 -m http.server --directory site 9000",
    "npm run dev",
    "npm start",
    "pnpm dev",
    "yarn preview",
    "bun run serve",
    "NODE_ENV=development nohup npm run dev &",
    "npx serve -s dist",
    "npx vite",
    "npx --yes http-server .",
    "npx live-server",
    "vite",
    "vite --port 3000",
    "next dev",
    "flask --app app run",
    "python -m flask run",
    "uvicorn main:app --reload",
    "php -S localhost:8000",
    "python manage.py runserver",
    "bundle exec jekyll serve",
    'bash -c "python3 -m http.server"',
    'python3 -c "import http.server; http.server.test()"',
]

NOT_SERVERS = [
    "grep vite package.json",
    "npm run build",
    "npm test",
    "npm install",
    "npx vite build",
    "vite build",
    "python3 -m pytest tests",
    "python3 build.py",
    "pip install flask uvicorn",
    "cat server.py",
    "ls; echo serve",
    "git log --grep http.server",
    "next build",
]


@pytest.mark.parametrize("command", SHELL_SERVERS)
def test_a_server_command_line_is_recognised(command):
    assert server_in_command(command) is not None


@pytest.mark.parametrize("command", NOT_SERVERS)
def test_a_command_that_only_mentions_a_server_word_is_not(command):
    assert server_in_command(command) is None


def test_the_folder_a_server_would_serve_is_read_from_the_line():
    assert (
        server_in_command('cd "a b" && cd site && python3 -m http.server').folder
        == "a b/site"
    )
    assert server_in_command("python3 -m http.server -d out").folder == "out"


PYTHON_SERVERS = [
    "import socketserver, http.server\n"
    "with socketserver.TCPServer(('', 8000), http.server.SimpleHTTPRequestHandler) as s:\n"
    "    s.serve_forever()",
    "from http.server import HTTPServer\nHTTPServer(('', 8000), H)",
    "from http.server import ThreadingHTTPServer\nThreadingHTTPServer(('', 8000), H)",
    "from flask import Flask\napp = Flask(__name__)\napp.run(port=5000)",
    "import uvicorn\nuvicorn.run(app)",
    "import subprocess\nsubprocess.run(['python3', '-m', 'http.server'])",
    "from aiohttp import web\nweb.run_app(app)",
]

NOT_PYTHON_SERVERS = [
    "print(6 * 7)",
    "import requests\nprint(requests.get('http://localhost:8000').status_code)",
    "from http import HTTPStatus\nprint(HTTPStatus.OK)",
    "result = pipeline.run(data)",
]


@pytest.mark.parametrize("source", PYTHON_SERVERS)
def test_python_that_serves_forever_is_recognised(source):
    assert server_in_python(source) is not None


@pytest.mark.parametrize("source", NOT_PYTHON_SERVERS)
def test_ordinary_python_is_not(source):
    assert server_in_python(source) is None


# ------------------------------------------------------------- run_shell_command


class _Host(ShellToolsMixin):
    """A shell host; ``offered`` stands in for a chat open in GAIA desktop."""

    def __init__(self, offered: bool):
        self._tools_registry = {"dev_server": object()} if offered else {}


@pytest.fixture
def no_process(monkeypatch):
    ran = []

    def fake_run_step(step, cwd, timeout, granted, waiter=None, bypass=False):
        ran.append(step.text)
        import subprocess

        return subprocess.CompletedProcess(step.text, 0, "hi\n", "")

    monkeypatch.setattr(shell_tools, "_run_step", fake_run_step)
    return ran


def _shell(offered: bool):
    host = _Host(offered)
    host.register_shell_tools()
    return host, get_tool_metadata("run_shell_command")["function"]


def test_run_shell_command_refuses_a_server_and_names_dev_server(no_process, tmp_path):
    host, run = _shell(offered=True)
    result = run(command="python3 -m http.server 8000", working_directory=str(tmp_path))
    assert result["status"] == "error"
    assert no_process == []
    folder = str(tmp_path.resolve())
    assert f'dev_server(action="start", project="{folder}")' in result["error"]
    assert 'load_tools("dev_server")' in result["error"]


def test_the_refusal_comes_before_the_approval_prompt(tmp_path):
    host, _run = _shell(offered=True)
    refusal = host.policy_refusal_for_call(
        "run_shell_command",
        {"command": "npm run dev", "working_directory": str(tmp_path)},
    )
    assert refusal is not None and "dev_server" in refusal["error"]


def test_outside_gaia_desktop_the_refusal_says_to_ask_the_user(no_process, tmp_path):
    _host, run = _shell(offered=False)
    result = run(command="npx serve .", working_directory=str(tmp_path))
    assert result["status"] == "error"
    assert "Ask the user to run the server" in result["error"]
    assert "dev_server" not in result["error"]
    assert no_process == []


def test_an_ordinary_command_still_runs(no_process, tmp_path):
    _host, run = _shell(offered=True)
    result = run(command="echo hi", working_directory=str(tmp_path))
    assert result["status"] == "success", result
    assert no_process == ["echo hi"]


# ------------------------------------------------- run_python, execute_python_file

SERVE_FOREVER = (
    "import socketserver, http.server\n"
    "with socketserver.TCPServer(('', 8000), http.server.SimpleHTTPRequestHandler) as s:\n"
    "    s.serve_forever()\n"
)


@pytest.fixture(scope="module")
def python_tools(tmp_path_factory):
    """One ChatAgent's run_python and execute_python_file (building one is slow)."""
    pytest.importorskip("gaia_agent_chat")
    from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

    from gaia.agents.base.tools import _TOOL_REGISTRY

    root = tmp_path_factory.mktemp("project").resolve()
    (root / "pyproject.toml").write_text("[project]\nname = 'toy'\n")
    saved = dict(_TOOL_REGISTRY)
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(root)
        # Not this machine's ~/.gaia: its embedded Lemonade record is not the test's.
        mp.setenv("GAIA_HOME", str(tmp_path_factory.mktemp("gaia-home")))
        with (
            patch("gaia_agent_chat.agent.RAGSDK"),
            patch("gaia_agent_chat.agent.RAGConfig"),
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
    with patch("gaia_agent_chat.agent.run_python_job") as job:
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
    # As in GAIA desktop, where the pane registers dev_server on the agent.
    monkeypatch.setitem(agent._tools_registry, "dev_server", {})
    site = root / "site"
    site.mkdir(exist_ok=True)
    script = site / "temp_server.py"
    script.write_text(SERVE_FOREVER)
    with patch("gaia_agent_chat.agent.run_python_job") as job:
        result = tools["execute_python_file"](file_path=str(script))
    job.assert_not_called()
    assert result["status"] == "error"
    assert f'dev_server(action="start", project="{site}")' in result["error"]


def test_run_python_outside_gaia_desktop_says_to_ask_the_user(python_tools):
    _root, tools, _agent = python_tools
    with patch("gaia_agent_chat.agent.run_python_job") as job:
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
