# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A denied tool call is a decision about its effect, not its tool name (#4447).

Seen live: ``run_shell_command("pytest")`` timed out unanswered, and the agent
wrote ``run_tests.py`` and ran it with ``execute_python_file`` instead. These
tests pin that every spelling of the same effect is recognised, that nothing
unrelated is swept up with it, and that the loop refuses the reroute or puts
it to the user rather than running it.
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.denied_effects import (
    COMMAND,
    NETWORK,
    PATH,
    SCRIPT,
    TEST_SUITE,
    DeniedEffects,
    Effect,
    effects_of_call,
    effects_of_shell_command,
    render_call,
)
from gaia.agents.base.tools import tool


def _shell(command: str) -> dict:
    return {"command": command}


def _overlaps(denied_tool, denied_args, tool_name, tool_args, cwd) -> bool:
    ledger = DeniedEffects(str(cwd))
    ledger.record(denied_tool, denied_args, "Tool was denied by the user.")
    return ledger.conflict(tool_name, tool_args) is not None


# ---------------------------------------------------------------------------
# Effect extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "py.test tests",
        "python -m pytest",
        "python3.12 -m pytest -x",
        "py -3 -m pytest",
        "uv run pytest",
        "poetry run pytest",
        "bash -c 'pytest -x'",
        'powershell -Command "python -m pytest"',
        "cd repo && python -m pytest tests 2>&1 | tail -5",
        "GAIA_X=1 pytest",
        "tox -e py312",
        "python -m unittest discover",
    ],
)
def test_every_spelling_of_running_the_suite_is_one_effect(command, tmp_path):
    assert Effect(COMMAND, TEST_SUITE) in effects_of_shell_command(
        command, str(tmp_path)
    )


def test_python_snippets_that_run_the_suite_are_recognised(tmp_path):
    snippets = [
        "import subprocess, sys\nsubprocess.run([sys.executable, '-m', 'pytest'])",
        "import os\nos.system('pytest -q')",
        "from subprocess import run\nrun('python -m pytest', shell=True)",
        "import pytest\npytest.main(['-q'])",
        "import unittest\nunittest.main(module=None)",
        "import runpy\nrunpy.run_module('pytest', run_name='__main__')",
    ]
    for code in snippets:
        effects = effects_of_call("run_python", {"code": code}, str(tmp_path))
        assert Effect(COMMAND, TEST_SUITE) in effects, code


def test_a_script_is_read_for_what_it_runs(tmp_path):
    script = tmp_path / "run_tests.py"
    script.write_text("import pytest\nraise SystemExit(pytest.main())\n")

    effects = effects_of_call(
        "execute_python_file", {"file_path": "run_tests.py"}, str(tmp_path)
    )

    assert Effect(COMMAND, TEST_SUITE) in effects
    assert any(e.kind == SCRIPT for e in effects)


def test_running_or_importing_a_test_module_runs_the_suite(tmp_path):
    for tool_name, args in [
        ("execute_python_file", {"file_path": "test_totals.py"}),
        ("run_shell_command", _shell("python tests/store_test.py")),
        (
            "run_python",
            {"code": "from test_totals import test_adds_tax\ntest_adds_tax()"},
        ),
        ("run_python", {"code": "import tests.test_totals as t\nt.test_adds_tax()"}),
    ]:
        effects = effects_of_call(tool_name, args, str(tmp_path))
        assert Effect(COMMAND, TEST_SUITE) in effects, (tool_name, args)


def test_code_that_merely_imports_unittest_mock_runs_nothing(tmp_path):
    code = "from unittest import mock\nprint(mock.sentinel.x)"
    assert effects_of_call("run_python", {"code": code}, str(tmp_path)) == set()


def test_pipeline_helpers_and_cd_are_never_the_denied_effect(tmp_path):
    effects = effects_of_shell_command(
        "cd repo && pytest -q 2>&1 | tail -5 | grep FAILED", str(tmp_path)
    )
    assert effects == {Effect(COMMAND, TEST_SUITE)}


