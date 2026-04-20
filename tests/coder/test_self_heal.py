#  Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for :mod:`gaia.coder.self_fix.self_heal` (§7.5, §15.8 P8).

classify_failure's LLM call is mocked — we never hit Anthropic from tests.
pause/resume use :func:`gaia.coder.stores.paused_tasks.write_snapshot`
(exercised with tmp_path). restart_self's exit path uses ``exit_fn``
injection so tests can assert the 42 exit code without terminating pytest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.coder.self_fix import self_heal

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_restart_window():
    """Reset the module-level restart-timestamp list between tests."""
    self_heal._reset_restart_window()
    yield
    self_heal._reset_restart_window()


def _valid_response(kind="self-code", confidence=85) -> str:
    return json.dumps(
        {
            "kind": kind,
            "evidence": "Pattern matches fp_812 cache-key collision.",
            "confidence": confidence,
            "suggested_next_action": "Open a self-fix PR.",
        }
    )


# ---------------------------------------------------------------------------
# classify_failure
# ---------------------------------------------------------------------------


def test_classify_failure_happy_path():
    """A well-formed Opus response round-trips into FailureClassification."""

    def client(**_kw):
        return _valid_response("self-code", 85)

    result = self_heal.classify_failure(
        error={
            "message": "KeyError",
            "stack": "...",
            "tool_name": "write_file",
            "tool_args": {"path": "a"},
        },
        context_json={"recent_tool_calls": []},
        dev_mode_on=True,
        client=client,
    )
    assert result.kind == "self-code"
    assert result.confidence == 85
    assert result.escalated_low_confidence is False


def test_classify_failure_low_confidence_escalates_to_external():
    """confidence < 50 AND non-external → force-rewritten to external."""

    def client(**_kw):
        return _valid_response("self-code", 30)

    result = self_heal.classify_failure(
        error={"message": "x", "stack": "", "tool_name": "t", "tool_args": {}},
        context_json={},
        client=client,
    )
    assert result.kind == "external"
    assert result.escalated_low_confidence is True


def test_classify_failure_external_keeps_low_confidence():
    """An already-external classification is NOT rewritten, just passed through."""

    def client(**_kw):
        return _valid_response("external", 30)

    result = self_heal.classify_failure(
        error={"message": "x", "stack": "", "tool_name": "t", "tool_args": {}},
        context_json={},
        client=client,
    )
    assert result.kind == "external"
    assert result.escalated_low_confidence is False


def test_classify_failure_unknown_kind_raises():
    def client(**_kw):
        return json.dumps(
            {
                "kind": "unknown",
                "evidence": "",
                "confidence": 50,
                "suggested_next_action": "",
            }
        )

    with pytest.raises(ValueError, match="unknown kind"):
        self_heal.classify_failure(
            error={"message": "", "stack": "", "tool_name": "t", "tool_args": {}},
            context_json={},
            client=client,
        )


def test_classify_failure_bad_json_raises():
    def client(**_kw):
        return "not json"

    with pytest.raises(ValueError, match="not valid JSON"):
        self_heal.classify_failure(
            error={"message": "", "stack": "", "tool_name": "t", "tool_args": {}},
            context_json={},
            client=client,
        )


def test_classify_failure_prompt_renders_with_substitutions(monkeypatch):
    """The prompt template placeholders ({{error_message}}) get substituted."""
    captured = {}

    def fake_client(*, prompt, **kw):
        captured["prompt"] = prompt
        return _valid_response()

    self_heal.classify_failure(
        error={
            "message": "DivZero",
            "stack": "trace",
            "tool_name": "run",
            "tool_args": {"a": 1},
        },
        context_json={"recent_tool_calls": [{"tool": "x"}]},
        dev_mode_on=True,
        client=fake_client,
    )
    assert "DivZero" in captured["prompt"]
    assert '"a": 1' in captured["prompt"]
    assert "on" in captured["prompt"]  # dev_mode_status
    assert "{{" not in captured["prompt"]  # all placeholders substituted


