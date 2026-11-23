# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The prompt that asks to run a Python file shows the file's content.

A path says nothing about what a script does, and the agent writes scripts into
its scratch folder without asking. So the prompt carries the script, read when
the prompt is built, and says plainly when it cannot.
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.call_risk import SCRIPT_ARG
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.script_preview import (
    MISSING,
    NOT_TEXT,
    SCRIPT_MAX_BYTES,
    SCRIPT_SHOWN_CHARS,
    SCRIPT_SHOWN_LINES,
    TOO_LARGE,
    changed_script_refusal,
    read_script,
    script_prompt_args,
)
from gaia.agents.base.tools import tool


def test_a_short_script_is_shown_whole(tmp_path):
    script = tmp_path / "build.py"
    script.write_bytes(b"import os\r\nprint(os.getcwd())\r\n")

    preview = read_script(str(script))

    assert preview.shown == {
        "text": "import os\nprint(os.getcwd())",
        "hidden_lines": 0,
    }
    assert preview.digest == hashlib.sha256(script.read_bytes()).hexdigest()


def test_a_long_script_is_capped_and_counts_what_is_left(tmp_path):
    script = tmp_path / "long.py"
    total = SCRIPT_SHOWN_LINES + 37
    script.write_text("\n".join(f"x{i} = {i}" for i in range(total)), encoding="utf-8")

    shown = read_script(str(script)).shown

    assert shown["text"].splitlines()[0] == "x0 = 0"
    assert len(shown["text"].splitlines()) == SCRIPT_SHOWN_LINES
    assert shown["hidden_lines"] == 37


def test_wide_lines_are_capped_by_size_too(tmp_path):
    script = tmp_path / "wide.py"
    line = "y = '" + "a" * 999 + "'"
    script.write_text("\n".join([line] * 100), encoding="utf-8")

    shown = read_script(str(script)).shown

    kept = len(shown["text"].splitlines())
    assert len(shown["text"]) <= SCRIPT_SHOWN_CHARS
    assert 0 < kept < 100 and shown["hidden_lines"] == 100 - kept


@pytest.mark.parametrize(
    "content, reason",
    [
        (b"MZ\x90\x00\x03\x00\x00\x00", NOT_TEXT),
        (b"print('caf\xe9')\n", NOT_TEXT),
        (b"x = 1\n" * (SCRIPT_MAX_BYTES // 6 + 1), TOO_LARGE),
        (b"x = '" + b"a" * (SCRIPT_SHOWN_CHARS + 1) + b"'\n", TOO_LARGE),
    ],
    ids=["binary", "not utf-8", "over the byte limit", "one huge line"],
)
def test_a_file_that_cannot_be_shown_says_why(tmp_path, content, reason):
    script = tmp_path / "odd.py"
    script.write_bytes(content)

    preview = read_script(str(script))

    assert preview.shown == {"unreadable": reason}
    assert preview.digest == hashlib.sha256(content).hexdigest()


def test_a_missing_file_says_so(tmp_path):
    preview = read_script(str(tmp_path / "gone.py"))

    assert preview.shown == {"unreadable": MISSING}
    assert preview.digest == ""


def test_an_empty_script_is_shown_as_empty(tmp_path):
    script = tmp_path / "empty.py"
    script.write_bytes(b"")

    assert read_script(str(script)).shown == {"text": "", "hidden_lines": 0}


class _Host:
    working_directory = None


def test_the_run_is_refused_when_the_file_changed_after_its_prompt(tmp_path):
    script = tmp_path / "job.py"
    script.write_text("print('shown')\n", encoding="utf-8")
    host = _Host()

    shown = script_prompt_args(host, "execute_python_file", {"file_path": str(script)})
    assert shown[SCRIPT_ARG]["text"] == "print('shown')"
    script.write_text("print('swapped')\n", encoding="utf-8")
    refusal = changed_script_refusal(host, str(script), script.read_bytes())

    assert refusal["status"] == "error" and refusal["executed"] is False
    assert "changed after the approval prompt" in refusal["error"]
    # One prompt, one run: the next call builds its own prompt.
    assert changed_script_refusal(host, str(script), script.read_bytes()) is None


def test_an_unchanged_file_runs(tmp_path):
    script = tmp_path / "job.py"
    script.write_text("print('shown')\n", encoding="utf-8")
    host = _Host()

    script_prompt_args(host, "execute_python_file", {"file_path": str(script)})

    assert changed_script_refusal(host, str(script), script.read_bytes()) is None


def test_a_file_that_appears_after_a_prompt_said_missing_is_refused(tmp_path):
    script = tmp_path / "late.py"
    host = _Host()

    shown = script_prompt_args(host, "execute_python_file", {"file_path": str(script)})
    assert shown[SCRIPT_ARG] == {"unreadable": MISSING}
    script.write_text("print('late')\n", encoding="utf-8")

    assert changed_script_refusal(host, str(script), script.read_bytes()) is not None


def test_other_tools_get_no_script(tmp_path):
    args = {"file_path": str(tmp_path / "a.py"), "content": "x"}

    assert script_prompt_args(_Host(), "write_file", args) is args


def test_a_path_that_cannot_be_resolved_says_the_script_is_unreadable():
    shown = script_prompt_args(
        _Host(), "execute_python_file", {"file_path": "bad\x00name.py"}
    )

    assert shown[SCRIPT_ARG] == {"unreadable": "unreadable"}


class _Recorder(AgentConsole):
    """Approves every prompt and keeps what it was shown."""

    def __init__(self):
        super().__init__()
        self.prompts = []
        self.before_answer = None

    def confirm_tool_execution(self, tool_name, tool_args):
        self.prompts.append((tool_name, tool_args))
        if self.before_answer:
            self.before_answer()
        return True


class _Runner(Agent):
    def __init__(self, **kwargs):
        self.ran = []
        self._recorder = _Recorder()
        super().__init__(**kwargs)

    def _get_system_prompt(self):
        return "runner"

    def _create_console(self):
        return self._recorder

    def _register_tools(self):
        agent = self

        @tool
        def execute_python_file(file_path: str) -> dict:
            """Probe runner."""
            agent.ran.append(file_path)
            return {"status": "success", "return_code": 0}


def _runner():
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _Runner(silent_mode=True, skip_lemonade=True)


def test_the_agent_puts_the_script_on_the_prompt(tmp_path):
    script = tmp_path / "build_recon.py"
    script.write_text("import shutil\nshutil.rmtree('out')\n", encoding="utf-8")
    agent = _runner()

    result = agent._execute_tool("execute_python_file", {"file_path": str(script)})

    [(name, shown)] = agent.console.prompts
    assert name == "execute_python_file"
    assert shown[SCRIPT_ARG] == {
        "text": "import shutil\nshutil.rmtree('out')",
        "hidden_lines": 0,
    }
    assert result["status"] == "success"
    # The tool still runs with the model's own arguments.
    assert agent.ran == [str(script)]


def test_the_model_cannot_supply_the_script_shown(tmp_path):
    script = tmp_path / "real.py"
    script.write_text("import shutil\n", encoding="utf-8")
    agent = _runner()

    result = agent._execute_tool(
        "execute_python_file",
        {"file_path": str(script), SCRIPT_ARG: {"text": "print('harmless')"}},
    )

    assert result["status"] == "error" and SCRIPT_ARG in result["error"]
    assert agent.console.prompts == [] and agent.ran == []
