#  Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for :mod:`gaia.coder.tools.debug` (§5.9).

Each of the eight tools has its own unit test plus a smoke test for the
mixin. External subprocess calls (``subprocess.run``) are stubbed via
monkeypatching to keep these tests hermetic and fast.
"""

from __future__ import annotations

import json
import uuid

import pytest

from gaia.coder.stores import memory as memory_store
from gaia.coder.tools import debug as debug_tools

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _FakeCompleted:
    """Stand-in for :class:`subprocess.CompletedProcess`."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# ---------------------------------------------------------------------------
# repro_attempt
# ---------------------------------------------------------------------------


def test_repro_attempt_reproduced_on_all_attempts(monkeypatch):
    """Signature matches every run → reproduced=True."""

    def fake_run(argv, **kw):
        return _FakeCompleted(stdout="KeyError: user_id", returncode=1)

    monkeypatch.setattr(debug_tools, "_run", fake_run)
    result = debug_tools.repro_attempt(
        "pytest tests/", expected_failure_signature="KeyError user_id", attempts=3
    )
    assert result["reproduced"] is True
    assert result["attempts"] == 3
    assert result["attempts_reproduced"] == 3
    assert result["match_score"] > 0.5


def test_repro_attempt_not_reproduced_when_signature_mismatches(monkeypatch):
    def fake_run(argv, **kw):
        return _FakeCompleted(stdout="totally unrelated error", returncode=1)

    monkeypatch.setattr(debug_tools, "_run", fake_run)
    result = debug_tools.repro_attempt("pytest", "KeyError user_id", attempts=3)
    assert result["reproduced"] is False
    assert result["attempts_reproduced"] == 0


def test_repro_attempt_zero_attempts_raises():
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        debug_tools.repro_attempt("cmd", "sig", attempts=0)


# ---------------------------------------------------------------------------
# git_bisect
# ---------------------------------------------------------------------------


def test_git_bisect_parses_first_bad_commit(monkeypatch):
    """The bisect-run output line is parsed into culprit_sha."""

    def fake_runner(argv, **kw):
        if argv[:2] == ["git", "bisect"] and "run" in argv:
            return _FakeCompleted(
                stdout=(
                    "[deadbeefcafe1234567890abcdef1234567890ab] running tests\n"
                    "deadbeefcafe1234567890abcdef1234567890ab is the first bad commit\n"
                ),
                returncode=0,
            )
        return _FakeCompleted(returncode=0)

    result = debug_tools.git_bisect(
        "HEAD~10", "HEAD", "pytest -x", git_runner=fake_runner
    )
    assert result["culprit_sha"] == "deadbeefcafe1234567890abcdef1234567890ab"
    assert "first bad commit" in result["log"]
    assert len(result["tested_refs"]) >= 1


def test_git_bisect_returns_none_on_failure(monkeypatch):
    def fake_runner(argv, **kw):
        if "start" in argv:
            return _FakeCompleted(stderr="not a git repo", returncode=128)
        return _FakeCompleted(returncode=0)

    result = debug_tools.git_bisect("a", "b", "cmd", git_runner=fake_runner)
    assert result["culprit_sha"] is None
    assert result["returncode"] == 128


# ---------------------------------------------------------------------------
# add_instrumented_trace
# ---------------------------------------------------------------------------


def test_add_instrumented_trace_inserts_logger_debug(tmp_path, monkeypatch):
    """The probe adds a logger.debug line at the indicated position."""
    target = tmp_path / "mymod.py"
    target.write_text("def foo():\n    return 1\n")

    calls = {"branch_created": False, "head_resolved": False}

    def fake_run(argv, **kw):
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            calls["head_resolved"] = True
            return _FakeCompleted(stdout="abc123\n", returncode=0)
        if argv[:3] == ["git", "checkout", "-b"]:
            calls["branch_created"] = True
            return _FakeCompleted(returncode=0)
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(debug_tools, "_run", fake_run)
    result = debug_tools.add_instrumented_trace(
        "mymod.py", 2, "probe message", cwd=tmp_path
    )
    text = target.read_text()
    assert "logger.debug" in text
    assert "probe message" in text
    assert calls["head_resolved"] and calls["branch_created"]
    assert result["revert_handle"] == "abc123"
    assert result["branch"].startswith("auto/gaia-coder-probe-")


def test_add_instrumented_trace_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_tools, "_run", lambda *a, **k: _FakeCompleted())
    with pytest.raises(FileNotFoundError):
        debug_tools.add_instrumented_trace("nope.py", 1, "msg", cwd=tmp_path)


# ---------------------------------------------------------------------------
# run_with_tracing
# ---------------------------------------------------------------------------


def test_run_with_tracing_sets_faulthandler(monkeypatch):
    captured_env = {}

    def fake_run(argv, cwd=None, env=None, timeout=0):
        captured_env.update(env or {})
        return _FakeCompleted(stdout="ok", stderr="trace goes here")

    monkeypatch.setattr(debug_tools, "_run", fake_run)
    result = debug_tools.run_with_tracing(
        "python script.py", trace_flags=["python-dev"]
    )
    assert captured_env["PYTHONFAULTHANDLER"] == "1"
    assert captured_env["PYTHONDEVMODE"] == "1"
    assert result["trace_output"] == "trace goes here"


def test_run_with_tracing_prepends_X_dev(monkeypatch):
    captured_argv = []

    def fake_run(argv, cwd=None, env=None, timeout=0):
        captured_argv.extend(argv)
        return _FakeCompleted()

    monkeypatch.setattr(debug_tools, "_run", fake_run)
    debug_tools.run_with_tracing("python foo.py", trace_flags=["python-dev"])
    assert captured_argv[:3] == ["python", "-X", "dev"]