# ---------------------------------------------------------------------------
# pause_current_task / resume_task
# ---------------------------------------------------------------------------


def test_pause_and_resume_roundtrip(tmp_path: Path):
    """Snapshot content survives round-trip through JSON."""
    paused = self_heal.pause_current_task(
        "task-42",
        "self-bug in classify_failure",
        root=tmp_path,
        cwd=tmp_path,
        tool_call_history=[{"tool": "read_file", "args": {"path": "x"}}],
        partial_outputs={"stdout": "blah"},
        original_prompt="scaffold a weather agent",
    )
    assert paused.path.exists()
    assert paused.task_id == "task-42"

    result = self_heal.resume_task("task-42", root=tmp_path)
    assert result.task_id == "task-42"
    assert result.original_prompt == "scaffold a weather agent"
    assert result.tool_call_history == [{"tool": "read_file", "args": {"path": "x"}}]
    assert result.partial_outputs == {"stdout": "blah"}
    assert paused.path.exists(), "resume_task should not auto-delete by default"


def test_resume_missing_snapshot_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        self_heal.resume_task("never-existed", root=tmp_path)


def test_resume_corrupt_snapshot_raises(tmp_path: Path):
    """Missing required fields = SelfHealError, not a silent downgrade."""
    (tmp_path / "weird.json").write_text(json.dumps({"task_id": "weird"}))
    with pytest.raises(self_heal.SelfHealError, match="missing required fields"):
        self_heal.resume_task("weird", root=tmp_path)


def test_resume_can_delete_snapshot(tmp_path: Path):
    self_heal.pause_current_task("t1", "reason", root=tmp_path, original_prompt="x")
    result = self_heal.resume_task("t1", root=tmp_path, delete_snapshot=True)
    assert result.task_id == "t1"
    assert not (tmp_path / "t1.json").exists()


# ---------------------------------------------------------------------------
# restart_self
# ---------------------------------------------------------------------------


def test_restart_self_cold_exit_code_42():
    """kind='code' → exit_fn called with 42."""
    calls = []
    self_heal.restart_self(
        "code changed",
        kind="code",
        exit_fn=calls.append,
        now=1000.0,
    )
    assert calls == [self_heal.RESTART_EXIT_CODE]


def test_restart_self_hot_reload_prompt_only():
    """kind='prompt-only' does NOT call exit_fn; returns reloaded list."""
    calls = []
    result = self_heal.restart_self(
        "prompt tweak",
        kind="prompt-only",
        exit_fn=calls.append,
        now=1000.0,
    )
    assert calls == []
    assert result.exited is False
    assert result.kind == "prompt-only"


def test_restart_self_rate_limit_triggers_on_fourth():
    """§7.5: > 3 restarts / hour → RestartStormError."""
    calls = []
    for i in range(self_heal._RESTART_MAX_IN_WINDOW):
        self_heal.restart_self(
            f"attempt {i}",
            kind="code",
            exit_fn=calls.append,
            now=1000.0 + i,
        )
    with pytest.raises(self_heal.RestartStormError, match="Refusing to restart"):
        self_heal.restart_self(
            "one too many",
            kind="code",
            exit_fn=calls.append,
            now=1000.0 + self_heal._RESTART_MAX_IN_WINDOW,
        )


def test_restart_self_stale_timestamps_are_purged():
    """Old restarts outside the window do not count against the cap."""
    calls = []
    for i in range(self_heal._RESTART_MAX_IN_WINDOW):
        self_heal.restart_self(
            f"old {i}",
            kind="code",
            exit_fn=calls.append,
            now=0.0 + i,
        )
    # Now jump far outside the window — the cap should reset.
    future = self_heal._RESTART_COUNT_WINDOW + 1000.0
    self_heal.restart_self(
        "fresh",
        kind="code",
        exit_fn=calls.append,
        now=future,
    )
    assert len(calls) == self_heal._RESTART_MAX_IN_WINDOW + 1


def test_restart_self_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown kind"):
        self_heal.restart_self("x", kind="banana", exit_fn=lambda _: None)
