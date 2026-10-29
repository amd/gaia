# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What happens on the backend around one eval scenario (#5296, #5297).

Each test runs ``run_scripted_scenario`` for real: the driver is a separate
process (``_fake_claude.py``) behind a ``claude`` shim, talking HTTP to a fake
Agent UI backend that keeps the product's behaviour where it matters — a
permission prompt waits until it is answered, and an abandoned turn keeps
running and holds the chat slot.
"""

import json
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

from gaia.eval import runner

from ._fake_agent_ui import FakeAgentUI

FAKE_CLAUDE = Path(__file__).with_name("_fake_claude.py")

# The fake backend is a real HTTP server on 127.0.0.1.
pytestmark = pytest.mark.allow_network


def _scenario(sid, **setup):
    return {
        "id": sid,
        "category": "error_recovery",
        "persona": "casual_user",
        "setup": {"index_documents": [], **setup},
        "turns": [{"turn": 1, "objective": "x", "success_criteria": "ok"}],
    }


@pytest.fixture
def claude_shim(tmp_path, monkeypatch):
    """Put a ``claude`` that runs ``_fake_claude.py`` where the runner looks."""
    if os.name == "nt":
        shim = tmp_path / "claude.cmd"
        shim.write_text(f'@"{sys.executable}" "{FAKE_CLAUDE}" %*\n')
    else:
        shim = tmp_path / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLAUDE}" "$@"\n')
        shim.chmod(0o755)
    real_which = runner.shutil.which
    monkeypatch.setattr(
        runner.shutil,
        "which",
        lambda name, *a, **kw: str(shim) if name == "claude" else real_which(name),
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return shim


@pytest.fixture
def backend():
    fake = FakeAgentUI()
    url = fake.start()
    fake.url = url
    yield fake
    fake.stop()


def _run(fake, tmp_path, monkeypatch, scenario, plan, timeout):
    monkeypatch.setenv(
        "FAKE_CLAUDE_PLAN", json.dumps({"scenario_id": scenario["id"], **plan})
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    start = time.monotonic()
    result = runner.run_scripted_scenario(
        tmp_path / "scenario.yaml",
        scenario,
        run_dir,
        fake.url,
        "test-model",
        "1.00",
        timeout,
    )
    return result, time.monotonic() - start


# ── #5296: prompts are answered at once, under the scenario's policy ──────────


def test_a_path_prompt_is_denied_at_once_instead_of_waiting_out_the_turn(
    backend, claude_shim, tmp_path, monkeypatch
):
    scenario = _scenario("file_not_found")
    result, elapsed = _run(
        backend,
        tmp_path,
        monkeypatch,
        scenario,
        {"messages": ["prompt request_path_access C:/projects/q.pdf"]},
        timeout=20,
    )

    assert result["status"] == "PASS", result.get("error")
    assert elapsed < 15
    assert backend.answers == [
        {
            "session_id": result["session_id"],
            "tool": "request_path_access",
            "approved": False,
        }
    ]
    (prompt,) = result["prompts"]
    assert prompt["tool"] == "request_path_access"
    assert prompt["decision"] == "deny"
    assert prompt["delivered"] is True
    assert "C:/projects/q.pdf" in prompt["args_summary"]
    assert prompt["waited_s"] < 10
    assert result["prompt_counts"] == {"request_path_access: deny": 1}
    # The scorecard reads the trace, so the record has to be in it.
    trace = json.loads(
        (tmp_path / "run" / "traces" / "file_not_found.json").read_text()
    )
    assert trace["prompts"] == result["prompts"]


def test_a_scenario_that_says_the_user_approves_a_tool_gets_it_approved(
    backend, claude_shim, tmp_path, monkeypatch
):
    scenario = _scenario("save_notes", approve_prompts=["write_file"])
    result, _ = _run(
        backend,
        tmp_path,
        monkeypatch,
        scenario,
        {"messages": ["prompt write_file notes.txt"]},
        timeout=20,
    )

    assert result["status"] == "PASS", result.get("error")
    assert backend.answers[0]["approved"] is True
    assert result["prompts"][0]["decision"] == "allow_once"


def test_a_prompt_nobody_could_answer_is_an_infra_error_naming_the_tool(
    claude_shim, tmp_path, monkeypatch
):
    from gaia.eval import scenario_backend

    fake = FakeAgentUI(confirm_wait_s=4, confirm_status=500)
    fake.url = fake.start()
    monkeypatch.setattr(scenario_backend, "PROMPT_STUCK_S", 2)
    try:
        result, _ = _run(
            fake,
            tmp_path,
            monkeypatch,
            _scenario("csv_analysis"),
            {"messages": ["prompt run_python print(1)"]},
            timeout=20,
        )
    finally:
        fake.stop()

    assert result["status"] == "INFRA_ERROR"
    assert "run_python" in result["error"]
    assert result["prompts"][0]["delivered"] is False


def test_a_chat_that_is_not_the_evals_is_never_answered_or_stopped(
    claude_shim, tmp_path, monkeypatch
):
    import threading

    import requests

    # Its own chat slot, so the person's turn cannot block the scenario.
    fake = FakeAgentUI()
    fake.slot = threading.Semaphore(2)
    fake.url = fake.start()
    mine = requests.post(f"{fake.url}/api/sessions", json={"title": "My chat"}).json()
    threading.Thread(
        target=lambda: requests.post(
            f"{fake.url}/api/chat/send",
            json={"session_id": mine["id"], "message": "prompt write_file a.txt"},
            timeout=60,
        ),
        daemon=True,
    ).start()
    try:
        result, _ = _run(
            fake,
            tmp_path,
            monkeypatch,
            _scenario("other_chat"),
            {"messages": ["hi"]},
            timeout=20,
        )
        assert result["status"] == "PASS", result.get("error")
        assert result["prompts"] == []
        assert fake.answers == []
        assert mine["id"] in fake.active()
        assert fake.cancels == []
    finally:
        fake.stop()


@pytest.mark.parametrize(
    "value", ["write_file", [""], [3], [{"tool": "write_file"}]], ids=repr
)
def test_validation_rejects_a_malformed_approve_list(tmp_path, value):
    data = _scenario("s", approve_prompts=value)
    with pytest.raises(ValueError, match="approve_prompts"):
        runner.validate_scenario(tmp_path / "s.yaml", data)


def test_validation_accepts_a_list_of_tool_names(tmp_path):
    runner.validate_scenario(
        tmp_path / "s.yaml", _scenario("s", approve_prompts=["write_file"])
    )


# ── #5297: a timed-out scenario leaves nothing behind ─────────────────────────


def test_a_timed_out_turn_is_stopped_and_the_next_scenario_starts_at_once(
    backend, claude_shim, tmp_path, monkeypatch
):
    first, _ = _run(
        backend,
        tmp_path,
        monkeypatch,
        _scenario("search_slow"),
        {"messages": ["hello", "hang"], "then": "sleep"},
        timeout=6,
    )
    assert first["status"] == "TIMEOUT"
    assert backend.active() == []
    assert len(backend.cancels) == 1

    second, elapsed = _run(
        backend,
        tmp_path,
        monkeypatch,
        _scenario("search_next"),
        {"messages": ["hi"]},
        timeout=20,
    )
    assert second["status"] == "PASS", second.get("error")
    assert elapsed < 10
    assert backend.busy_rejections == 0


def test_a_timeout_keeps_the_turns_that_finished(
    backend, claude_shim, tmp_path, monkeypatch
):
    result, _ = _run(
        backend,
        tmp_path,
        monkeypatch,
        _scenario("search_slow"),
        {"messages": ["hello", "hang"], "then": "sleep"},
        timeout=6,
    )

    assert result["status"] == "TIMEOUT"
    assert result["overall_score"] is None
    turns = result["turns"]
    assert [t["user_message"] for t in turns] == ["hello", "hang"]
    assert turns[0]["agent_response"] == "echo: hello"
    assert turns[1]["agent_tools"] == ["search_file"]
    assert all(t["judged"] is False for t in turns)
    assert result["last_tool"] == "search_file"


def test_the_timeout_fires_on_time_and_kills_the_drivers_children(
    backend, claude_shim, tmp_path, monkeypatch
):
    pidfile = tmp_path / "grandchild.pid"
    result, elapsed = _run(
        backend,
        tmp_path,
        monkeypatch,
        _scenario("slow_driver"),
        {"then": "sleep", "sleep_s": 40, "grandchild_pidfile": str(pidfile)},
        timeout=3,
    )

    assert result["status"] == "TIMEOUT"
    assert elapsed < 20
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.2)
    assert not _alive(pid), "the driver's child outlived the timeout"


def test_a_backend_that_will_not_go_idle_stops_the_run_with_a_named_error(
    claude_shim, tmp_path, monkeypatch
):
    from gaia.eval import scenario_backend

    fake = FakeAgentUI()
    fake.url = fake.start()
    monkeypatch.setattr(scenario_backend, "SETTLE_TIMEOUT_S", 2)
    # The backend accepts the cancel but the turn never notices it.
    monkeypatch.setattr(
        fake,
        "_execute",
        lambda run, message: time.sleep(30),
    )
    try:
        result, _ = _run(
            fake,
            tmp_path,
            monkeypatch,
            _scenario("stuck"),
            {"messages": ["hang"]},
            timeout=4,
        )
    finally:
        fake.stop()

    assert result["status"] == "TIMEOUT"
    assert "Eval: stuck" in result["backend_not_idle"]


def _alive(pid):
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
