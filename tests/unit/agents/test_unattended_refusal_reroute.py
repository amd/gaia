# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A call refused only because nobody could approve it is not a "no".

Unattended (pre-approved) runs refuse shell commands outside the no-prompt
list. That refusal must not close the auto-approved routes to the same thing
— a skill's own fetch tool, ``write_file`` — while a person's "no", a delete,
and a cloud-metadata reach still do. Drives the real ``Agent._execute_tool``
and the real shell policy; the other tools are probes and nothing is fetched.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import (
    SilentConsole,
    unaskable_denial_message,
    unattended_denial_message,
    user_denial_message,
)
from gaia.agents.base.denied_effects import UNATTENDED_REFUSAL, DeniedEffects
from gaia.agents.base.tools import tool
from gaia.agents.tools.shell_tools import (
    ALLOWED_COMMANDS,
    ShellToolsMixin,
    private_fetch_note,
    web_tools_refuse,
)

FEED = "http://127.0.0.1:8080/feed.xml"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE", "GAIA_HOME"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.delenv("GAIA_WEB_ALLOWED_HOSTS", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


class _NoSayer(SilentConsole):
    """A person at the prompt who says no to the shell and yes to the rest."""

    def __init__(self, reason=user_denial_message, unattended=False):
        super().__init__()
        self.reason = reason
        self.unattended = unattended

    def confirm_tool_execution(self, tool_name, tool_args):
        if tool_name == "run_shell_command":
            return self.deny_tool_execution(
                tool_name, self.reason(tool_name), unattended=self.unattended
            )
        return True


class _Agent(Agent, ShellToolsMixin):
    with_file_tools = True

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        self.register_shell_tools()
        agent = self
        agent.ran = []
        if not self.with_file_tools:
            return

        @tool
        def fetch_rss(url: str) -> dict:
            """Probe feed reader."""
            agent.ran.append(("fetch_rss", url))
            return {"status": "success"}

        @tool
        def fetch_page(url: str) -> dict:
            """Probe page fetcher."""
            agent.ran.append(("fetch_page", url))
            return {"status": "success"}

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Probe writer."""
            agent.ran.append(("write_file", file_path))
            return {"status": "success"}

        @tool
        def read_file(file_path: str) -> dict:
            """Probe reader."""
            agent.ran.append(("read_file", file_path))
            return {"status": "success"}

        @tool
        def browse_directory(path: str = ".") -> dict:
            """Probe lister."""
            agent.ran.append(("browse_directory", path))
            return {"status": "success"}

        @tool
        def run_python(code: str) -> dict:
            """Probe Python runner."""
            agent.ran.append(("run_python", code))
            return {"status": "success"}

    def _create_console(self):
        return self._console_override


def _agent(console, cwd, file_tools=True):
    with patch("gaia.agents.base.agent.AgentSDK"):
        _Agent._console_override = console
        _Agent.with_file_tools = file_tools
        agent = _Agent(silent_mode=True, skip_lemonade=True)
    agent.max_commands_per_minute = 1000
    agent.max_commands_per_10_seconds = 1000
    agent._denied_effects = DeniedEffects(str(cwd))
    return agent


def _unattended(cwd, file_tools=True):
    return _agent(SilentConsole(auto_approve_gated_tools=True), cwd, file_tools)


def _shell(agent, command):
    return agent._execute_tool("run_shell_command", {"command": command})


# ---------------------------------------------------------------------------
# Unattended refusals leave the auto-approved routes open
# ---------------------------------------------------------------------------


def test_a_refused_curl_does_not_block_the_skills_feed_tool(_isolated, monkeypatch):
    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")
    agent = _unattended(_isolated)

    refused = _shell(agent, f"curl {FEED}")
    feed = agent._execute_tool("fetch_rss", {"url": FEED})
    page = agent._execute_tool("fetch_page", {"url": FEED})

    assert refused["executed"] is False
    assert refused[UNATTENDED_REFUSAL] is True
    assert feed["status"] == "success" and page["status"] == "success"
    assert agent.ran == [("fetch_rss", FEED), ("fetch_page", FEED)]
    assert not agent._denied_effects


def test_a_refused_curl_to_a_public_host_does_not_block_fetch_page(_isolated):
    agent = _unattended(_isolated)

    _shell(agent, "curl -s https://example.com/rss")
    page = agent._execute_tool("fetch_page", {"url": "https://example.com/rss"})

    assert page["status"] == "success"


def test_a_refused_copy_item_does_not_block_write_file(_isolated):
    agent = _unattended(_isolated)

    refused = _shell(agent, "Copy-Item a.txt b.txt")
    written = agent._execute_tool(
        "write_file", {"file_path": "b.txt", "content": "copied"}
    )

    assert refused["executed"] is False
    assert written["status"] == "success"
    assert agent.ran == [("write_file", "b.txt")]


@pytest.mark.parametrize(
    "reason", [unattended_denial_message, unaskable_denial_message]
)
def test_a_prompt_nothing_could_show_records_no_refusal(_isolated, reason):
    agent = _agent(_NoSayer(reason, unattended=True), _isolated)

    denied = _shell(agent, "Copy-Item a.txt b.txt")
    written = agent._execute_tool(
        "write_file", {"file_path": "b.txt", "content": "copied"}
    )

    assert denied["status"] == "denied"
    assert "hint" not in denied
    assert not agent._denied_effects
    assert written["status"] == "success"


@pytest.mark.parametrize("terminal", [False, True])
def test_the_terminal_console_marks_its_unasked_denials(_isolated, terminal):
    console = SilentConsole()
    with (
        patch(
            "gaia.agents.base.console.terminal_is_interactive", return_value=terminal
        ),
        patch.object(SilentConsole, "_display", return_value=False),
    ):
        console.confirm_tool_execution("run_shell_command", {"command": "x"})

    assert console.confirmation_unattended("run_shell_command") is True


def test_the_agent_ui_and_api_mark_their_unasked_denials(monkeypatch):
    from gaia.api.sse_handler import SSEOutputHandler as ApiHandler
    from gaia.ui.sse_handler import SSEOutputHandler as UiHandler

    monkeypatch.setattr(
        "gaia.agents.base.console.auto_approve_env_enabled", lambda: False
    )
    ui = UiHandler()
    ui.background_mode = True
    api = ApiHandler()

    for handler in (ui, api):
        assert handler.confirm_tool_execution("write_file", {}) is False
        assert handler.confirmation_unattended("write_file") is True


# ---------------------------------------------------------------------------
# What still closes the other routes
# ---------------------------------------------------------------------------


def test_a_no_worded_some_other_way_still_blocks(_isolated):
    """A console with its own wording for a person's no fails closed."""
    agent = _agent(_NoSayer(lambda name: "The operator rejected this."), _isolated)

    _shell(agent, "Copy-Item a.txt b.txt")
    rerouted = agent._execute_tool(
        "write_file", {"file_path": "b.txt", "content": "copied"}
    )

    assert rerouted["status"] == "denied"
    assert agent.ran == []