def test_a_url_inside_file_content_is_not_a_request(tmp_path):
    effects = effects_of_call(
        "write_file",
        {"file_path": "README.md", "content": "See https://github.com/amd/gaia"},
        str(tmp_path),
    )
    assert {e.kind for e in effects} == {PATH}


def test_render_call_shows_the_exact_command():
    assert render_call("run_shell_command", _shell("pytest -q")) == "pytest -q"
    assert (
        render_call("execute_python_file", {"file_path": "t.py", "args": "-v"})
        == "python t.py -v"
    )


def test_render_call_names_a_file_change_in_words():
    temp_file = os.path.join(tempfile.gettempdir(), "scratch", "check_ties.py")
    assert (
        render_call("write_file", {"file_path": temp_file, "content": "x = 1"})
        == "write check_ties.py in a temp folder"
    )
    assert render_call("edit_file", {"file_path": "README.md"}) == "edit README.md"
    project = os.path.join(os.path.expanduser("~"), "proj")
    assert (
        render_call("replace_function", {"file_path": os.path.join(project, "a.py")})
        == f"edit a.py in {project}"
    )


def test_render_call_keeps_the_other_args_a_user_declined():
    """A re-confirmation must still show what the first denial covered (#4460)."""
    rendered = render_call(
        "download_file",
        {"url": "https://evil.example.com/payload", "save_to": "report.pdf"},
    )
    assert rendered.startswith("download to report.pdf")
    assert "https://evil.example.com/payload" in rendered

    rendered = render_call(
        "move_file", {"path": "secrets.env", "destination": "/public/share"}
    )
    assert rendered.startswith("move secrets.env")
    assert "/public/share" in rendered

    rendered = render_call("rename_file", {"target": "a.txt", "destination": "b.txt"})
    assert rendered.startswith("rename a.txt")
    assert "b.txt" in rendered


# ---------------------------------------------------------------------------
# Overlap
# ---------------------------------------------------------------------------


def test_denied_pytest_blocks_the_script_that_runs_it(tmp_path):
    (tmp_path / "run_tests.py").write_text("import pytest\npytest.main()\n")
    assert _overlaps(
        "run_shell_command",
        _shell("pytest"),
        "execute_python_file",
        {"file_path": "run_tests.py"},
        tmp_path,
    )


def test_denied_pytest_blocks_run_python_and_other_runners(tmp_path):
    for tool_name, args in [
        ("run_python", {"code": "import pytest; pytest.main()"}),
        ("run_shell_command", _shell("python -m pytest -k store")),
        ("run_shell_command", _shell("tox")),
        ("wait_for_condition", _shell("pytest -x")),
    ]:
        assert _overlaps(
            "run_shell_command", _shell("pytest -q"), tool_name, args, tmp_path
        ), (tool_name, args)


def test_subcommands_scope_the_denial(tmp_path):
    assert not _overlaps(
        "run_shell_command",
        _shell("git push"),
        "run_shell_command",
        _shell("git status"),
        tmp_path,
    )
    assert _overlaps(
        "run_shell_command",
        _shell("git push"),
        "run_shell_command",
        _shell("git push origin main"),
        tmp_path,
    )
    assert not _overlaps(
        "run_shell_command",
        _shell("gh issue close 3"),
        "run_shell_command",
        _shell("gh issue list"),
        tmp_path,
    )


def test_a_denied_pipeline_does_not_block_its_helpers(tmp_path):
    assert not _overlaps(
        "run_shell_command",
        _shell("cd repo && pytest | tail -5"),
        "run_shell_command",
        _shell("tail -n 20 build.log"),
        tmp_path,
    )


