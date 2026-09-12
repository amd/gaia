# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A completed write must be reported as completed, whatever the display does.

``edit_file`` and ``write_file`` put the bytes on disk and *then* ask the
console to show what changed. When that display step raised — the Agent-UI /
TUI handler had no ``print_diff`` at all — the exception fell into the tool's
catch-all and the call returned ``{"status": "error"}`` for a file that had
already been changed. The agent then told the user the file was untouched
(#3676).

Every test here runs the real tool against a real file on disk and asserts the
two things that must agree: what the file contains, and what the tool said.

No LLM or external service required.
"""

import importlib

import pytest

from gaia.agents.base.console import OutputHandler
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_edit import FileStateTracker


@pytest.fixture(autouse=True)
def clean_tracker():
    """The tracker is process-wide; no test may inherit another's ledger."""
    FileStateTracker.instance().clear()
    yield
    FileStateTracker.instance().clear()


@pytest.fixture
def file_tools():
    """``(tool_name) -> callable`` for the file-I/O mixin, console attachable."""
    module = importlib.import_module("gaia.agents.tools.file_io_tools")
    mixin = module.FileIOToolsMixin()

    saved = dict(_TOOL_REGISTRY)
    try:
        mixin.register_file_io_tools()

        def get(name):
            entry = _TOOL_REGISTRY.get(name)
            assert entry is not None, f"{name} was not registered"
            return entry["function"]

        get.mixin = mixin
        yield get
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _ui_handler():
    """The Agent-UI / TUI handler — the one #3676 was reported against."""
    from gaia.ui.sse_handler import SSEOutputHandler

    return SSEOutputHandler()


def _api_handler():
    from gaia.api.sse_handler import SSEOutputHandler

    return SSEOutputHandler()


class RaisingConsole(OutputHandler):
    """A handler whose rendering is broken in every direction."""

    def print_processing_start(self, query, max_steps, model_id=None): ...

    def print_step_header(self, step_num, step_limit): ...

    def print_state_info(self, state_message): ...

    def print_thought(self, thought): ...

    def print_goal(self, goal): ...

    def print_plan(self, plan, current_step=None): ...

    def print_tool_usage(self, tool_name): ...

    def print_tool_complete(self): ...

    def pretty_print_json(self, data, title=None): ...

    def print_error(self, error_message, recoverable=False): ...

    def print_warning(self, warning_message): ...

    def start_progress(self, message): ...

    def stop_progress(self): ...

    def print_final_answer(self, answer, total_tokens=None, ttft_seconds=None): ...

    def print_repeated_tool_warning(self): ...

    def print_completion(self, steps_taken, steps_limit): ...

    def print_step_paused(self, description): ...

    def print_command_executing(self, command): ...

    def print_agent_selected(self, agent_name, language, project_type): ...

    def print_info(self, message):
        raise RuntimeError("display is broken")

    def print_diff(self, diff, filename):
        raise RuntimeError("display is broken")

    def print_prompt(self, prompt, title="Prompt"):
        raise RuntimeError("display is broken")


CONSOLES = [
    ("ui_sse", _ui_handler),
    ("api_sse", _api_handler),
    ("raising", RaisingConsole),
]
CONSOLE_IDS = [name for name, _ in CONSOLES]


@pytest.fixture(params=[factory for _, factory in CONSOLES], ids=CONSOLE_IDS)
def console(request):
    return request.param()


# ============================================================================
# 1. THE CONTRACT ITSELF
# ============================================================================


class TestHandlerContract:
    """No handler the write tools can be given may be missing a display hook."""

    @pytest.mark.parametrize("factory", [f for _, f in CONSOLES], ids=CONSOLE_IDS)
    @pytest.mark.parametrize("method", ["print_diff", "print_info", "print_prompt"])
    def test_handler_has_the_method_the_write_tools_call(self, factory, method):
        assert callable(getattr(factory(), method, None))

    def test_base_handler_declares_print_diff(self):
        """Declared on the ABC so a future handler inherits it for free."""
        assert callable(getattr(OutputHandler, "print_diff", None))


# ============================================================================
# 2. edit_file — DISK AND RESULT MUST AGREE
# ============================================================================


class TestEditFileReportsTheWrite:
    def test_edit_succeeds_with_a_real_handler_attached(
        self, file_tools, console, tmp_path
    ):
        path = tmp_path / "note.txt"
        path.write_text("alpha\n", encoding="utf-8")
        file_tools.mixin.console = console

        result = file_tools("edit_file")(str(path), "alpha", "beta")

        assert path.read_text(encoding="utf-8") == "beta\n"
        assert result["status"] == "success", result

    def test_a_broken_display_is_reported_without_denying_the_write(
        self, file_tools, tmp_path
    ):
        path = tmp_path / "note.txt"
        path.write_text("alpha\n", encoding="utf-8")
        file_tools.mixin.console = RaisingConsole()

        result = file_tools("edit_file")(str(path), "alpha", "beta")

        assert path.read_text(encoding="utf-8") == "beta\n"
        assert result["status"] == "success"
        # The failure is surfaced, not swallowed — and says the write happened.
        assert "display is broken" in result["display_error"]
        assert "written" in result["display_error"]

    def test_a_clean_edit_carries_no_display_error(self, file_tools, tmp_path):
        path = tmp_path / "note.txt"
        path.write_text("alpha\n", encoding="utf-8")
        file_tools.mixin.console = _ui_handler()

        result = file_tools("edit_file")(str(path), "alpha", "beta")

        assert "display_error" not in result


# ============================================================================
# 3. write_file — SAME RULE
# ============================================================================


class TestWriteFileReportsTheWrite:
    def test_write_succeeds_with_a_real_handler_attached(
        self, file_tools, console, tmp_path
    ):
        path = tmp_path / "new.txt"
        file_tools.mixin.console = console

        result = file_tools("write_file")(str(path), "hello\n")

        assert path.read_text(encoding="utf-8") == "hello\n"
        assert result["status"] == "success", result

    def test_a_broken_display_is_reported_without_denying_the_write(
        self, file_tools, tmp_path
    ):
        path = tmp_path / "new.txt"
        file_tools.mixin.console = RaisingConsole()

        result = file_tools("write_file")(str(path), "hello\n")

        assert path.read_text(encoding="utf-8") == "hello\n"
        assert result["status"] == "success"
        assert "display is broken" in result["display_error"]