def test_a_persons_no_still_blocks_write_file(_isolated):
    agent = _agent(_NoSayer(), _isolated)

    denied = _shell(agent, "Copy-Item a.txt b.txt")
    rerouted = agent._execute_tool(
        "write_file", {"file_path": "b.txt", "content": "copied"}
    )

    assert denied["status"] == "denied"
    assert rerouted["status"] == "denied"
    assert rerouted["executed"] is False
    assert "declined" in rerouted["error"]
    assert agent.ran == []


def test_a_persons_no_to_curl_still_blocks_the_feed_tool(_isolated, monkeypatch):
    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")
    agent = _agent(_NoSayer(), _isolated)

    _shell(agent, f"curl {FEED}")
    feed = agent._execute_tool("fetch_rss", {"url": FEED})

    assert feed["status"] == "denied"
    assert agent.ran == []


def test_a_hard_policy_block_still_blocks(_isolated):
    agent = _unattended(_isolated)
    agent.policy_refusal_for_call = lambda name, args: (
        {"status": "error", "tier": "refuse", "error": "not allowed"}
        if name == "run_shell_command"
        else None
    )

    _shell(agent, "Copy-Item a.txt b.txt")
    rerouted = agent._execute_tool(
        "write_file", {"file_path": "b.txt", "content": "copied"}
    )

    assert rerouted["status"] == "denied"
    assert "was refused" in rerouted["error"]
    assert agent.ran == []


_PY_DELETES = [
    "import shutil\nshutil.rmtree('victim')",
    "import os\nos.remove('victim')",
    "import os\nos.unlink('victim')",
    "import os\nos.rmdir('victim')",
    "from pathlib import Path\nPath('victim').unlink()",
    "import shutil as s\ns.rmtree('victim')",
]


def _python_c(code: str) -> str:
    return 'python -c "' + code.replace("\n", "; ") + '"'


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf victim",
        "rm victim",
        "del victim",
        "erase victim",
        "rd /s /q victim",
        "rmdir victim",
        "ri victim",
        "Remove-Item -Recurse victim",
        'powershell -Command "ri victim"',
        "cmd /c del victim",
        # A copy that also deletes is judged by its worst step.
        "Copy-Item a.txt b.txt; Remove-Item victim",
        "python -c \"import os; os.remove('victim/f.txt')\"",
        *[_python_c(code) for code in _PY_DELETES],
    ],
)
@pytest.mark.parametrize("reroute", _PY_DELETES)
def test_a_delete_refused_unattended_stays_off_python(_isolated, command, reroute):
    (_isolated / "victim").mkdir()
    agent = _unattended(_isolated)

    refused = _shell(agent, command)
    rerouted = agent._execute_tool("run_python", {"code": reroute})

    assert refused["executed"] is False
    assert UNATTENDED_REFUSAL not in refused
    assert rerouted["status"] == "denied"
    assert agent.ran == []


