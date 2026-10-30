# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Adversarial checks on long Python jobs (#5102, #5104).

Hostile timeouts, scripts that fan out into many processes, detach, ignore
signals, or write until they are killed, and two jobs running at once. Each
test asserts the guard holds: a bad timeout is refused before anything runs,
the agent's wait never passes its hard cap, every process a job started dies
with it, nothing the job wrote is deleted, and one job's kill never reaches
another job.
"""

from __future__ import annotations

import math
import os
import sys
import textwrap
import threading
import time
from unittest.mock import patch

import psutil
import pytest

pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.python_jobs import (  # noqa: E402
    DEFAULT_PYTHON_TIMEOUT,
    MAX_PYTHON_TIMEOUT,
    resolve_python_timeout,
    run_python_job,
    stopped_job_result,
)

from gaia.agents.base.agent import (  # noqa: E402
    DEFAULT_TOOL_TIMEOUT,
    MAX_REQUESTED_TOOL_TIMEOUT,
    REQUESTED_TIMEOUT_GRACE,
    Agent,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool  # noqa: E402


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _wait_gone(pids, seconds: float = 15.0) -> list:
    """The pids among *pids* still alive after *seconds*."""
    end = time.monotonic() + seconds
    left = list(pids)
    while left and time.monotonic() < end:
        left = [pid for pid in left if _alive(pid)]
        if left:
            time.sleep(0.1)
    return left


def _pids(path) -> list:
    return [int(line) for line in path.read_text().split() if line.strip()]


def _job(script, cwd, timeout, **kw):
    return run_python_job(
        [sys.executable, str(script)],
        cwd=str(cwd),
        env=None,
        timeout=timeout,
        should_stop=kw.pop("should_stop", lambda: False),
        **kw,
    )


# ---------------------------------------------------------------------------
# Hostile timeouts are refused before anything runs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        0,
        -1,
        -0.0,
        0.4,  # truncates to zero
        -(10**9),
        MAX_PYTHON_TIMEOUT + 1,
        str(MAX_PYTHON_TIMEOUT + 1),
        10**100,
        "abc",
        "",
        "  ",
        "1e9",
        "30s",
        "-5",
        float("nan"),
        float("inf"),
        float("-inf"),
        "inf",
        "nan",
        [],
        [30],
        {},
        object(),
        True,
        False,
    ],
)
def test_a_bad_timeout_is_refused_with_a_value_error(bad):
    with pytest.raises(ValueError):
        resolve_python_timeout(bad)


@pytest.mark.parametrize(
    "good,expected",
    [
        (None, DEFAULT_PYTHON_TIMEOUT),
        (1, 1),
        ("30", 30),
        (" 30 ", 30),
        (30.9, 30),
        (MAX_PYTHON_TIMEOUT, MAX_PYTHON_TIMEOUT),
        (str(MAX_PYTHON_TIMEOUT), MAX_PYTHON_TIMEOUT),
    ],
)
def test_a_usable_timeout_is_accepted_and_never_above_the_cap(good, expected):
    assert resolve_python_timeout(good) == expected
    assert resolve_python_timeout(good) <= MAX_PYTHON_TIMEOUT


# ---------------------------------------------------------------------------
# The agent's wait never passes its hard cap
# ---------------------------------------------------------------------------


@pytest.fixture
def timed_tool(monkeypatch):
    saved = dict(_TOOL_REGISTRY)
    monkeypatch.delenv("GAIA_AGENT_TOOL_TIMEOUT", raising=False)

    @tool(timeout_arg="timeout")
    def job(timeout: int = DEFAULT_PYTHON_TIMEOUT) -> dict:
        """Stands in for run_python."""
        return {"status": "success"}

    class _A(Agent):
        def _register_tools(self):
            pass

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _A(skip_lemonade=True, silent_mode=True)
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


HARD_CAP = MAX_REQUESTED_TOOL_TIMEOUT + REQUESTED_TIMEOUT_GRACE


@pytest.mark.parametrize(
    "requested",
    [
        10**9,
        10**300,
        float("inf"),
        "inf",
        "1e308",
        float("nan"),
        "nan",
        -1,
        -(10**9),
        0,
        "soon",
        [],
        {},
        True,
        MAX_PYTHON_TIMEOUT,
        MAX_PYTHON_TIMEOUT + 1,
    ],
)
def test_the_watchdog_wait_is_finite_and_capped(timed_tool, requested):
    wait = timed_tool._bounded_tool_timeout("job", {"timeout": requested})

    assert math.isfinite(wait)
    assert DEFAULT_TOOL_TIMEOUT <= wait <= HARD_CAP


def test_the_watchdog_waits_out_the_largest_allowed_request(timed_tool):
    wait = timed_tool._bounded_tool_timeout("job", {"timeout": MAX_PYTHON_TIMEOUT})

    assert wait >= MAX_PYTHON_TIMEOUT + REQUESTED_TIMEOUT_GRACE


# ---------------------------------------------------------------------------
# Every process a job starts dies with it
# ---------------------------------------------------------------------------


def _fan_out(tmp_path, children: int, depth: int, then: str):
    """A script whose tree is *children* wide and *depth* deep, pids recorded."""
    pid_file = tmp_path / "pids.txt"
    worker = tmp_path / "worker.py"
    worker.write_text(textwrap.dedent(f"""
        import os, subprocess, sys, time
        depth = int(sys.argv[1])
        with open({str(pid_file)!r}, "a") as f:
            f.write(f"{{os.getpid()}}\\n")
        if depth > 0:
            subprocess.Popen([sys.executable, __file__, str(depth - 1)])
        time.sleep(120)
        """))
    script = tmp_path / "fan.py"
    script.write_text(textwrap.dedent(f"""
        import subprocess, sys, time
        for _ in range({children}):
            subprocess.Popen([sys.executable, {str(worker)!r}, "{depth - 1}"])
        """) + then + "\n")
    return script, pid_file


def _wait_for_pids(pid_file, count, seconds=30.0) -> list:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if pid_file.exists() and len(_pids(pid_file)) >= count:
            break
        time.sleep(0.1)
    return _pids(pid_file) if pid_file.exists() else []


def test_twelve_grandchildren_and_their_children_all_die_at_the_timeout(tmp_path):
    script, pid_file = _fan_out(tmp_path, children=12, depth=2, then="time.sleep(120)")
    started = time.monotonic()

    job = _job(script, tmp_path, timeout=8)

    assert job.stopped == "timeout"
    assert time.monotonic() - started < 60
    pids = _pids(pid_file)
    assert len(pids) >= 12, "the fan-out did not start in time to be a fair test"
    assert _wait_gone(pids) == []


def test_orphans_whose_parent_already_exited_die_at_the_timeout(tmp_path):
    """The script exits at once; its children were re-parented before the kill."""
    script, pid_file = _fan_out(
        tmp_path, children=4, depth=2, then="time.sleep(3)\nsys.exit(0)"
    )

    job = _job(script, tmp_path, timeout=30)

    pids = _pids(pid_file)
    assert len(pids) >= 4
    assert job.left_running is True
    assert _wait_gone(pids) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows detach flags")
def test_a_detached_grandchild_dies_at_the_timeout(tmp_path):
    pid_file = tmp_path / "pids.txt"
    script = tmp_path / "detach.py"
    script.write_text(textwrap.dedent(f"""
        import subprocess, sys, time
        sleeper = [sys.executable, "-c", "import time; time.sleep(120)"]
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        pids = []
        for extra in (subprocess.CREATE_BREAKAWAY_FROM_JOB, 0):
            try:
                pids.append(subprocess.Popen(sleeper, creationflags=flags | extra).pid)
            except OSError:
                pass
        open({str(pid_file)!r}, "w").write("\\n".join(map(str, pids)))
        time.sleep(120)
        """))

    job = _job(script, tmp_path, timeout=5)

    assert job.stopped == "timeout"
    pids = _pids(pid_file)
    assert pids
    assert _wait_gone(pids) == []


def test_a_script_that_ignores_signals_and_interrupts_is_still_killed(tmp_path):
    pid_file = tmp_path / "pids.txt"
    script = tmp_path / "stubborn.py"
    script.write_text(textwrap.dedent(f"""
        import os, signal, subprocess, sys, time
        for name in ("SIGTERM", "SIGINT", "SIGBREAK", "SIGHUP"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), signal.SIG_IGN)
        child = subprocess.Popen([sys.executable, "-c", (
            "import signal, time\\n"
            "for n in ('SIGTERM', 'SIGINT', 'SIGBREAK', 'SIGHUP'):\\n"
            "    if hasattr(signal, n): signal.signal(getattr(signal, n), signal.SIG_IGN)\\n"
            "while True:\\n"
            "    try: time.sleep(0.1)\\n"
            "    except BaseException: pass\\n"
        )])
        open({str(pid_file)!r}, "w").write(f"{{os.getpid()}}\\n{{child.pid}}\\n")
        while True:
            try:
                time.sleep(0.1)
            except BaseException:
                pass
        """))
    started = time.monotonic()

    job = _job(script, tmp_path, timeout=3)

    assert job.stopped == "timeout"
    assert time.monotonic() - started < 30
    assert _wait_gone(_pids(pid_file)) == []


def test_a_stop_request_reaches_a_script_that_ignores_signals(tmp_path):
    script = tmp_path / "stubborn.py"
    script.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "while True:\n"
        "    try:\n"
        "        time.sleep(0.1)\n"
        "    except BaseException:\n"
        "        pass\n"
    )
    asked = time.monotonic() + 1.0

    job = _job(
        script, tmp_path, timeout=60, should_stop=lambda: time.monotonic() > asked
    )

    assert job.stopped == "cancelled"
    assert time.monotonic() - asked < 20


# ---------------------------------------------------------------------------
# What a killed job wrote is reported, never deleted
# ---------------------------------------------------------------------------


def test_a_job_writing_a_huge_file_is_reported_and_nothing_is_deleted(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    keep = out / "users-own.xlsx"
    keep.write_bytes(b"precious")
    old = time.time() - 3600
    os.utime(keep, (old, old))
    target = out / "export.csv"
    script = tmp_path / "writer.py"
    script.write_text(textwrap.dedent(f"""
        import time
        chunk = ("x" * 1023 + "\\n") * 64
        with open({str(target)!r}, "w") as f:
            while True:
                f.write(chunk)
                f.flush()
                time.sleep(0.01)
        """))
    before = set(os.listdir(out))

    job = _job(script, tmp_path, timeout=3, watch=[out, tmp_path], ignore=[script])
    result = stopped_job_result(job, 3)

    assert job.stopped == "timeout"
    assert str(target) in result["files_modified"]
    assert str(keep) not in result["files_modified"]
    assert str(script) not in result["files_modified"]
    assert "export.csv" in result["error"]
    assert result["status"] == "error" and result["timed_out"] is True
    # Nothing was deleted: the partial file and the user's own file remain.
    assert set(os.listdir(out)) >= before | {"export.csv"}
    assert target.stat().st_size > 0
    assert keep.read_bytes() == b"precious"


def test_a_job_that_deletes_nothing_reports_no_files(tmp_path):
    script = tmp_path / "idle.py"
    script.write_text("import time\ntime.sleep(60)\n")

    job = _job(script, tmp_path, timeout=2, watch=[tmp_path], ignore=[script])

    assert job.stopped == "timeout"
    assert job.written == []
    assert stopped_job_result(job, 2)["files_modified"] == []


# ---------------------------------------------------------------------------
# Two jobs at once never kill each other's processes
# ---------------------------------------------------------------------------


def test_one_jobs_timeout_never_reaches_another_jobs_children(tmp_path):
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    a_script, a_pids = _fan_out(a_dir, children=3, depth=1, then="time.sleep(120)")
    b_pid_file = b_dir / "pids.txt"
    b_script = b_dir / "steady.py"
    b_script.write_text(textwrap.dedent(f"""
        import subprocess, sys, time
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(12)"]
        )
        open({str(b_pid_file)!r}, "w").write(str(child.pid))
        time.sleep(10)
        print("b finished")
        child.wait()
        """))
    results = {}

    def run(name, script, cwd, timeout):
        results[name] = _job(script, cwd, timeout=timeout)

    b = threading.Thread(target=run, args=("b", b_script, b_dir, 60))
    b.start()
    _wait_for_pids(b_pid_file, 1)
    run("a", a_script, a_dir, 4)

    b_child = _pids(b_pid_file)[0]
    assert results["a"].stopped == "timeout"
    assert _wait_gone(_pids(a_pids)) == []
    # A's kill is done; B and its child are still running.
    assert b.is_alive()
    assert _alive(b_child)

    b.join(60)
    assert results["b"].stopped is None
    assert results["b"].run.returncode == 0
    assert "b finished" in results["b"].run.stdout


def test_two_jobs_timing_out_together_each_kill_only_their_own(tmp_path):
    dirs = [tmp_path / "x", tmp_path / "y"]
    for folder in dirs:
        folder.mkdir()
    scripts = [_fan_out(d, children=2, depth=1, then="time.sleep(120)") for d in dirs]
    jobs = [None, None]

    def run(i):
        jobs[i] = _job(scripts[i][0], dirs[i], timeout=4)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    assert [j.stopped for j in jobs] == ["timeout", "timeout"]
    for _script, pid_file in scripts:
        assert _wait_gone(_pids(pid_file)) == []
