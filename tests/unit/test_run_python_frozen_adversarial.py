# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Hostile scripts and argv for the frozen Python runner (#5088).

The tools are driven in frozen mode and the argv they build is relayed to a
real interpreter standing in for the binary's entry point (``dispatch``), so
each case runs the whole path: tool -> argv -> dispatch -> script -> result.
"""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat import python_runner  # noqa: E402
from gaia_agent_chat.python_runner import (  # noqa: E402
    BUNDLED_LIBRARIES,
    RUN_PYTHON_FILE_FLAG,
    dispatch,
    interpreter_request,
)

from tests.unit.test_run_python_frozen import (  # noqa: E402,F401
    _DISPATCH,
    FAKE_BINARY,
    _dispatch,
    _run_both,
    child_env,
    frozen,
    no_inherited_project_root,
    project,
    tools,
)

_REAL_RUN = subprocess.run
#: The real interpreter; the ``frozen`` fixture points sys.executable elsewhere.
_PYTHON = sys.executable
_RUNNER_ROOT = os.path.dirname(os.path.dirname(python_runner.__file__))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep the developer's ~/.gaia MCP servers out of every agent built here."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


@pytest.fixture
def relay():
    """Run whatever argv a frozen tool builds through a stand-in binary whose
    entry point is the real ``dispatch``. Records each relayed argv."""
    seen = []

    def run(argv, **kwargs):
        assert argv[0] == FAKE_BINARY, argv
        seen.append(list(argv))
        env = dict(kwargs.get("env") or os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (env.get("PYTHONPATH"), _RUNNER_ROOT) if p
        )
        kwargs["env"] = env
        return _REAL_RUN([_PYTHON, "-c", _DISPATCH, *argv[1:]], **kwargs)

    with patch("subprocess.run", side_effect=run):
        yield seen


# ── Awkward script paths and arguments ───────────────────────────────────


_NAMES = ["with space", "ünïcødé 日本", "it's", "a;b&c", "trailing."]


@pytest.mark.parametrize("folder", _NAMES)
def test_awkward_script_paths_run_through_the_frozen_tool(
    frozen, tools, project, relay, folder
):
    if folder.endswith(".") and os.name == "nt":
        pytest.skip("Windows strips a trailing dot from a folder name")
    where = project / folder
    where.mkdir()
    script = where / "my script ✓.py"
    script.write_text(
        "import os, sys\nprint(os.path.basename(sys.argv[0]))\n", encoding="utf-8"
    )

    result = tools["execute_python_file"](file_path=str(script))

    assert result["status"] == "success", result
    assert result["stdout"].strip() == "my script ✓.py"
    assert relay[0][1:3] == [RUN_PYTHON_FILE_FLAG, str(script.resolve())]


def test_script_sees_quoted_args_and_lookalike_flags_verbatim(
    frozen, tools, project, relay
):
    """Args that look like the runner's or the agent's own flags, or like
    interpreter options, belong to the script and nothing else."""
    script = project / "argv.py"
    script.write_text("import sys, json\nprint(json.dumps(sys.argv[1:]))\n")

    result = tools["execute_python_file"](
        file_path=str(script),
        args=f'{RUN_PYTHON_FILE_FLAG} --json-events -m pip -c "two words" \'q"x\'',
    )

    assert result["status"] == "success", result
    assert result["stdout"].strip() == (
        '["--gaia-run-python-file", "--json-events", "-m", "pip", "-c", '
        '"two words", "q\\"x"]'
    )


def test_argv_through_runner_matches_python_for_hostile_values(project, child_env):
    script = project / "argv.py"
    script.write_text("import sys\nprint(repr(sys.argv[1:]))\n", encoding="utf-8")
    args = ["", " ", "-m", "--", "日本", 'a"b', "%PATH%", "$HOME", "-c"]

    child_env["PYTHONIOENCODING"] = "utf-8"
    real, frozen_run = _run_both(script, args, project, child_env)

    assert real.returncode == frozen_run.returncode == 0, frozen_run.stderr
    assert frozen_run.stdout == real.stdout


# ── Exit codes ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body, code, needle",
    [
        ("import sys\nsys.exit(3)", 3, "exited with code 3"),
        ("import sys\nsys.exit(255)", 255, "exited with code 255"),
        ("import sys\nsys.exit('bad input')", 1, "bad input"),
        ("import os\nos._exit(7)", 7, "exited with code 7"),
        ("raise KeyboardInterrupt", 1, "KeyboardInterrupt"),
    ],
)
@pytest.mark.parametrize("tool", ["run_python", "execute_python_file"])
def test_nonzero_exit_is_an_error_carrying_the_code(
    frozen, tools, project, relay, tool, body, code, needle
):
    if tool == "run_python":
        result = tools[tool](code=body)
    else:
        script = project / "exits.py"
        script.write_text(body + "\n", encoding="utf-8")
        result = tools[tool](file_path=str(script))

    assert result["status"] == "error", result
    assert result.get("return_code") == code, result
    assert needle in result["error"]


# ── Scripts that start sys.executable themselves ─────────────────────────


def test_script_spawning_the_binary_with_c_and_m_json_tool(project, child_env):
    """Inside a frozen build ``sys.executable`` is the binary; a script that
    starts it with ``-c`` or ``-m json.tool`` must get Python, not the agent."""
    data = project / "d.json"
    data.write_text('{"k": [1, 2]}', encoding="utf-8")
    script = project / "spawns.py"
    script.write_text(
        "import subprocess, sys\n"
        f"BINARY = [sys.executable, '-c', {_DISPATCH!r}]\n"
        "a = subprocess.run(BINARY + ['-c', 'print(6*7)'], capture_output=True,"
        " text=True, stdin=subprocess.DEVNULL)\n"
        f"b = subprocess.run(BINARY + ['-m', 'json.tool', {str(data)!r}],"
        " capture_output=True, text=True, stdin=subprocess.DEVNULL)\n"
        "c = subprocess.run(BINARY + ['-m', 'pip', 'install', 'x'],"
        " capture_output=True, text=True, stdin=subprocess.DEVNULL)\n"
        "print(a.returncode, a.stdout.strip())\n"
        "print(b.returncode, b.stdout.count('\"k\"'))\n"
        "print(c.returncode, 'cannot install' if \"can't be installed\" in "
        "c.stderr else c.stderr)\n",
        encoding="utf-8",
    )

    _, frozen_run = _run_both(script, [], project, child_env)

    assert frozen_run.returncode == 0, frozen_run.stderr
    assert frozen_run.stdout.splitlines() == ["0 42", "0 1", "1 cannot install"]


@pytest.mark.parametrize(
    "argv",
    [
        ["-m", "pip", "install", "x"],
        ["-mpip", "install", "x"],
        ["-u", "-m", "pip", "install", "x"],
        ["-I", "-X", "utf8", "-m", "pip3", "install", "x"],
        ["-m", "pip._internal", "install", "x"],
        ["-m", "ensurepip"],
        ["-m", "venv", "env"],
        ["-m", "uv", "pip", "install", "x"],
    ],
)
def test_every_installer_spelling_is_refused_with_the_library_list(
    argv, project, child_env
):
    result = _dispatch(argv, project, child_env)

    assert result.returncode == 1
    assert "can't be installed" in result.stderr
    assert all(name in result.stderr for name in BUNDLED_LIBRARIES)
    assert "Traceback" not in result.stderr


def test_json_tool_on_closed_stdin_fails_fast_instead_of_hanging(project, child_env):
    """``-m json.tool`` with no file reads stdin; with stdin closed it must
    fail at once, well inside the subprocess timeout."""
    result = _dispatch(["-m", "json.tool"], project, child_env)
    assert result.returncode != 0
    assert "Expecting value" in result.stderr


# ── stdout/stdin abuse ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "import sys\nprint('kept')\nsys.stdout.close()",
        "import sys\nprint('kept')\nsys.stdout.close()\nraise SystemExit(5)",
        "import sys\nsys.stderr.close()\nraise ValueError('hidden')",
        "import sys\nsys.stdout = None\nprint('dropped')",
    ],
    ids=["close-stdout", "close-stdout-exit", "close-stderr-raise", "stdout-none"],
)
def test_scripts_that_close_their_streams_match_python(project, child_env, body):
    script = project / "streams.py"
    script.write_text(body + "\n", encoding="utf-8")

    real, frozen_run = _run_both(script, [], project, child_env)

    assert frozen_run.returncode == real.returncode, frozen_run.stderr
    assert frozen_run.stdout == real.stdout


@pytest.mark.parametrize("tool", ["run_python", "execute_python_file"])
def test_input_on_closed_stdin_errors_instead_of_hanging(
    frozen, tools, project, relay, tool
):
    body = "name = input('name? ')\nprint(name)"
    if tool == "run_python":
        result = tools[tool](code=body)
    else:
        script = project / "asks.py"
        script.write_text(body + "\n", encoding="utf-8")
        result = tools[tool](file_path=str(script), timeout=60)

    assert result["status"] == "error", result
    assert "EOFError" in result["error"]
    assert result["duration_seconds"] < 30


@pytest.mark.parametrize("tool", ["run_python", "execute_python_file"])
def test_huge_output_is_truncated_and_still_a_success(
    frozen, tools, project, relay, tool
):
    body = "import sys\nsys.stdout.write('x' * 2_000_000)\nprint('END')"
    if tool == "run_python":
        result = tools[tool](code=body)
    else:
        script = project / "loud.py"
        script.write_text(body + "\n", encoding="utf-8")
        result = tools[tool](file_path=str(script))

    assert result["status"] == "success", {
        k: v for k, v in result.items() if k != "stdout"
    }
    assert len(result["stdout"]) < 20_000


# ── The agent's own argv never reaches the runner ────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--json-events"],
        ["--json-events", "--dev"],
        ["--dev", "--json-events", "--full-access"],
        ["--model", "Qwen3.6-35B-A3B-GGUF", "--json-events"],
        ["--model=x.py"],
        ["--history-file", "C:/Users/me/run me.py", "--json-events"],
        ["--use-claude", "--claude-model", "claude-opus-5"],
        ["--host", "127.0.0.1", "--port", "8141"],
        ["--serve", "--host", "0.0.0.0"],
        ["--app-dir", "C:/x", "--host", "h", "--port", "1"],
        ["-"],
        ["-X"],
        ["-W"],
        ["-u", "-B"],
        ["-h"],
        ["--help"],
        ["--version"],
    ],
)
def test_agent_argv_falls_through_to_the_agent(argv):
    assert interpreter_request(argv) is None
    assert dispatch(argv) is None


def test_runner_flag_not_first_is_left_to_the_agent():
    assert dispatch(["--dev", RUN_PYTHON_FILE_FLAG, "x.py"]) is None


def test_runner_flag_without_a_script_is_a_usage_error(capsys):
    assert dispatch([RUN_PYTHON_FILE_FLAG]) == 2
    assert "usage" in capsys.readouterr().err
