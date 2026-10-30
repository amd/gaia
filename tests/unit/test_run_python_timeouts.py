# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Long ``run_python`` / ``execute_python_file`` jobs: a run time the model can
raise, a kill that reaches every process the job started, and a report of the
files it was writing when it was stopped.
"""

from __future__ import annotations

import os
import sys
import textwrap
import time
from unittest.mock import patch

import psutil
import pytest

pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig  # noqa: E402
from gaia_agent_chat.python_jobs import (  # noqa: E402
    DEFAULT_PYTHON_TIMEOUT,
    MAX_PYTHON_TIMEOUT,
    files_written_since,
    folders_named_in,
    resolve_python_timeout,
    run_python_job,
    watch_folders,
)

from gaia.agents.base.agent import (  # noqa: E402
    DEFAULT_TOOL_TIMEOUT,
    ToolExecutionTimeout,
)
from gaia.agents.base.project_map import PROJECT_ROOT_ENV  # noqa: E402
from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402


@pytest.fixture(autouse=True)
def no_inherited_project_root(monkeypatch, project):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    monkeypatch.chdir(project)


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("project")
    (root / "pyproject.toml").write_text("[project]\nname = 'toy'\n")
    return root.resolve()


@pytest.fixture(scope="module")
def agent_tools(project):
    """One ChatAgent rooted at *project* (building one takes ~10 s)."""
    saved = dict(_TOOL_REGISTRY)
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv(PROJECT_ROOT_ENV, raising=False)
        mp.chdir(project)
        with (
            patch("gaia_agent_chat.agent.RAGSDK"),
            patch("gaia_agent_chat.agent.RAGConfig"),
        ):
            agent = ChatAgent(
                ChatAgentConfig(silent_mode=True, allowed_paths=[str(project)])
            )
        try:
            yield agent, dict(_TOOL_REGISTRY)
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)


def _wait_gone(pid: int, seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not psutil.pid_exists(pid):
            return True
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return False


# ── the run time ────────────────────────────────────────────────────────────


def test_a_job_slower_than_its_timeout_is_stopped(agent_tools):
    _agent, tools = agent_tools
    run_python = tools["run_python"]["function"]

    result = run_python(code="import time\ntime.sleep(3)\nprint('done')", timeout=1)

    assert result["status"] == "error"
    assert result["timed_out"] is True
    assert "Timed out after 1s" in result["error"]
    assert f"max {MAX_PYTHON_TIMEOUT}" in result["error"]


def test_the_same_job_finishes_with_a_longer_timeout(agent_tools):
    _agent, tools = agent_tools
    run_python = tools["run_python"]["function"]

    result = run_python(code="import time\ntime.sleep(3)\nprint('done')", timeout=10)

    assert result["status"] == "success", result
    assert result["stdout"].strip() == "done"


def test_execute_python_file_honours_its_timeout(agent_tools, project):
    _agent, tools = agent_tools
    execute = tools["execute_python_file"]["function"]
    script = project / "slow.py"
    script.write_text("import time\ntime.sleep(3)\nprint('done')\n")

    stopped = execute(file_path=str(script), timeout=1)
    finished = execute(file_path=str(script), timeout=10)

    assert stopped["timed_out"] is True
    assert finished["status"] == "success", finished


@pytest.mark.parametrize("tool_name", ["run_python", "execute_python_file"])
def test_the_ceiling_is_refused_not_clamped(agent_tools, project, tool_name):
    _agent, tools = agent_tools
    func = tools[tool_name]["function"]
    script = project / "noop.py"
    script.write_text("print('ran')\n")
    args = {"code": "print('ran')"} if tool_name == "run_python" else {}
    if tool_name == "execute_python_file":
        args["file_path"] = str(script)

    result = func(timeout=MAX_PYTHON_TIMEOUT + 1, **args)

    assert result["status"] == "error"
    assert f"{MAX_PYTHON_TIMEOUT}s ceiling" in result["error"]
    assert "stdout" not in result


def test_resolve_python_timeout():
    assert resolve_python_timeout(None) == DEFAULT_PYTHON_TIMEOUT
    assert resolve_python_timeout(MAX_PYTHON_TIMEOUT) == MAX_PYTHON_TIMEOUT
    for bad in (0, -1, "soon", MAX_PYTHON_TIMEOUT + 1):
        with pytest.raises(ValueError):
            resolve_python_timeout(bad)


def test_the_default_fails_on_its_own_clock_before_the_agent_gives_up():
    assert DEFAULT_PYTHON_TIMEOUT < DEFAULT_TOOL_TIMEOUT


@pytest.mark.parametrize("tool_name", ["run_python", "execute_python_file"])
def test_the_agent_waits_out_the_requested_timeout(agent_tools, tool_name):
    agent, tools = agent_tools
    assert tools[tool_name]["timeout_arg"] == "timeout"
    assert agent._bounded_tool_timeout(tool_name, {"timeout": 900}) == 930.0
    assert agent._bounded_tool_timeout(tool_name, {}) == DEFAULT_TOOL_TIMEOUT


# ── the kill ────────────────────────────────────────────────────────────────


def test_a_timeout_kills_the_grandchild_too(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    script = tmp_path / "spawn.py"
    script.write_text(textwrap.dedent(f"""
            import subprocess, sys, time
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"]
            )
            open({str(pid_file)!r}, "w").write(str(child.pid))
            time.sleep(60)
            """))

    job = run_python_job(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        env=None,
        timeout=3,
        should_stop=lambda: False,
    )

    assert job.stopped == "timeout"
    grandchild = int(pid_file.read_text())
    assert _wait_gone(grandchild), f"grandchild {grandchild} outlived the timeout"


def _spawner(tmp_path, then: str) -> tuple:
    """A script that starts a 60 s grandchild, records its pid, then *then*."""
    pid_file = tmp_path / "grandchild.pid"
    script = tmp_path / "spawn.py"
    script.write_text(textwrap.dedent(f"""
            import subprocess, sys, time
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"]
            )
            open({str(pid_file)!r}, "w").write(str(child.pid))
            """) + then + "\n")
    return script, pid_file


def test_a_script_that_exits_ends_the_run_and_its_leftovers(tmp_path):
    script, pid_file = _spawner(tmp_path, "print('done')")

    t0 = time.monotonic()
    job = run_python_job(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        env=None,
        timeout=60,
        should_stop=lambda: False,
    )

    assert job.stopped is None
    assert job.run.returncode == 0
    assert job.run.stdout.strip() == "done"
    assert job.left_running is True
    assert time.monotonic() - t0 < 30
    assert _wait_gone(int(pid_file.read_text()))


def test_an_abandoned_call_kills_its_grandchild(agent_tools, project, monkeypatch):
    """The agent gives up on the call; the job's whole tree goes with it."""
    agent, tools = agent_tools
    # Abandon well before the job's own 30 s timeout.
    monkeypatch.setattr("gaia.agents.base.agent.REQUESTED_TIMEOUT_GRACE", -27)
    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "1")
    script, pid_file = _spawner(project, "time.sleep(60)")

    with pytest.raises(ToolExecutionTimeout):
        agent._call_tool_bounded(
            tools["execute_python_file"]["function"],
            {"file_path": str(script), "timeout": 30},
            "execute_python_file",
        )

    deadline = time.monotonic() + 15
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _wait_gone(int(pid_file.read_text())), "abandoned job kept running"