def test_a_denied_host_is_denied_through_every_client(tmp_path):
    denied = _shell("curl -s https://api.github.com/repos/acme/x/issues")
    for tool_name, args in [
        (
            "run_python",
            {
                "code": "import urllib.request\n"
                "urllib.request.urlopen('https://api.github.com/x')"
            },
        ),
        (
            "run_shell_command",
            _shell('powershell -Command "Invoke-RestMethod https://api.github.com/x"'),
        ),
        ("fetch_page", {"url": "https://api.github.com/repos/acme/x"}),
    ]:
        assert _overlaps("run_shell_command", denied, tool_name, args, tmp_path)
    assert not _overlaps(
        "run_shell_command",
        denied,
        "fetch_page",
        {"url": "https://example.com/"},
        tmp_path,
    )


def test_a_denied_file_is_denied_through_the_shell_too(tmp_path):
    denied = {"file_path": "notes/todo.txt", "content": "x"}
    assert _overlaps(
        "write_file",
        denied,
        "run_shell_command",
        _shell("echo x > notes/todo.txt"),
        tmp_path,
    )
    assert _overlaps(
        "write_file", denied, "run_shell_command", _shell("rm -rf notes"), tmp_path
    )
    assert not _overlaps(
        "write_file",
        denied,
        "write_file",
        {"file_path": "notes/other.txt", "content": "y"},
        tmp_path,
    )


def test_record_can_keep_only_some_kinds(tmp_path):
    ledger = DeniedEffects(str(tmp_path))
    ledger.record(
        "run_shell_command",
        _shell("curl https://api.github.com/x"),
        "refused",
        kinds={NETWORK, PATH},
    )
    assert (
        ledger.conflict("run_shell_command", _shell("curl https://example.com")) is None
    )
    assert ledger.conflict("fetch_page", {"url": "https://api.github.com"})


def test_writing_a_test_file_is_not_running_it(tmp_path):
    for tool_name in ("write_python_file", "edit_python_file", "write_file"):
        assert not _overlaps(
            "run_shell_command",
            _shell("pytest -q"),
            tool_name,
            {"file_path": "tests/test_totals.py", "content": "x"},
            tmp_path,
        ), tool_name


def test_flag_values_are_not_subcommands(tmp_path):
    for denied, other in [
        ("kubectl -n prod delete pod web", "kubectl -n prod get pods"),
        ("gh --repo o/r issue close 3", "gh --repo o/r issue list"),
        ("git -C repo push", "git -C repo status"),
    ]:
        assert not _overlaps(
            "run_shell_command",
            _shell(denied),
            "run_shell_command",
            _shell(other),
            tmp_path,
        ), (denied, other)


def test_a_url_in_text_is_not_a_request(tmp_path):
    for denied in [
        _shell('git commit -m "fix https://github.com/amd/gaia/issues/1"'),
        _shell("echo see https://github.com/amd/gaia"),
    ]:
        assert not _overlaps(
            "run_shell_command",
            denied,
            "fetch_page",
            {"url": "https://github.com/amd/gaia"},
            tmp_path,
        ), denied
    code = "# docs at https://github.com/amd/gaia\nprint(1)"
    assert effects_of_call("run_python", {"code": code}, str(tmp_path)) == set()


def test_a_cd_in_the_command_moves_where_the_script_is_found(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "check.py").write_text("import subprocess\nsubprocess.run(['pytest'])\n")
    assert _overlaps(
        "run_shell_command",
        _shell("pytest"),
        "run_shell_command",
        _shell("cd repo && python check.py"),
        tmp_path,
    )


def test_run_tests_script_is_the_suite_even_before_it_is_written(tmp_path):
    assert _overlaps(
        "run_shell_command",
        _shell("pytest"),
        "execute_python_file",
        {"file_path": "run_tests.py"},
        tmp_path,
    )


# ---------------------------------------------------------------------------
# The gate in Agent._execute_tool
# ---------------------------------------------------------------------------


class _DenyShellConsole(AgentConsole):
    """Denies run_shell_command, approves everything else, records prompts."""

    def __init__(self):
        super().__init__()
        self.prompted = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.prompted.append(tool_name)
        return tool_name != "run_shell_command"


