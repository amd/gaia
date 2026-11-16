# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An edit tool refuses an edit it already applied to the same file this turn.

Gemma 4 E4B called ``edit_file`` twice in one turn with old_content "milk" and
new_content "oat milk". The second call matched the "milk" inside the "oat
milk" the first had written, and the user's list read "- oat oat milk".

No LLM or external service required.
"""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.base.verification import check_was_executed
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.security import PathValidator

TODO = "- eggs\n- milk\n- bread\n"
MILK = {"old_content": "milk", "new_content": "oat milk"}


class _Host(FileIOToolsMixin, FileSearchToolsMixin):
    """One agent's worth of file tools, without the agent around them."""

    _turn_seq = 1


def _capture(host, registrar):
    saved = dict(_TOOL_REGISTRY)
    try:
        getattr(host, registrar)()
        return {
            name: entry["function"]
            for name, entry in _TOOL_REGISTRY.items()
            if saved.get(name) is not entry
        }
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def host(tmp_path):
    validator = PathValidator()
    validator.allowed_paths.add(tmp_path.resolve())
    built = _Host()
    built.path_validator = validator
    built._path_validator = validator
    built.tools = {
        "file_io": _capture(built, "register_file_io_tools"),
        "file_search": _capture(built, "register_file_search_tools"),
    }
    return built


@pytest.fixture
def todo(tmp_path):
    path = tmp_path / "todo.txt"
    path.write_text(TODO, encoding="utf-8")
    return path


@pytest.fixture(params=["file_io", "file_search"])
def edit(request, host):
    """``edit_file`` from one mixin, called on a file it has read."""
    tools = host.tools[request.param]

    def call(path, **kwargs):
        return tools["edit_file"](file_path=str(path), **kwargs)

    call.read = lambda path: tools["read_file"](file_path=str(path))
    return call


class TestTheSameEditTwiceInOneTurn:
    def test_the_second_milk_edit_is_refused_and_the_file_keeps_one_oat(
        self, edit, todo
    ):
        edit.read(todo)
        assert edit(todo, **MILK)["status"] == "success"

        again = edit(todo, **MILK)

        assert again["status"] == "error"
        assert todo.read_text(encoding="utf-8") == "- eggs\n- oat milk\n- bread\n"

    def test_the_refusal_says_what_a_repeat_would_do_and_what_to_do(self, edit, todo):
        edit.read(todo)
        edit(todo, **MILK)

        again = edit(todo, **MILK)

        assert again["error_type"] == "already_applied"
        assert check_was_executed(again) is False
        assert "already applied" in again["error"]
        assert "todo.txt" in again["error"]
        assert "'oat milk' to 'oat oat milk'" in again["error"]
        assert "Nothing was written" in again["error"]
        assert "read_file" in again["error"]

    def test_edit_python_file_refuses_a_repeat_too(self, host, tmp_path):
        path = tmp_path / "shop.py"
        path.write_text('ITEM = "milk"\n', encoding="utf-8")
        tools = host.tools["file_io"]
        tools["read_file"](file_path=str(path))
        tools["edit_python_file"](file_path=str(path), **MILK, backup=False)

        again = tools["edit_python_file"](file_path=str(path), **MILK, backup=False)

        assert again["status"] == "error"
        assert path.read_text(encoding="utf-8") == 'ITEM = "oat milk"\n'

    def test_a_dry_run_does_not_count_as_applied(self, host, tmp_path):
        path = tmp_path / "shop.py"
        path.write_text('ITEM = "milk"\n', encoding="utf-8")
        tools = host.tools["file_io"]
        tools["read_file"](file_path=str(path))
        tools["edit_python_file"](file_path=str(path), **MILK, dry_run=True)

        real = tools["edit_python_file"](file_path=str(path), **MILK, backup=False)

        assert real["status"] == "success", real


class TestLegitimateRepeatsStillRun:
    def test_a_different_edit_to_the_same_file_runs(self, edit, todo):
        edit.read(todo)
        edit(todo, **MILK)

        second = edit(todo, old_content="bread", new_content="rye bread")

        assert second["status"] == "success", second
        assert todo.read_text(encoding="utf-8") == "- eggs\n- oat milk\n- rye bread\n"

    def test_the_same_edit_to_another_file_runs(self, edit, todo, tmp_path):
        other = tmp_path / "other.txt"
        other.write_text(TODO, encoding="utf-8")
        edit.read(todo)
        edit.read(other)
        edit(todo, **MILK)

        second = edit(other, **MILK)

        assert second["status"] == "success", second
        assert other.read_text(encoding="utf-8") == "- eggs\n- oat milk\n- bread\n"

    def test_the_same_edit_in_a_later_turn_runs(self, edit, host, todo):
        edit.read(todo)
        edit(todo, **MILK)
        host._turn_seq += 1

        later = edit(todo, **MILK)

        assert later["status"] == "success", later
        assert todo.read_text(encoding="utf-8") == "- eggs\n- oat oat milk\n- bread\n"

    def test_a_refused_edit_does_not_count_as_applied(self, edit, todo):
        edit.read(todo)
        assert edit(todo, old_content="cheese", new_content="brie")["status"] == "error"
        todo.write_text("- cheese\n", encoding="utf-8")
        edit.read(todo)

        result = edit(todo, old_content="cheese", new_content="brie")

        assert result["status"] == "success", result


# ---------------------------------------------------------------------------


class _RecordingConsole(AgentConsole):
    def __init__(self):
        super().__init__()
        self.asked = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.asked.append(tool_name)
        return True


class _FileAgent(Agent, FileIOToolsMixin):
    def _get_system_prompt(self) -> str:
        return "files"

    def _register_tools(self) -> None:
        self.register_file_io_tools()
        self._snapshot_tools()

    def _create_console(self):
        return _RecordingConsole()


@pytest.fixture
def agent(tmp_path):
    saved = dict(_TOOL_REGISTRY)
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            built = _FileAgent(silent_mode=True, skip_lemonade=True)
        validator = PathValidator()
        validator.allowed_paths.add(tmp_path.resolve())
        built.path_validator = validator
        built.console = _RecordingConsole()
        yield built
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


class TestRefusedBeforeConfirmation:
    def test_a_repeated_edit_never_reaches_the_prompt(self, agent, todo):
        args = {"file_path": str(todo), **MILK}
        agent._execute_tool("read_file", {"file_path": str(todo)})
        assert agent._execute_tool("edit_file", args)["status"] == "success"
        agent.console.asked.clear()

        again = agent._execute_tool("edit_file", args)

        assert again["status"] == "error"
        assert agent.console.asked == []
        assert todo.read_text(encoding="utf-8") == "- eggs\n- oat milk\n- bread\n"
