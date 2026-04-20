# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for :mod:`gaia.eval.runners.coder_cli`.

These tests spawn real ``python -m gaia.coder.cli daemon`` subprocesses
against a temporary sandbox directory. They are fast (the stub daemon
completes each task in ~200 ms) and deterministic.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gaia.coder.cli import ARTIFACT_FILENAMES
from gaia.eval.runners.coder_cli import (
    AgentHandle,
    CoderCLIRunner,
    TaskResult,
    in_tree_runner,
)

TASK_BODY = """---
id: smoke-task
title: Smoke test task
expected_fix_class: tool
max_diff_loc: 10
max_wall_clock_min: 1
scoring:
  - name: compiles
    check: "true"
    weight: 1.0
---

# Body

Minimal task for runner tests.
"""


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """Return a fresh sandbox directory for each test."""
    # tmp_path is already unique per test — use as-is.
    return tmp_path


@pytest.fixture
def task_md(tmp_path: Path) -> Path:
    path = tmp_path / "task.md"
    path.write_text(TASK_BODY, encoding="utf-8")
    return path


@pytest.fixture
def runner() -> CoderCLIRunner:
    """A CoderCLIRunner that invokes the in-tree module.

    Avoids depending on the installed ``gaia-coder`` shim — tests must
    exercise the worktree source.
    """
    return in_tree_runner()


# ---------------------------------------------------------------------------
# Core round-trip tests (required by the Phase 2 acceptance criteria).
# ---------------------------------------------------------------------------


def test_runner_spawn_and_stop(runner: CoderCLIRunner, sandbox: Path) -> None:
    """Runner spawns daemon, stops it, process is reaped cleanly."""
    handle = runner.spawn_agent(sandbox, tier=0)
    assert isinstance(handle, AgentHandle)
    assert handle.pid > 0
    # The pid file should exist while the daemon is alive.
    pid_file = sandbox / ".eval-artifacts" / ".daemon.pid"
    assert pid_file.exists(), f"expected pid file at {pid_file}"
    recorded_pid = int(pid_file.read_text(encoding="utf-8").strip())
    assert recorded_pid == handle.pid

    rc = runner.stop_agent(handle)
    assert rc == 0, f"daemon exited with rc={rc}"
    assert handle.process.poll() is not None, "daemon process should be reaped"
    # Daemon should have cleaned up its pid file on exit.
    assert not pid_file.exists(), "daemon did not clean up pid file"


def test_runner_reads_task_from_stdin(
    runner: CoderCLIRunner, sandbox: Path, task_md: Path
) -> None:
    """`ask -` reads body from stdin and returns the front-matter id."""
    handle = runner.spawn_agent(sandbox, tier=0)
    try:
        task_id = runner.send_task(handle, task_md)
        assert (
            task_id == "smoke-task"
        ), f"expected task_id from front-matter 'id:' field, got {task_id!r}"
    finally:
        runner.stop_agent(handle)


def test_runner_collects_all_6_artifacts(
    runner: CoderCLIRunner, sandbox: Path, task_md: Path
) -> None:
    """After the stub daemon completes, all 6 artifact files exist."""
    handle = runner.spawn_agent(sandbox, tier=0)
    try:
        task_id = runner.send_task(handle, task_md)
        result = runner.wait_for_completion(handle, task_id, timeout_min=1)
        assert result.completed, f"daemon did not complete: {result}"
        artifacts = runner.collect_artifacts(sandbox, task_id)
    finally:
        runner.stop_agent(handle)

    # Every filename from the canonical list is present.
    assert set(artifacts.keys()) == set(ARTIFACT_FILENAMES)
    missing = [name for name, p in artifacts.items() if not p.exists()]
    assert not missing, f"missing artifact file(s): {missing}"

    # Spot-check that pass_results.json is the stub payload.
    pass_results = json.loads(artifacts["pass_results.json"].read_text())
    assert pass_results["stub"] is True
    assert pass_results["task_id"] == "smoke-task"


def test_run_one_convenience(
    runner: CoderCLIRunner, sandbox: Path, task_md: Path
) -> None:
    """``run_one`` spawns, runs, collects, and tears down in a single call."""
    result, artifacts = runner.run_one(
        sandbox=sandbox, task_md_path=task_md, tier=0, timeout_min=1
    )
    assert isinstance(result, TaskResult)
    assert result.completed, f"run_one result not completed: {result}"
    assert result.task_id == "smoke-task"
    assert result.elapsed_s >= 0.0
    assert result.elapsed_s < 60.0  # stub should be near-instant
    # No daemon left running.
    pid_file = sandbox / ".eval-artifacts" / ".daemon.pid"
    assert not pid_file.exists(), "run_one left a live daemon behind"
    # All artifacts present.
    for name in ARTIFACT_FILENAMES:
        assert artifacts[name].exists(), f"missing artifact: {name}"


def test_wait_timeout_returns_124(runner: CoderCLIRunner, sandbox: Path) -> None:
    """If no task is ever sent, `wait` times out with returncode 124.

    Uses a sub-minute timeout (``0.05 min`` = 3 s) so the test is
    fast. The CLI accepts float minutes specifically for this use
    case — see ``gaia.coder.cli._handle_wait``.
    """
    handle = runner.spawn_agent(sandbox, tier=0)
    try:
        start = time.monotonic()
        result = runner.wait_for_completion(
            handle, task_id="never-arrives", timeout_min=0.05
        )
        elapsed = time.monotonic() - start
    finally:
        runner.stop_agent(handle)

    assert not result.completed, "wait should have timed out"
    assert result.timed_out is True
    assert (
        result.wait_returncode == 124
    ), f"expected rc=124 for timeout, got {result.wait_returncode}"
    # Sanity: the wait returned in well under the 60-s-per-min
    # budget because we passed 0.05 min = 3 s.
    assert elapsed < 15.0, f"timeout overran ({elapsed:.1f}s)"


def test_stop_is_idempotent(runner: CoderCLIRunner, sandbox: Path) -> None:
    """`stop_agent` works even when no daemon is running."""
    handle = runner.spawn_agent(sandbox, tier=0)
    runner.stop_agent(handle)
    # Second stop on the same handle should not raise — the underlying
    # ``gaia-coder stop`` subcommand is idempotent.
    runner.stop_agent(handle)


def test_no_network_writes_flag_propagates(
    runner: CoderCLIRunner, sandbox: Path, task_md: Path
) -> None:
    """The --no-network-writes flag lands in the daemon's trace.

    This is the single most important eval invariant: §10.2 says the
    harness MUST forbid network writes. If the flag isn't plumbed
    through, a future real daemon could silently open a real PR
    during eval. The stub daemon echoes the flag into every trace so
    we can verify plumbing end-to-end.
    """
    result, artifacts = runner.run_one(
        sandbox=sandbox,
        task_md_path=task_md,
        tier=0,
        no_network_writes=True,
        timeout_min=1,
    )
    assert result.completed
    trace_line = artifacts["trace.jsonl"].read_text(encoding="utf-8").strip()
    event = json.loads(trace_line)
    assert (
        event["options"]["no_network_writes"] is True
    ), f"--no-network-writes flag was lost; trace options={event['options']}"
    assert event["options"]["stub"] is True