class _AskingConsole(_DenyShellConsole):
    """Also has the question channel the Agent UI and TUI provide."""

    def __init__(self, answer):
        super().__init__()
        self.answer = answer
        self.questions = []

    def request_user_input_blocking(self, message, choices=None, **_):
        self.questions.append((message, choices))
        return self.answer


class _Probe(Agent):
    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        agent = self
        agent.ran = []

        @tool
        def run_shell_command(command: str) -> dict:
            """Probe shell."""
            agent.ran.append(("run_shell_command", command))
            return {"status": "success", "return_code": 0}

        @tool
        def execute_python_file(file_path: str) -> dict:
            """Probe python runner."""
            agent.ran.append(("execute_python_file", file_path))
            return {"status": "success", "return_code": 0}

        @tool
        def read_file(file_path: str) -> dict:
            """Probe reader."""
            agent.ran.append(("read_file", file_path))
            return {"status": "success"}

    def _create_console(self):
        return self._console_override


def _agent(console, cwd):
    with patch("gaia.agents.base.agent.AgentSDK"):
        _Probe._console_override = console
        a = _Probe(silent_mode=True, skip_lemonade=True)
    a._denied_effects = DeniedEffects(str(cwd))
    return a


@pytest.fixture
def script(tmp_path):
    path = tmp_path / "run_tests.py"
    path.write_text("import pytest\nraise SystemExit(pytest.main(['-q']))\n")
    return str(path)


def test_the_reroute_is_refused_and_never_runs(tmp_path, script):
    console = _DenyShellConsole()
    agent = _agent(console, tmp_path)

    first = agent._execute_tool("run_shell_command", {"command": "pytest -q"})
    second = agent._execute_tool("execute_python_file", {"file_path": script})

    assert first["status"] == "denied"
    assert second["status"] == "denied"
    assert second["executed"] is False
    assert agent.ran == []
    assert "pytest -q" in second["error"]
    assert "runs the test suite" in second["error"]
    # Nothing to ask on this console: the model is told to ask in its reply.
    assert "ask the user" in second["error"]
    # Refused before the prompt: nobody is asked to approve a reroute.
    assert console.prompted == ["run_shell_command"]


def test_unrelated_calls_still_run(tmp_path):
    agent = _agent(_DenyShellConsole(), tmp_path)
    agent._execute_tool("run_shell_command", {"command": "pytest -q"})

    result = agent._execute_tool("read_file", {"file_path": "README.md"})

    assert result["status"] == "success"
    assert agent.ran == [("read_file", "README.md")]


def test_the_user_is_asked_with_the_exact_command(tmp_path, script):
    console = _AskingConsole("__NO_RESPONSE__")
    agent = _agent(console, tmp_path)
    agent._execute_tool("run_shell_command", {"command": "pytest -q"})

    result = agent._execute_tool("execute_python_file", {"file_path": script})

    assert result["status"] == "denied"
    assert agent.ran == []
    [(question, choices)] = console.questions
    assert "`pytest -q`" in question
    assert f"python {script}" in question
    assert choices == [Agent.ALLOW_DENIED_EFFECT, Agent.KEEP_DENIED_EFFECT]


def test_allow_once_runs_it_without_asking_twice(tmp_path, script):
    console = _AskingConsole(Agent.ALLOW_DENIED_EFFECT)
    agent = _agent(console, tmp_path)
    agent._execute_tool("run_shell_command", {"command": "pytest -q"})

    result = agent._execute_tool("execute_python_file", {"file_path": script})

    assert result["status"] == "success"
    assert agent.ran == [("execute_python_file", script)]
    assert console.prompted == ["run_shell_command"]
    # Once means once: the next route to the suite is asked about again.
    agent._execute_tool("run_shell_command", {"command": "python -m pytest"})
    assert len(console.questions) == 2