def test_a_stop_request_kills_the_job(tmp_path):
    asked = time.monotonic() + 0.5

    t0 = time.monotonic()
    job = run_python_job(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=str(tmp_path),
        env=None,
        timeout=60,
        should_stop=lambda: time.monotonic() > asked,
    )

    assert job.stopped == "cancelled"
    assert time.monotonic() - t0 < 15


# ── what it left behind ─────────────────────────────────────────────────────


def test_a_stopped_job_names_the_files_it_was_writing(agent_tools, project):
    _agent, tools = agent_tools
    run_python = tools["run_python"]["function"]
    out_dir = project.parent / "deliverables"
    out_dir.mkdir(exist_ok=True)
    before = out_dir / "notes.txt"
    before.write_text("the user's own file")
    old = time.time() - 3600
    os.utime(before, (old, old))

    code = textwrap.dedent(f"""
        import time
        with open(r"{out_dir / 'report.xlsx'}", "w") as f:
            f.write("half")
            f.flush()
            time.sleep(30)
        """)
    result = run_python(code=code, timeout=2)

    assert result["timed_out"] is True
    assert str(out_dir / "report.xlsx") in result["files_modified"]
    assert "report.xlsx" in result["error"]
    # Reported, never deleted; untouched files are not named.
    assert (out_dir / "report.xlsx").exists()
    assert before.read_text() == "the user's own file"
    assert str(before) not in result["files_modified"]