def test_cloud_metadata_refused_unattended_stays_off_python(_isolated):
    agent = _unattended(_isolated)

    refused = _shell(agent, "curl http://169.254.169.254/latest/meta-data/")
    rerouted = agent._execute_tool(
        "run_python",
        {"code": "import urllib.request\nurllib.request.urlopen('http://0xa9fea9fe/')"},
    )

    assert UNATTENDED_REFUSAL not in refused
    assert rerouted["status"] == "denied"
    assert agent.ran == []


def test_a_timed_out_prompt_still_records_nothing(_isolated):
    class _Expired(_NoSayer):
        def confirmation_timed_out(self, tool_name):
            return tool_name == "run_shell_command"

    agent = _agent(_Expired(), _isolated)

    _shell(agent, "Copy-Item a.txt b.txt")

    assert not agent._denied_effects


# ---------------------------------------------------------------------------
# The refusal names the route to take instead
# ---------------------------------------------------------------------------

_NO_PROMPT = ("dir", "findstr") if os.name == "nt" else ("ls", "grep")


def test_the_refusal_names_the_auto_approved_routes(_isolated):
    agent = _unattended(_isolated)

    error = _shell(agent, "Get-Process")["error"]

    for route in ("read_file", "browse_directory", "run_python"):
        assert route in error
    for command in _NO_PROMPT:
        assert f"`{command}`" in error
        assert command in ALLOWED_COMMANDS
    # One sentence added, not a lecture.
    added = error.split("turn on full access.", 1)[1].strip()
    assert added.startswith("Use ") and added.count(".") == 1


def test_the_refusal_never_names_a_tool_the_agent_lacks(_isolated):
    agent = _unattended(_isolated, file_tools=False)

    error = _shell(agent, "type notes.txt")["error"]

    for missing in ("read_file", "browse_directory", "run_python"):
        assert missing not in error
    assert f"`{_NO_PROMPT[0]}`" in error


def test_a_refused_delete_is_not_given_another_route(_isolated):
    agent = _unattended(_isolated)

    error = _shell(agent, "rm -rf victim")["error"]

    assert "run_python" not in error and "read_file" not in error


def test_the_no_prompt_examples_really_run_unattended(_isolated):
    agent = _unattended(_isolated)

    for command in _NO_PROMPT:
        assert (
            agent.policy_refusal_for_call(
                "run_shell_command", {"command": f"{command} x"}
            )
            is None
        ), command


# ---------------------------------------------------------------------------
# "GAIA's web tools refuse to fetch" only when they would
# ---------------------------------------------------------------------------

WEB_CLAUSE = "web tools refuse to fetch"


@pytest.mark.parametrize(
    "allowed, host, refused",
    [
        ("", "127.0.0.1", True),
        ("127.0.0.1", "127.0.0.1", False),
        ("localhost", "localhost", False),
        ("localhost", "LOCALHOST", False),
        ("2130706433", "2130706433", False),
        ("::1", "[::1]", False),
        # Allowlisting one spelling does not allow another.
        ("localhost", "127.0.0.1", True),
        ("127.0.0.1", "127.1", True),
        # The allowlist only ever opens loopback.
        ("10.0.0.5", "10.0.0.5", True),
        ("169.254.169.254", "169.254.169.254", True),
        ("printer.local", "printer.local", True),
        (" 127.0.0.1 , other", "127.0.0.1", False),
        ("example.com", "example.com", False),
    ],
)
def test_web_tools_refuse_follows_the_fetchers_allowlist(
    monkeypatch, allowed, host, refused
):
    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", allowed)

    assert web_tools_refuse(host) is refused


def test_the_card_drops_the_web_clause_for_an_allowlisted_host(monkeypatch):
    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")

    note = private_fetch_note([("127.0.0.1", "this PC")])

    assert note == "Reaches 127.0.0.1 on this PC."
    assert WEB_CLAUSE not in note


def test_the_card_names_the_refused_host_when_one_is(monkeypatch):
    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")

    note = private_fetch_note(
        [("127.0.0.1", "this PC"), ("10.0.0.5", "your local network")]
    )

    assert "10.0.0.5" in note and WEB_CLAUSE in note


def test_an_allowlisted_curl_still_asks_but_makes_no_web_claim(_isolated, monkeypatch):
    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")
    agent = _unattended(_isolated)

    allowed = _shell(agent, f"curl {FEED}")
    monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "")
    blocked = _shell(agent, f"curl {FEED}")

    assert allowed["executed"] is False and blocked["executed"] is False
    assert WEB_CLAUSE not in allowed["error"]
    assert WEB_CLAUSE in blocked["error"]