@pytest.mark.parametrize("answer", ["ok", "Sure.", "yes please", "go ahead"])
def test_a_plain_yes_counts_as_allow(tmp_path, script, answer):
    agent = _agent(_AskingConsole(answer), tmp_path)
    agent._execute_tool("run_shell_command", {"command": "pytest -q"})

    result = agent._execute_tool("execute_python_file", {"file_path": script})

    assert result["status"] == "success"


@pytest.mark.parametrize(
    "answer, expected",
    [
        (Agent.KEEP_DENIED_EFFECT, "said no again"),
        ("only run the store tests", "only run the store tests"),
    ],
)
def test_a_no_or_a_reply_is_passed_back(tmp_path, script, answer, expected):
    agent = _agent(_AskingConsole(answer), tmp_path)
    agent._execute_tool("run_shell_command", {"command": "pytest -q"})

    result = agent._execute_tool("execute_python_file", {"file_path": script})

    assert result["status"] == "denied"
    assert expected in result["error"]
    assert agent.ran == []


def test_a_console_that_cannot_take_answers_is_never_asked(tmp_path, script):
    # The TUI's stdio agent: a question there has no route back and hangs.
    console = _AskingConsole(Agent.ALLOW_DENIED_EFFECT)
    console.answers_questions = False
    agent = _agent(console, tmp_path)
    agent._execute_tool("run_shell_command", {"command": "pytest -q"})

    result = agent._execute_tool("execute_python_file", {"file_path": script})

    assert result["status"] == "denied"
    assert console.questions == []
    assert "ask the user" in result["error"]


def test_the_question_quotes_the_users_words_not_the_memory_context(tmp_path, script):
    console = _AskingConsole("__NO_RESPONSE__")
    agent = _agent(console, tmp_path)
    agent._original_user_input = "check the tie order in sorting.py"
    agent._current_query = (
        "[GAIA Memory Context]\nCurrent time: 2026-09-29\n\n"
        "check the tie order in sorting.py"
    )
    agent._execute_tool("run_shell_command", {"command": "pytest -q"})

    agent._execute_tool("execute_python_file", {"file_path": script})

    [(question, _)] = console.questions
    assert "check the tie order in sorting.py" in question
    assert "GAIA Memory Context" not in question


def test_a_background_run_is_never_asked(tmp_path, script):
    console = _AskingConsole(Agent.ALLOW_DENIED_EFFECT)
    console.background_mode = True
    agent = _agent(console, tmp_path)
    agent._execute_tool("run_shell_command", {"command": "pytest -q"})

    result = agent._execute_tool("execute_python_file", {"file_path": script})

    assert result["status"] == "denied"
    assert console.questions == []


def test_a_console_that_insists_overrides_a_grant(tmp_path):
    console = _DenyShellConsole()
    agent = _agent(console, tmp_path)
    agent.skill_grant_covers_call = lambda name, args: True
    assert agent._call_is_pre_authorized("run_shell_command", {"command": "pytest"})

    console.insists_on_asking = lambda name, args: "pytest" in args["command"]
    assert not agent._call_is_pre_authorized("run_shell_command", {"command": "pytest"})
    assert agent._call_is_pre_authorized("run_shell_command", {"command": "ls"})


def test_a_preflight_refusal_is_a_step_not_a_denial(tmp_path):
    """A read-first refusal is fixed by reading; the retry must not be blocked."""
    agent = _agent(_DenyShellConsole(), tmp_path)
    agent.policy_refusal_for_call = lambda name, args: (
        {"status": "error", "error": "Read it with read_file before overwriting it"}
        if name == "write_file"
        else None
    )
    agent._execute_tool("write_file", {"file_path": "a.py", "content": "x"})
    assert not agent._denied_effects


def test_a_refused_call_is_never_offered_as_allow_once(tmp_path, script):
    """The policy gate runs before the reroute question (validate, then ask)."""
    console = _AskingConsole(Agent.ALLOW_DENIED_EFFECT)
    agent = _agent(console, tmp_path)
    agent._execute_tool("run_shell_command", {"command": "pytest -q"})
    agent.policy_refusal_for_call = lambda name, args: (
        {"status": "error", "error": "not allowed"}
        if name == "execute_python_file"
        else None
    )

    result = agent._execute_tool("execute_python_file", {"file_path": script})

    assert result["status"] == "error"
    assert console.questions == []