def test_folders_named_in_finds_absolute_output_folders(tmp_path):
    target = tmp_path / "out"
    target.mkdir()
    source = f"base = r'{target}'\nopen(r'{target / 'x.xlsx'}', 'w')\nrel = 'a/b'\n"

    assert folders_named_in(source) == [str(target)]
    assert folders_named_in("this is not python (") == []


def test_folders_named_in_skips_drive_roots_and_home(tmp_path):
    root = os.path.abspath(os.sep)
    home = os.path.expanduser("~")
    source = f"a = r'{root}'\nb = r'{os.path.join(root, 'x.txt')}'\nc = r'{home}'\n"

    assert folders_named_in(source) == []


def test_files_written_since_ignores_older_and_excluded_files(tmp_path):
    old = tmp_path / "old.txt"
    old.write_text("x")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    since = time.time()
    new = tmp_path / "sub" / "new.csv"
    new.parent.mkdir()
    new.write_text("y")
    script = tmp_path / "job.py"
    script.write_text("z")

    found, complete = files_written_since([tmp_path, tmp_path], since, ignore=[script])

    assert found == [str(new)]
    assert complete is True


def test_a_bounded_search_says_it_is_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr("gaia_agent_chat.python_jobs._REPORT_MAX_FILES", 1)
    since = time.time()
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")

    found, complete = files_written_since([tmp_path], since)

    assert len(found) == 1
    assert complete is False


def test_the_home_folder_is_never_searched():
    assert watch_folders(os.path.expanduser("~")) == []
    assert watch_folders(os.path.abspath(os.sep)) == []


def test_a_frozen_launch_is_killed_the_same_way(tmp_path):
    """The frozen agent runs scripts through its own runner mode (#5093)."""
    from gaia_agent_chat import python_runner

    script, pid_file = _spawner(tmp_path, "time.sleep(60)")
    launcher = (
        "import sys\nsys.frozen = True\n"
        "from gaia_agent_chat.python_runner import run_python_file\n"
        "sys.exit(run_python_file(sys.argv[2:]))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.dirname(os.path.dirname(python_runner.__file__))

    job = run_python_job(
        [
            sys.executable,
            "-c",
            launcher,
            python_runner.RUN_PYTHON_FILE_FLAG,
            str(script),
        ],
        cwd=str(tmp_path),
        env=env,
        timeout=4,
        should_stop=lambda: False,
    )

    assert job.stopped == "timeout"
    assert _wait_gone(int(pid_file.read_text()))


@pytest.mark.skipif(os.name != "nt", reason="Job Objects are Windows-only")
def test_without_a_job_object_the_live_tree_is_still_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gaia_agent_chat.python_jobs._assign_job_object", lambda _pid: None
    )
    script, pid_file = _spawner(tmp_path, "time.sleep(60)")

    job = run_python_job(
        [sys.executable, str(script)],
        cwd=str(tmp_path),
        env=None,
        timeout=3,
        should_stop=lambda: False,
    )

    assert job.stopped == "timeout"
    assert _wait_gone(int(pid_file.read_text()))