# ---------------------------------------------------------------------------
# diff_behavior
# ---------------------------------------------------------------------------


def test_diff_behavior_returns_unified_diff(monkeypatch, tmp_path):
    """good/bad outputs differ → diff string includes @@ markers."""
    sequence = iter(
        [
            _FakeCompleted(stdout="stashed\n"),  # stash push
            _FakeCompleted(returncode=0),  # switch good
            _FakeCompleted(stdout="pass 42\n", returncode=0),  # good harness
            _FakeCompleted(returncode=0),  # switch bad
            _FakeCompleted(stdout="fail 42\n", returncode=1),  # bad harness
            _FakeCompleted(returncode=0),  # switch -
            _FakeCompleted(returncode=0),  # stash pop
        ]
    )

    def fake_run(argv, **kw):
        return next(sequence)

    monkeypatch.setattr(debug_tools, "_run", fake_run)
    result = debug_tools.diff_behavior("goodref", "badref", "./harness", cwd=tmp_path)
    assert "pass 42" in result["good_output"]
    assert "fail 42" in result["bad_output"]
    assert "@@" in result["diff"]


# ---------------------------------------------------------------------------
# query_failure_patterns
# ---------------------------------------------------------------------------


def test_query_failure_patterns_ranks_by_similarity(tmp_path):
    """Similar signatures rank higher; exact-match beats partial."""
    conn = memory_store.open_store(tmp_path / "memory.db")
    try:
        for i, sig in enumerate(
            ["KeyError user_id missing", "IndexError bounds", "AttributeError"]
        ):
            row = memory_store.MemoryRow(
                id=str(uuid.uuid4()),
                topic="failure_patterns",
                created_at=f"2026-01-0{i + 1}T00:00:00Z",
                source_kind="feedback",
                payload_json=json.dumps(
                    {
                        "error_signature": sig,
                        "stack_hash": f"hash{i}",
                        "root_cause": f"cause{i}",
                        "fix_pr_url": f"https://pr/{i}",
                    }
                ),
                embedding_key=f"hash{i}",
                confidence=80,
            )
            memory_store.insert_row(conn, row)

        hits = debug_tools.query_failure_patterns(
            "KeyError user_id missing", memory_conn=conn, limit=5
        )
    finally:
        conn.close()

    assert hits
    # Exact-match signature should be first.
    assert "cause0" in hits[0]["root_cause"]
    assert hits[0]["similarity"] >= hits[-1]["similarity"]


def test_query_failure_patterns_empty_when_no_rows(tmp_path):
    conn = memory_store.open_store(tmp_path / "memory.db")
    try:
        hits = debug_tools.query_failure_patterns("anything", memory_conn=conn)
    finally:
        conn.close()
    assert hits == []


# ---------------------------------------------------------------------------
# flake_check
# ---------------------------------------------------------------------------


def test_flake_check_detects_flaky_test():
    """3 pass / 2 fail out of 5 → flake_rate=0.4, is_flaky=True."""
    outcomes = iter([0, 1, 0, 1, 0])  # pass, fail, pass, fail, pass

    def runner(fqn, idx, cwd):
        return next(outcomes)

    result = debug_tools.flake_check(
        "tests/test_x.py::test_y", attempts=5, runner=runner
    )
    assert result["passed"] == 3
    assert result["failed"] == 2
    assert result["flake_rate"] == pytest.approx(0.4)
    assert result["is_flaky"] is True


def test_flake_check_all_pass_not_flaky():
    result = debug_tools.flake_check("t", attempts=3, runner=lambda *a: 0)
    assert result["is_flaky"] is False
    assert result["flake_rate"] == 0.0


def test_flake_check_all_fail_not_flaky():
    """All-fail is a real bug, not a flake (mirrors §5.9)."""
    result = debug_tools.flake_check("t", attempts=3, runner=lambda *a: 1)
    assert result["is_flaky"] is False


# ---------------------------------------------------------------------------
# minimize_repro
# ---------------------------------------------------------------------------


def test_minimize_repro_halves_input():
    """A deterministic reproducer narrows to a small prefix."""

    def reproducer(candidate: str) -> bool:
        # "B" appears exactly once in the original; each successful halving
        # MUST preserve it for the binary search to make progress.
        return "B" in candidate

    result = debug_tools.minimize_repro(
        "cmd", "xxxxxxxxxxxxxxxxB", reproducer=reproducer
    )
    assert "B" in result["minimized"]
    assert result["minimized_length"] < result["original_length"]
    assert result["iterations"] >= 1


def test_minimize_repro_refuses_non_reproducer():
    with pytest.raises(ValueError, match="does not reproduce"):
        debug_tools.minimize_repro("cmd", "nothing-matches", reproducer=lambda c: False)


def test_minimize_repro_empty_input():
    result = debug_tools.minimize_repro("cmd", "", reproducer=lambda c: True)
    assert result["minimized"] == ""
    assert result["iterations"] == 0


# ---------------------------------------------------------------------------
# Mixin smoke test
# ---------------------------------------------------------------------------


def test_debug_tools_mixin_registers_all_eight():
    """register_debug_tools returns exactly the eight §5.9 tool names."""

    class _Agent(debug_tools.DebugToolsMixin):
        pass

    agent = _Agent()
    registered = agent.register_debug_tools()
    assert set(registered) == {
        "repro_attempt",
        "git_bisect",
        "add_instrumented_trace",
        "run_with_tracing",
        "diff_behavior",
        "query_failure_patterns",
        "flake_check",
        "minimize_repro",
    }
    assert len(registered) == 8
