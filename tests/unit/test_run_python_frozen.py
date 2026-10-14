# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``run_python`` and ``execute_python_file`` work inside the frozen agent (#5088).

A PyInstaller build has no interpreter: ``sys.executable`` is the agent binary,
so ``[sys.executable, script]`` handed the script path to the agent's own
argument parser and nothing ever ran. The TUI still showed the call as a
success, because the tool returned ``status: success`` for a non-zero exit.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat import python_runner  # noqa: E402
from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig  # noqa: E402
from gaia_agent_chat.python_jobs import PythonJob  # noqa: E402
from gaia_agent_chat.python_runner import (  # noqa: E402
    RUN_PYTHON_FILE_FLAG,
    python_command,
    requests_run_python_file,
)

from gaia.agents.base.project_map import PROJECT_ROOT_ENV  # noqa: E402
from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402

FAKE_BINARY = os.path.join("C:\\", "Program Files", "GAIA", "gaia-agent.exe")


@pytest.fixture(autouse=True)
def no_inherited_project_root(monkeypatch):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)


@pytest.fixture
def frozen(monkeypatch):
    """This process as PyInstaller presents it: frozen, executable = the agent."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", FAKE_BINARY)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'toy'\n", encoding="utf-8")
    return root.resolve()


@pytest.fixture
def tools(project, monkeypatch):
    """The agent's registered Python tools, keyed by name."""
    monkeypatch.chdir(project)
    saved = dict(_TOOL_REGISTRY)
    with (
        patch("gaia_agent_chat.agent.RAGSDK"),
        patch("gaia_agent_chat.agent.RAGConfig"),
    ):
        ChatAgent(ChatAgentConfig(silent_mode=True, allowed_paths=[str(project)]))
    try:
        yield {
            name: _TOOL_REGISTRY[name]["function"]
            for name in ("run_python", "execute_python_file")
        }
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _spawn(**finished):
    """Stand in for the child: a job that ran to completion."""
    return patch(
        "gaia_agent_chat.agent.run_python_job",
        return_value=PythonJob(_completed([], **finished), None, []),
    )


# ---------------------------------------------------------------------------
# The command each tool builds
# ---------------------------------------------------------------------------


def test_from_source_the_interpreter_runs_the_script():
    assert python_command("s.py", ["a", "b"]) == [sys.executable, "s.py", "a", "b"]


def test_frozen_the_binary_is_asked_for_its_runner_mode(frozen):
    cmd = python_command("s.py", ["a"])

    assert cmd == [FAKE_BINARY, RUN_PYTHON_FILE_FLAG, "s.py", "a"]


def test_frozen_run_python_never_hands_the_script_to_the_agent_parser(
    frozen, tools, project
):
    with _spawn(stdout="42\n") as spawn:
        result = tools["run_python"](code="print(6 * 7)")

    argv = spawn.call_args.args[0]
    assert argv[0] == FAKE_BINARY
    assert argv[1] == RUN_PYTHON_FILE_FLAG
    assert argv[2].endswith(".py") and len(argv) == 3
    assert result["status"] == "success", result


def test_frozen_execute_python_file_passes_its_args_after_the_script(
    frozen, tools, project
):
    script = project / "job.py"
    script.write_text("print('hi')\n", encoding="utf-8")

    with _spawn() as spawn:
        tools["execute_python_file"](file_path=str(script), args="--rows 3")

    assert spawn.call_args.args[0] == [
        FAKE_BINARY,
        RUN_PYTHON_FILE_FLAG,
        str(script),
        "--rows",
        "3",
    ]


# ---------------------------------------------------------------------------
# A failed run reads as a failure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["run_python", "execute_python_file"])
def test_a_non_zero_exit_is_an_error_with_the_stderr(tool, tools, project):
    code = "import sys\nprint('partial')\nsys.exit('bom.xlsx could not be written')\n"
    if tool == "run_python":
        result = tools[tool](code=code)
    else:
        script = project / "fails.py"
        script.write_text(code, encoding="utf-8")
        result = tools[tool](file_path=str(script))

    assert result["status"] == "error", result
    assert result["has_errors"] is True
    assert result["return_code"] == 1
    assert "partial" in result["stdout"]
    assert "bom.xlsx could not be written" in result["stderr"]
    assert "exited with code 1" in result["error"]
    assert "bom.xlsx could not be written" in result["error"]


@pytest.mark.parametrize("tool", ["run_python", "execute_python_file"])
def test_the_agent_parser_rejecting_the_script_is_an_error(tool, tools, project):
    """The exact #5088 failure: exit 2, argparse's complaint on stderr."""
    stderr = "gaia-agent-gaia-stdio: error: unrecognized arguments: C:/t.py\n"
    script = project / "t.py"
    script.write_text("print(1)\n", encoding="utf-8")
    with _spawn(returncode=2, stderr=stderr):
        if tool == "run_python":
            result = tools[tool](code="print(1)")
        else:
            result = tools[tool](file_path=str(script))

    assert result["status"] == "error", result
    assert "unrecognized arguments" in result["error"]


@pytest.mark.parametrize("tool", ["run_python", "execute_python_file"])
def test_a_start_failure_is_an_error_naming_the_program(tool, frozen, tools, project):
    script = project / "t.py"
    script.write_text("print(1)\n", encoding="utf-8")
    with patch(
        "gaia_agent_chat.agent.run_python_job",
        side_effect=FileNotFoundError(2, "not found"),
    ):
        if tool == "run_python":
            result = tools[tool](code="print(1)")
        else:
            result = tools[tool](file_path=str(script))

    assert result["status"] == "error", result
    assert result["has_errors"] is True
    assert FAKE_BINARY in result["error"]


