# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The session panel's Environment is enforced by the agent, not just shown (#5276).

A folder the user removes is refused by the real file tools on the next call —
under a wider grant, with an approving console, and under full access — and
internet off stops web, browser, connector and online shell calls before
they can run or ask.
"""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.shell_session import ShellSession
from gaia.security import PathValidator


class _Console(SilentConsole):
    """Approves every prompt, as a user in full access would."""

    def __init__(self, full_access=False):
        super().__init__()
        self.full_access = full_access
        self.asked = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.asked.append(tool_name)
        return True


class _Agent(Agent, FileIOToolsMixin):
    def __init__(self, scope, console):
        self.path_validator = PathValidator(allowed_paths=[str(scope)])
        self.ran = []
        with patch("gaia.agents.base.agent.AgentSDK"):
            super().__init__(
                silent_mode=True, skip_lemonade=True, output_handler=console
            )

    def _get_system_prompt(self) -> str:
        return "env"

    def _register_tools(self) -> None:
        self.register_file_io_tools()
        ran = self.ran

        @tool
        def search_web(query: str) -> dict:
            """Search the web."""
            ran.append("search_web")
            return {"status": "success"}

        @tool
        def run_shell_command(command: str) -> dict:
            """Run a shell command."""
            ran.append(command)
            return {"status": "success"}

        @tool
        def browser_open(url: str) -> dict:
            """Open a page in the browser."""
            ran.append(url)
            return {"status": "success"}


@pytest.fixture(autouse=True)
def _keep_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def layout(tmp_path, monkeypatch):
    # tmp_path is under the system temp dir, which is never askable.
    monkeypatch.setattr(
        "gaia.security._system_temp_roots", lambda: {str(tmp_path / "systemp")}
    )
    scope = tmp_path / "scope"
    project = scope / "project"
    project.mkdir(parents=True)
    (project / "notes.txt").write_text("secret plan", encoding="utf-8")
    (scope / "other.txt").write_text("fine", encoding="utf-8")
    return {"scope": scope, "project": project, "file": project / "notes.txt"}


def _call(agent, name, **args):
    return agent._execute_tool(name, args)


class TestRemovedFolder:
    @pytest.mark.parametrize("full_access", [False, True])
    def test_every_file_tool_refuses_it(self, layout, full_access):
        console = _Console(full_access=full_access)
        agent = _Agent(layout["scope"], console)
        target = layout["file"]
        assert _call(agent, "read_file", file_path=str(target))["status"] == "success"

        agent.path_validator.withhold_path(str(layout["project"]))

        read = _call(agent, "read_file", file_path=str(target))
        write = _call(agent, "write_file", file_path=str(target), content="x")
        edit = _call(
            agent,
            "edit_file",
            file_path=str(target),
            old_content="secret",
            new_content="x",
        )
        create = _call(
            agent,
            "write_file",
            file_path=str(layout["project"] / "new.txt"),
            content="x",
        )
        for result in (read, write, edit, create):
            assert result["status"] == "error", result
            assert "Access denied" in result["error"]
        # Tools that use the validator's message say why.
        assert "removed from this chat" in write["error"]
        assert target.read_text(encoding="utf-8") == "secret plan"
        assert not (layout["project"] / "new.txt").exists()
        # Refused outright: never offered as an approval.
        assert "__path_access__" not in " ".join(console.asked)
        # The rest of the wider grant still works.
        other = _call(agent, "read_file", file_path=str(layout["scope"] / "other.txt"))
        assert other["status"] == "success"

    def test_dot_dot_and_case_cannot_slip_past(self, layout):
        agent = _Agent(layout["scope"], _Console())
        agent.path_validator.withhold_path(str(layout["project"]))
        sneaky = layout["scope"] / "x" / ".." / "project" / "notes.txt"
        assert _call(agent, "read_file", file_path=str(sneaky))["status"] == "error"

    def test_adding_it_back_restores_access(self, layout):
        agent = _Agent(layout["scope"], _Console())
        validator = agent.path_validator
        validator.withhold_path(str(layout["project"]))
        validator.release_path(str(layout["project"]))
        result = _call(agent, "read_file", file_path=str(layout["file"]))
        assert result["status"] == "success"

    def test_removing_an_exact_grant_drops_it(self, tmp_path):
        folder = tmp_path / "only"
        folder.mkdir()
        validator = PathValidator(allowed_paths=[str(folder)])
        validator.withhold_path(str(folder))
        assert validator.allowed_paths == set()
        assert not validator.is_path_allowed(str(folder / "a.txt"), prompt_user=False)


class TestInternetOff:
    def _agent(self, layout):
        agent = _Agent(layout["scope"], _Console(full_access=True))
        agent.internet_off = True
        return agent

    def test_web_and_browser_tools_are_refused_before_running(self, layout):
        agent = self._agent(layout)
        for name, args in (
            ("search_web", {"query": "news"}),
            ("browser_open", {"url": "https://a.com"}),
        ):
            result = _call(agent, name, **args)
            assert result["status"] == "error"
            assert result["error_type"] == "internet_off"
            assert "Internet is off for this chat" in result["error"]
        assert agent.ran == []

    @pytest.mark.parametrize(
        "command",
        [
            "curl https://example.com",
            "git push origin main",
            "pip install requests",
            "cd x && npm install left-pad",
            "gh pr create --fill",
            "git -C repo pull",
            "npx create-react-app app",
            "uvx ruff check",
            "docker pull nginx",
            "cargo install ripgrep",
            "curl http://localhost:8000/ && curl example.com",
            "curl http://127.0.0.1/ ; git push origin main",
            "curl http://127.0.0.1.attacker.example/",
        ],
    )
    def test_online_shell_commands_are_refused(self, layout, command):
        agent = self._agent(layout)
        result = _call(agent, "run_shell_command", command=command)
        assert result["status"] == "error" and result["error_type"] == "internet_off"
        assert agent.ran == []

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git -C repo status",
            "python -m pytest",
            "uv run pytest",
            "git commit -m 'pull and push later'",
            "curl http://localhost:4316/api/health",
        ],
    )
    def test_local_commands_still_run(self, layout, command):
        agent = self._agent(layout)
        assert _call(agent, "run_shell_command", command=command)["status"] == "success"
        assert agent.ran == [command]

    def test_a_page_on_this_pc_stays_reachable(self, layout):
        agent = self._agent(layout)
        result = _call(agent, "browser_open", url="http://localhost:5316/")
        assert result["status"] == "success"
        assert agent.ran == ["http://localhost:5316/"]

    def test_a_look_alike_of_this_pc_is_refused(self, layout):
        agent = self._agent(layout)
        result = _call(agent, "browser_open", url="http://127.0.0.1.attacker.example/")
        assert result["error_type"] == "internet_off"
        assert agent.ran == []

    def test_connector_tools_are_refused(self, layout):
        agent = self._agent(layout)
        ran = []

        def fake(**_kw):
            ran.append(1)
            return {"status": "success"}

        agent._tools_registry["mcp_tavily_search"] = {
            "name": "mcp_tavily_search",
            "function": fake,
            "parameters": {},
            "_mcp_server": "tavily",
        }
        result = _call(agent, "mcp_tavily_search", query="x")
        assert result["error_type"] == "internet_off" and "tavily" in result["error"]
        assert ran == []

    def test_turning_it_back_on_takes_effect_on_the_next_call(self, layout):
        agent = self._agent(layout)
        assert _call(agent, "search_web", query="a")["status"] == "error"
        agent.internet_off = False
        assert _call(agent, "search_web", query="a")["status"] == "success"

    def test_file_tools_are_unaffected(self, layout):
        agent = self._agent(layout)
        result = _call(agent, "read_file", file_path=str(layout["file"]))
        assert result["status"] == "success"


class TestRemovedFolderInTheShell:
    """The shell starts in the server's folder; removing it must stop bare commands there."""

    @pytest.fixture
    def shell(self, layout, monkeypatch):
        import importlib

        module = importlib.import_module("gaia.agents.tools.shell_tools")
        mixin = module.ShellToolsMixin()
        mixin.path_validator = PathValidator(allowed_paths=[str(layout["scope"])])
        mixin.register_shell_tools()
        # A new session starts in the server's folder, here the removed one.
        mixin._shell_session = ShellSession(start_cwd=str(layout["project"]))
        return mixin, _TOOL_REGISTRY["run_shell_command"]["function"]

    @pytest.mark.parametrize(
        "command", ["dir", "type notes.txt", "ls", "cat notes.txt"]
    )
    def test_bare_commands_in_a_removed_cwd_are_refused(self, shell, layout, command):
        mixin, run = shell
        mixin.path_validator.withhold_path(str(layout["project"]))
        result = run(command)
        assert result["status"] == "error", result
        assert "removed from this chat" in result["error"]
        assert "secret plan" not in str(result)

    def test_an_explicit_working_directory_in_a_removed_folder_is_refused(
        self, shell, layout
    ):
        mixin, run = shell
        mixin.path_validator.withhold_path(str(layout["project"]))
        mixin._shell_session = ShellSession(start_cwd=str(layout["scope"]))
        result = run("dir", working_directory=str(layout["project"]))
        assert result["status"] == "error"
        assert "removed from this chat" in result["error"]

    def test_a_removed_folder_is_never_offered_for_approval(self, layout):
        validator = PathValidator(allowed_paths=[str(layout["scope"])])
        validator.withhold_path(str(layout["project"]))
        reason = validator._unaskable_reason(layout["project"].resolve())
        assert "removed from this chat" in reason