def test_a_policy_refusal_records_the_host_not_the_command(tmp_path):
    agent = _agent(_DenyShellConsole(), tmp_path)
    refused_host = "api.github.com"

    def _policy(tool_name, tool_args):
        if refused_host in str(tool_args.get("command", "")):
            return {"status": "error", "error": "curl is not allowed here"}
        return None

    agent.policy_refusal_for_call = _policy
    agent._execute_tool(
        "run_shell_command", {"command": f"curl https://{refused_host}/x"}
    )

    code = f"import urllib.request\nurllib.request.urlopen('https://{refused_host}')"
    ledger = agent._denied_effects
    assert ledger.conflict("run_python", {"code": code}) is not None
    assert (
        ledger.conflict("run_shell_command", {"command": "curl https://example.com"})
        is None
    )


# ---------------------------------------------------------------------------
# The loop: per-turn reset, and a recovery prompt that stops inviting reroutes
# ---------------------------------------------------------------------------


def _stub_chat(agent_, *responses):
    queue = list(responses)
    sent: list = []
    chat = MagicMock()

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        if not queue:
            raise AssertionError("chat stub ran out of scripted responses")
        resp = MagicMock()
        resp.text = queue.pop(0)
        resp.stats = {}
        return resp

    chat.send_messages = MagicMock(side_effect=_send)
    agent_.chat = chat
    return sent


def _call(name, args):
    return json.dumps({"thought": "next", "tool": name, "tool_args": args})


def _answer(text):
    return json.dumps({"thought": "done", "answer": text})


def test_the_ledger_lasts_one_turn(tmp_path, script, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = _agent(_DenyShellConsole(), tmp_path)
    agent.streaming = False

    _stub_chat(
        agent,
        _call("run_shell_command", {"command": "pytest -q"}),
        _call("execute_python_file", {"file_path": script}),
        _answer("Blocked: you declined pytest."),
    )
    agent.process_query("fix it and run the tests")
    assert agent.ran == []

    _stub_chat(
        agent,
        _call("execute_python_file", {"file_path": script}),
        _answer("Ran it."),
    )
    agent.process_query("ok, run run_tests.py")
    assert agent.ran == [("execute_python_file", script)]


def test_a_refusal_gets_a_recovery_prompt_that_does_not_invite_a_reroute(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    agent = _agent(_DenyShellConsole(), tmp_path)
    agent.streaming = False
    agent.policy_refusal_for_call = lambda name, args: (
        {"status": "error", "error": "'curl' is not allowed: it reaches the network"}
        if "curl" in str(args.get("command", ""))
        else None
    )
    sent = _stub_chat(
        agent,
        _call("run_shell_command", {"command": "curl https://api.github.com/x"}),
        _answer("I can't reach the API from here."),
    )

    agent.process_query("list the open issues")

    recovery = [
        m["content"]
        for m in sent[-1]
        if m.get("role") == "user" and "TOOL EXECUTION FAILED" in str(m["content"])
    ]
    assert recovery, "the refusal should still reach the recovery step"
    assert "Create a NEW corrected plan" not in recovery[-1]
    assert "do not reach the same result" in recovery[-1]


def test_an_ordinary_error_keeps_the_fix_it_prompt():
    assert not Agent._is_denial_error("FileNotFoundError: no such file 'x.py'")
    assert Agent._is_denial_error("Tool 'run_shell_command' was denied by the user.")
    assert Agent._is_denial_error(
        "Confirmation for 'run_shell_command' timed out after 60 s with no user "
        "response. Execution denied."
    )


def test_cwd_default_is_the_process_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert DeniedEffects().cwd == os.getcwd()