def test_the_ui_event_reports_the_failure(tools):
    """What the TUI trace shows: ``success`` must be False for a failed run."""
    from gaia.ui.sse_handler import SSEOutputHandler

    result = tools["run_python"](code="raise SystemExit(3)")
    handler = SSEOutputHandler()
    handler.print_tool_usage("run_python")
    handler.pretty_print_json(result, title="Tool Result")
    event = handler.event_queue.get_nowait()
    while event.get("type") != "tool_result":
        event = handler.event_queue.get_nowait()

    assert event["success"] is False
    assert "exited with code 3" in event["summary"]


# ---------------------------------------------------------------------------
# The frozen child behaves like ``python script.py``
# ---------------------------------------------------------------------------

#: Stand-in for the frozen binary's entry point: the same dispatch call.
_CHILD = (
    "import sys\n"
    "from gaia_agent_chat.python_runner import requests_run_python_file, "
    "run_python_file\n"
    "assert requests_run_python_file(sys.argv[1:])\n"
    "sys.exit(run_python_file(sys.argv[2:]))\n"
)


def _run_both(script, args, cwd, env):
    """``(python script args, runner script args)``, same cwd/env/stdin."""
    common = dict(
        cwd=str(cwd),
        env=env,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    real = subprocess.run([sys.executable, str(script), *args], **common)
    frozen = subprocess.run(
        [sys.executable, "-c", _CHILD, RUN_PYTHON_FILE_FLAG, str(script), *args],
        **common,
    )
    return real, frozen


@pytest.fixture
def child_env(project):
    """The project on the path, as the tools set it, plus this checkout's runner."""
    runner_root = os.path.dirname(os.path.dirname(python_runner.__file__))
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(project), runner_root])
    return env


@pytest.mark.parametrize(
    "body,args,code",
    [
        ("print(__name__)", [], 0),
        ("import sys\nprint(sys.argv[1:])", ["a", "b c"], 0),
        ("import sys\nprint(sys.stdin.read() == '')", [], 0),
        ("import os\nprint(os.getcwd())", [], 0),
        ("import sys\nsys.exit(4)", [], 4),
        ("import sys\nsys.exit()", [], 0),
        ("import sys\nsys.exit('message')", [], 1),
        ("raise ValueError('boom')", [], 1),
        ("from pkg import VALUE\nprint(VALUE)", [], 0),
        ("import helper\nprint(helper.X)", [], 0),
    ],
)
def test_the_runner_matches_the_interpreter(project, child_env, body, args, code):
    (project / "pkg").mkdir(exist_ok=True)
    (project / "pkg" / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")
    scripts = project / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "helper.py").write_text("X = 'sibling'\n", encoding="utf-8")
    script = scripts / "main.py"
    script.write_text(textwrap.dedent(body) + "\n", encoding="utf-8")

    real, frozen = _run_both(script, args, project, child_env)

    assert real.returncode == code, real.stderr
    assert frozen.returncode == real.returncode, frozen.stderr
    assert frozen.stdout == real.stdout
    assert frozen.stderr.splitlines()[-1:] == real.stderr.splitlines()[-1:]


def test_a_traceback_shows_the_script_not_the_runner(project, child_env):
    script = project / "boom.py"
    script.write_text("def f():\n    1 / 0\n\nf()\n", encoding="utf-8")

    _, frozen = _run_both(script, [], project, child_env)

    assert "ZeroDivisionError" in frozen.stderr
    assert str(script) in frozen.stderr
    assert "runpy" not in frozen.stderr
    assert "python_runner" not in frozen.stderr


def test_a_missing_script_exits_2(project, child_env):
    _, frozen = _run_both(project / "nope.py", [], project, child_env)

    assert frozen.returncode == 2
    assert "can't open file" in frozen.stderr


def test_only_the_flag_in_first_position_selects_the_runner():
    assert requests_run_python_file([RUN_PYTHON_FILE_FLAG, "x.py"])
    assert not requests_run_python_file([])
    assert not requests_run_python_file(["--dev", RUN_PYTHON_FILE_FLAG])


def test_the_runner_module_imports_only_the_stdlib():
    """It runs before the user's script, in the same process."""
    import ast

    tree = ast.parse(open(python_runner.__file__, encoding="utf-8").read())
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


def test_frozen_the_child_keeps_pyinstallers_environment(frozen, tools, monkeypatch):
    """Without ``_PYI_*`` a one-file child unpacks the whole binary again."""
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "/tmp/_MEI1234")
    with _spawn() as spawn:
        tools["run_python"](code="print(1)")

    env = spawn.call_args.kwargs["env"]
    assert env["_PYI_APPLICATION_HOME_DIR"] == "/tmp/_MEI1234"


def test_frozen_output_is_utf8_whatever_the_console_codec(project, child_env):
    """A frozen interpreter ignores PYTHONIOENCODING; the tools decode UTF-8."""
    script = project / "tick.py"
    script.write_text("print('done \u2713')\n", encoding="utf-8")
    child_env["PYTHONIOENCODING"] = "cp1252"
    frozen_child = "import sys\nsys.frozen = True\n" + _CHILD

    result = subprocess.run(
        [sys.executable, "-c", frozen_child, RUN_PYTHON_FILE_FLAG, str(script)],
        cwd=str(project),
        env=child_env,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.decode("utf-8").strip() == "done \u2713"
