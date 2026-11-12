# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""First-run setup: ``gaia init --check`` / ``gaia init`` driven from the Agent UI.

No real ``gaia init`` runs: ``subprocess.run`` / ``subprocess.Popen`` are
replaced with fakes that assert the argv the runner would have used.
"""

import io
import json
import subprocess
import sys
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.llm import lemonade_choice, lemonade_client, lemonade_launcher
from gaia.ui import setup_runner as sr
from gaia.ui.database import ChatDatabase
from gaia.ui.routers import setup as setup_router

_REAL_SERVER_PROBLEM = sr.server_problem


def _local_chat(run):
    """The model the app's chat opens on, for runs that don't test that choice."""
    return "Gemma-4-E4B-it-GGUF"


@pytest.fixture(autouse=True)
def gaia_home(tmp_path, monkeypatch):
    home = tmp_path / "gaia-home"
    monkeypatch.setenv("GAIA_HOME", str(home))
    # GAIA's own Lemonade unless a test says the user chose theirs.
    monkeypatch.setattr(sr, "users_lemonade_url", lambda: None)
    monkeypatch.setattr(setup_router, "users_lemonade_url", lambda: None)
    # No real server is probed when a run fails; tests that need one set it.
    monkeypatch.setattr(sr, "server_problem", lambda: None)
    return home


# ── argv ───────────────────────────────────────────────────────────────────


def test_check_args():
    assert sr.check_args(False, False) == [
        "init",
        "--check",
        "--profile",
        "gaia",
        "--json",
    ]
    assert sr.check_args(True, True)[-2:] == ["--skip-chat-model", "--load"]


def test_run_args_never_prompt_or_rebuild_the_running_ui():
    args = sr.run_args(False)
    assert args[:3] == ["init", "--profile", "gaia"]
    assert "--yes" in args and "--skip-webui-build" in args
    # The app ships its packages; its setup never pip-installs.
    assert "--skip-pip-extras" in args
    assert "--skip-chat-model" not in args
    assert sr.run_args(True)[-1] == "--skip-chat-model"


def test_run_command_is_what_a_person_would_type():
    assert sr.run_command(False) == "gaia init --profile gaia"
    assert sr.run_command(True) == "gaia init --profile gaia --skip-chat-model"
    assert sr.run_command(False, True) == "gaia init --profile gaia --gaia-lemonade"


def test_run_args_switch_to_gaias_own_server():
    assert sr.run_args(False, gaia_lemonade=True)[-1] == "--gaia-lemonade"
    assert "--gaia-lemonade" not in sr.run_args(False)


def test_run_args_server_only_downloads_nothing():
    args = sr.run_args(False, server_only=True)
    assert "--server-only" in args and "--download-model" not in args
    assert "--skip-models" not in sr.run_args(False)


def test_run_args_upgrade_replaces_only_the_embedded_server():
    args = sr.run_args(False, upgrade_embedded=True)
    assert "--server-only" in args
    assert "--skip-models" not in args
    assert "--force-reinstall" not in args
    assert "--download-model" not in args


def test_run_args_pull_each_ticked_extra_model():
    args = sr.run_args(False, extra_models=["Qwen3-VL-4B-Instruct-GGUF", "gpt-oss-20b"])
    assert args[-4:] == [
        "--download-model",
        "Qwen3-VL-4B-Instruct-GGUF",
        "--download-model",
        "gpt-oss-20b",
    ]


# ── parse_status ───────────────────────────────────────────────────────────


def test_parse_status_reads_the_last_json_line_after_logs():
    out = (
        "[2026-09-28 23:28:11] | INFO | x | y\n"
        '{"ready": false, "stage": "early"}\n'
        "some banner\n"
        '{"ready": true}\n'
    )
    assert sr.parse_status(out) == {"ready": True}


def test_parse_status_defaults_the_stage_when_not_ready():
    assert sr.parse_status('{"ready": false, "reasons": ["x"]}')["stage"] == "setup"
    assert sr.parse_status('{"ready": false, "stage": "models"}')["stage"] == "models"


def test_parse_status_unreadable_json_is_an_error():
    with pytest.raises(sr.SetupError, match="unreadable JSON"):
        sr.parse_status("{not json")


def test_parse_status_no_json_names_the_last_line():
    with pytest.raises(sr.SetupError, match="Traceback here"):
        sr.parse_status("hello\nTraceback here\n\n")


# ── check ──────────────────────────────────────────────────────────────────


class _CheckProc:
    """A ``gaia init --check`` child: answers once, records its wait in *kwargs*."""

    def __init__(self, kwargs, returncode, stdout, stderr, times_out=False):
        self._kwargs, self.returncode = kwargs, returncode
        self._out, self._times_out, self.killed = (stdout, stderr), times_out, False

    def communicate(self, timeout=None):
        if timeout is not None:
            self._kwargs["timeout"] = timeout
        if self._times_out and not self.killed:
            raise subprocess.TimeoutExpired("gaia", timeout)
        return self._out

    def kill(self):
        self.killed = True

    def poll(self):
        return None if not self.killed else -9


def _fake_run(
    monkeypatch, returncode, stdout="", stderr="", calls=None, times_out=False
):
    def popen(argv, **kwargs):
        if calls is not None:
            calls.append((argv, kwargs))
        return _CheckProc(kwargs, returncode, stdout, stderr, times_out)

    monkeypatch.setattr(sr.subprocess, "Popen", popen)


def test_check_ready_marks_initialized(monkeypatch):
    calls = []
    _fake_run(monkeypatch, 0, '{"ready": true}\n', calls=calls)
    assert not sr.is_initialized()
    assert sr.check() == {"ready": True}
    assert sr.is_initialized()
    argv, kwargs = calls[0]
    assert argv[:3] == [sys.executable, "-m", "gaia.cli"]
    assert argv[3:] == sr.check_args(False, False)
    assert kwargs["timeout"] == sr.CHECK_TIMEOUT_S


def test_check_with_load_waits_for_the_model_load(monkeypatch):
    calls = []
    _fake_run(monkeypatch, 0, '{"ready": true}', calls=calls)
    sr.check(skip_chat_model=True, load=True)
    argv, kwargs = calls[0]
    assert "--load" in argv and "--skip-chat-model" in argv
    assert kwargs["timeout"] == sr.VERIFY_TIMEOUT_S


@pytest.mark.parametrize("code", [1, 3])
def test_check_not_ready_exit_codes_are_answers(monkeypatch, code):
    _fake_run(monkeypatch, code, '{"ready": false, "reasons": ["no model"]}')
    status = sr.check()
    assert status["ready"] is False
    assert status["stage"] == "setup"
    assert not sr.is_initialized()


def test_check_unexpected_exit_is_an_error_not_not_ready(monkeypatch):
    _fake_run(monkeypatch, 2, stdout="", stderr="ModuleNotFoundError: gaia.cli")
    with pytest.raises(sr.SetupError, match=r"exit 2.*ModuleNotFoundError"):
        sr.check()


def test_check_timeout_is_an_error_and_stops_the_check(monkeypatch):
    calls = []
    _fake_run(monkeypatch, 0, calls=calls, times_out=True)
    procs = []
    with pytest.raises(sr.SetupError, match="took too long"):
        sr.check(on_start=procs.append)
    assert procs[0].killed


def test_check_hands_its_process_to_the_caller(monkeypatch):
    _fake_run(monkeypatch, 0, '{"ready": true}')
    procs = []
    sr.check(on_start=procs.append)
    assert len(procs) == 1


# ── marker ─────────────────────────────────────────────────────────────────


def _desktop_state(gaia_home, text):
    gaia_home.mkdir(parents=True, exist_ok=True)
    (gaia_home / sr.DESKTOP_INSTALL_STATE).write_text(text, encoding="utf-8")


def test_desktop_setup_error_is_none_without_a_desktop_install(gaia_home):
    assert sr.desktop_setup_error() is None


@pytest.mark.parametrize("init", ["done", "skipped", None])
def test_desktop_setup_error_is_none_when_init_did_not_fail(gaia_home, init):
    _desktop_state(gaia_home, json.dumps({"state": "ready", "init": init}))
    assert sr.desktop_setup_error() is None


def test_desktop_setup_error_is_the_reason_init_gave(gaia_home):
    reason = "Gemma-4-E4B-it-GGUF - Not enough free memory to load it."
    _desktop_state(
        gaia_home,
        json.dumps({"state": "ready", "init": "failed", "initError": reason}),
    )
    assert sr.desktop_setup_error() == reason


def test_desktop_setup_error_without_a_reason_points_at_the_log(gaia_home):
    _desktop_state(gaia_home, json.dumps({"state": "ready", "init": "failed"}))
    error = sr.desktop_setup_error()
    assert "setup did not finish" in error and "gaia init" not in error
    assert "electron-install.log" in error


def test_an_unreadable_desktop_setup_record_is_reported(gaia_home):
    _desktop_state(gaia_home, "{not json")
    assert sr.DESKTOP_INSTALL_STATE in sr.desktop_setup_error()


def test_marker_lives_under_gaia_home(gaia_home):
    assert not sr.is_initialized()
    sr.mark_initialized()
    assert (gaia_home / "chat" / "initialized").exists()
    assert sr.is_initialized()


# ── describe (mirrors tui/internal/gaiainit/verify_test.go) ────────────────


@pytest.mark.parametrize(
    "line",
    [
        "[2026-09-28 23:28:11] | INFO | gaia.llm.lemonade_embedded._download | "
        "lemonade_embedded.py:486 | Downloading",
        "[2026-09-28 23:28:40] | WARNING | gaia.llm.lemonade_client."
        "_post_load_with_transient_retry | x",
        "┌──────────────┐",
        "   Ensuring 1 model(s) are downloaded:",
        "",
    ],
)
def test_describe_keeps_log_records_off_the_screen(line):
    assert sr.describe(line) is None


@pytest.mark.parametrize(
    "line,phase,text",
    [
        (
            "Step 1/5: Starting Lemonade Server...",
            sr.PHASE_SERVER,
            "Starting the local model server",
        ),
        (
            "   Downloading Lemonade Server v2026.39.1...",
            sr.PHASE_SERVER,
            "Downloading the local model server",
        ),
        (
            "Step 2/5: Downloading models for 'gaia' profile...",
            sr.PHASE_MODELS,
            "Downloading the models",
        ),
        (
            "   Downloading: user.embeddinggemma-300m-GGUF",
            sr.PHASE_MODELS,
            "Downloading user.embeddinggemma-300m-GGUF",
        ),
        (
            "Step 5/5: Verifying setup...",
            sr.PHASE_FINISH,
            "Checking that the models load",
        ),
        (
            "Step 3/5: Installing Python dependencies...",
            sr.PHASE_FINISH,
            "Installing Python packages",
        ),
        (
            "Step 4/5: Checking agent installation...",
            sr.PHASE_FINISH,
            "Checking the GAIA agent",
        ),
        ("Step 4/5: Something new...", sr.PHASE_FINISH, "Something new"),
    ],
)
def test_describe_names_each_phase(line, phase, text):
    progress = sr.describe(line)
    assert progress is not None
    assert (progress.phase, progress.text) == (phase, text)


def test_describe_progress_bar_and_failure():
    bar = sr.describe("   [========------------] 42% (2.1 MB/5.1 MB)")
    assert bar is not None and bar.percent == 42 and bar.phase is None
    failed = sr.describe("   ❌ user.embeddinggemma-300m-GGUF - Request failed")
    assert failed is not None and failed.failed
    assert failed.text == "user.embeddinggemma-300m-GGUF - Request failed"


@pytest.mark.parametrize(
    "line,size",
    [
        # The model pull's bar (AgentConsole.print_download_progress).
        (
            "   [━━━━━━─────] 45%  1.20 GB / 2.50 GB @ 30 MB/s",
            "1.20 GB of 2.50 GB",
        ),
        # The server download's bar (InitCommand._download_progress).
        ("   [========------------] 42% (2.1 MB/5.1 MB)", "2.1 MB of 5.1 MB"),
        ("   [====] 7%", None),
    ],
)
def test_describe_reads_the_download_size(line, size):
    progress = sr.describe(line)
    assert progress is not None and progress.size == size


def test_setup_output_removes_ansi_codes_from_the_model_and_details_log():
    run = sr.SetupRun(False)
    runner = sr.SetupRunner()

    runner._consume(run, "\x1b[1;34mStep 1/5: Starting Lemonade Server...\x1b[0m")
    runner._consume(
        run,
        "   Chat model: Gemma-\x1b[1;36m4\x1b[0m-E4B-it-GGUF (default_model)",
    )

    assert run.chat_model == "Gemma-4-E4B-it-GGUF"
    assert run.log == [
        "Step 1/5: Starting Lemonade Server...",
        "Chat model: Gemma-4-E4B-it-GGUF (default_model)",
    ]


def test_describe_reads_the_chat_model_setup_chose():
    progress = sr.describe(
        "   Chat model: Qwen3.6-35B-A3B-GGUF — this PC has 32 GB for models (AMD iGPU)"
    )
    assert progress is not None and progress.chat_model == "Qwen3.6-35B-A3B-GGUF"
    assert progress.phase is None and not progress.text
    mine = sr.describe(
        "   Chat model: Gemma-4-E4B-it-GGUF (default_model in ~/.gaia/config.json)"
    )
    assert mine is not None and mine.chat_model == "Gemma-4-E4B-it-GGUF"


# ── SetupRun.view ──────────────────────────────────────────────────────────


def _statuses(run):
    return [s["status"] for s in run.view()["steps"]]


def test_view_before_any_step_is_reported():
    assert _statuses(sr.SetupRun(False)) == ["active", "pending", "pending"]


def test_view_marks_steps_before_the_active_one_done():
    run = sr.SetupRun(False, phase=sr.PHASE_MODELS)
    assert _statuses(run) == ["done", "active", "pending"]


def test_view_fails_only_the_step_that_was_reached():
    run = sr.SetupRun(False, phase=sr.PHASE_MODELS, state="failed")
    assert _statuses(run) == ["done", "failed", "pending"]
    early = sr.SetupRun(False, state="failed")
    assert _statuses(early) == ["failed", "pending", "pending"]


def test_view_cancelled():
    run = sr.SetupRun(False, phase=sr.PHASE_SERVER, state="cancelled")
    assert _statuses(run) == ["cancelled", "pending", "pending"]


def test_view_ready_is_all_done():
    run = sr.SetupRun(False, phase=sr.PHASE_SERVER, state="ready")
    assert _statuses(run) == ["done", "done", "done"]


def test_view_carries_the_command_and_a_bounded_log():
    run = sr.SetupRun(True, log=[str(i) for i in range(100)])
    view = run.view()
    assert view["command"] == "gaia init --profile gaia --skip-chat-model"
    assert view["log_tail"] == [str(i) for i in range(60, 100)]
    assert "under 1 GB" in view["steps"][1]["detail"]


def test_steps_never_promise_a_download_size_setup_has_not_chosen():
    steps = sr.step_labels(False)
    assert "6 GB" not in steps[1]["detail"]
    assert "fits this PC" in steps[1]["detail"]


def test_view_names_the_chat_model_once_setup_picks_it():
    run = sr.SetupRun(False, phase=sr.PHASE_MODELS, chat_model="Qwen3.6-35B-A3B-GGUF")
    view = run.view()
    assert view["chat_model"] == "Qwen3.6-35B-A3B-GGUF"
    assert "Qwen3.6-35B-A3B-GGUF" in view["steps"][1]["detail"]


def test_server_only_lists_just_the_server_step():
    assert [s["key"] for s in sr.step_labels(False, server_only=True)] == ["server"]
    mine = sr.step_labels(False, users_server="http://h:1/api/v1", server_only=True)
    assert mine == [
        {
            "key": "server",
            "label": "Use your Lemonade Server",
            "detail": "http://h:1/api/v1",
        }
    ]


def test_use_mine_replaces_the_install_step():
    steps = sr.step_labels(False, users_server="http://localhost:13305/api/v1")
    assert steps[0]["label"] == "Use your Lemonade Server"
    assert steps[0]["detail"] == "http://localhost:13305/api/v1"
    assert all("Install" not in s["label"] for s in steps)


# ── SetupRunner ────────────────────────────────────────────────────────────


class _BlockingStdout:
    """stdout of a process that prints *lines*, then waits until killed."""

    def __init__(self, lines=b""):
        self._pending = lines
        self.killed = threading.Event()

    def read1(self, _n):
        if self._pending:
            chunk, self._pending = self._pending, b""
            return chunk
        self.killed.wait(5)
        return b""


class _FakeProc:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.killed = False

    def kill(self):
        self.killed = True
        if isinstance(self.stdout, _BlockingStdout):
            self.stdout.killed.set()

    def poll(self):
        return -9 if self.killed else self.returncode

    def wait(self, timeout=None):
        return -9 if self.killed else self.returncode


def _popen(monkeypatch, proc, calls=None):
    def popen(argv, **kwargs):
        if calls is not None:
            calls.append((argv, kwargs))
        return proc

    monkeypatch.setattr(sr.subprocess, "Popen", popen)


def _wait_done(runner, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        view = runner.status()
        if view and view["state"] not in ("running", "verifying"):
            return view
        time.sleep(0.02)
    raise AssertionError(f"setup did not finish: {runner.status()}")


_OUTPUT = (
    b"Step 1/5: Starting Lemonade Server...\n"
    b"   [==--] 10% (1 MB/5 MB)\r   [====] 100% (5 MB/5 MB)\n"
    b"Step 2/5: Downloading models for 'gaia' profile...\n"
    b"   Downloading: user.embeddinggemma-300m-GGUF\n"
)


def test_a_clean_run_is_verified_then_ready(monkeypatch):
    calls, checks = [], []
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)), calls)

    def check(skip_chat_model, load, **_k):
        checks.append((skip_chat_model, load))
        return {"ready": True}

    monkeypatch.setattr(sr, "check", check)
    runner = sr.SetupRunner()
    first = runner.start(
        skip_chat_model=True, chat_model_for=lambda run: "fireworks.deepseek-v4p1-flash"
    )
    assert first["state"] == "running"
    view = _wait_done(runner)
    assert view["state"] == "ready"
    assert [s["status"] for s in view["steps"]] == ["done", "done", "done"]
    assert checks == [(True, True)]
    argv, kwargs = calls[0]
    assert argv[3:] == sr.run_args(True)
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert "[====] 100% (5 MB/5 MB)" in view["log_tail"]


def test_a_cloud_run_tests_a_chat_on_the_cloud_model(monkeypatch):
    turns = []
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))
    monkeypatch.setattr(sr, "check", lambda *a, **k: {"ready": True})
    monkeypatch.setattr(
        sr, "_agent_turn_problem", lambda model_id=None: turns.append(model_id)
    )
    runner = sr.SetupRunner()
    runner.start(
        skip_chat_model=True, chat_model_for=lambda run: "fireworks.deepseek-v4p1-flash"
    )
    view = _wait_done(runner)
    assert view["state"] == "ready"
    assert turns == ["fireworks.deepseek-v4p1-flash"]
    assert [t["job"] for t in view["tests"] if t["state"] == "passed"][-1] == "agent"


def test_a_cloud_run_with_no_saved_cloud_model_fails_its_chat_test(monkeypatch):
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))
    monkeypatch.setattr(sr, "check", lambda *a, **k: {"ready": True})
    monkeypatch.setattr(
        sr, "_agent_turn_problem", lambda model_id=None: pytest.fail("no turn expected")
    )
    runner = sr.SetupRunner()
    runner.start(skip_chat_model=True, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["state"] == "failed"
    assert view["error"] == sr.NO_CLOUD_MODEL


def test_a_server_only_run_is_ready_without_loading_models(monkeypatch):
    calls = []
    _popen(
        monkeypatch,
        _FakeProc(io.BytesIO(b"Step 1/2: Starting Lemonade Server...\n")),
        calls,
    )

    def check(*_a, **_k):
        raise AssertionError("a server-only run has no models to load")

    monkeypatch.setattr(sr, "check", check)
    runner = sr.SetupRunner()
    runner.start(skip_chat_model=False, server_only=True, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["state"] == "ready" and view["server_only"] is True
    assert [s["key"] for s in view["steps"]] == ["server"]
    assert view["steps"][0]["status"] == "done"
    assert "--server-only" in calls[0][0]


def test_an_embedded_upgrade_ends_ready_without_loading_models(monkeypatch):
    calls = []
    _popen(
        monkeypatch,
        _FakeProc(io.BytesIO(b"Step 1/2: Starting Lemonade Server...\n")),
        calls,
    )

    def check(*_a, **_k):
        raise AssertionError("an embedded upgrade preserves the existing model cache")

    monkeypatch.setattr(sr, "check", check)
    runner = sr.SetupRunner()
    runner.start(
        skip_chat_model=False, upgrade_embedded=True, chat_model_for=_local_chat
    )
    view = _wait_done(runner)
    assert view["state"] == "ready" and view["upgrade_embedded"] is True
    assert view["text"] == "Lemonade Server upgraded"
    assert "--server-only" in calls[0][0]
    assert "--skip-models" not in calls[0][0]
    assert "--force-reinstall" not in calls[0][0]
    # One step, the server's: no "Download the models" or "Check that the models load" marked done.
    assert [(s["label"], s["status"]) for s in view["steps"]] == [
        ("Update the local model server", "done")
    ]


def test_an_extra_model_with_no_load_check_ends_ready(monkeypatch):
    plan = json.dumps(
        {
            "event": "plan",
            "models": [
                {"id": "Gemma-4-E4B-it-GGUF", "bytes_total": 10, "downloaded": True},
                {"id": "gpt-oss-20b-GGUF", "bytes_total": 10, "downloaded": True},
            ],
        }
    )
    _popen(monkeypatch, _FakeProc(io.BytesIO((plan + chr(10)).encode())))
    monkeypatch.setattr(
        sr,
        "check",
        lambda *_a, **_k: {
            "ready": True,
            "models": [{"id": "Gemma-4-E4B-it-GGUF", "loaded": True}],
        },
    )
    runner = sr.SetupRunner()
    runner.start(
        skip_chat_model=False,
        extra_models=["gpt-oss-20b-GGUF"],
        chat_model_for=_local_chat,
    )
    view = _wait_done(runner)
    assert view["state"] == "ready"
    assert {m["id"]: m["state"] for m in view["models"]} == {
        "Gemma-4-E4B-it-GGUF": "ready",
        "gpt-oss-20b-GGUF": "ready",
    }


def test_only_extras_skip_the_load_check(monkeypatch):
    plan = json.dumps(
        {
            "event": "plan",
            "models": [
                {"id": "Gemma-4-E4B-it-GGUF", "bytes_total": 10, "downloaded": True},
                {
                    "id": "user.embeddinggemma-300m-GGUF",
                    "bytes_total": 1,
                    "downloaded": True,
                },
            ],
        }
    )
    _popen(monkeypatch, _FakeProc(io.BytesIO((plan + chr(10)).encode())))
    # The check reports Gemma only: the embedder was never verified.
    monkeypatch.setattr(
        sr,
        "check",
        lambda *_a, **_k: {
            "ready": True,
            "models": [{"id": "Gemma-4-E4B-it-GGUF", "loaded": True}],
        },
    )
    runner = sr.SetupRunner()
    runner.start(skip_chat_model=False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    states = {m["id"]: m["state"] for m in view["models"]}
    assert states["Gemma-4-E4B-it-GGUF"] == "ready"
    assert states["user.embeddinggemma-300m-GGUF"] != "ready"


def test_a_failed_server_only_run_fails_like_any_run(monkeypatch):
    _popen(monkeypatch, _FakeProc(io.BytesIO("\u274c Port in use\n".encode()), 1))
    monkeypatch.setattr(sr, "server_problem", lambda: None)
    runner = sr.SetupRunner()
    runner.start(skip_chat_model=False, server_only=True, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["state"] == "failed" and "Port in use" in view["error"]


def test_a_run_follows_the_chat_model_and_the_users_server(monkeypatch):
    output = (
        "Step 1/5: Starting Lemonade Server...\n"
        "   Chat model: Qwen3.6-35B-A3B-GGUF — this PC has 32 GB for models\n"
        "Step 2/5: Downloading models for 'gaia' profile...\n"
    ).encode()
    _popen(monkeypatch, _FakeProc(io.BytesIO(output)))
    monkeypatch.setattr(sr, "check", lambda *_a, **_k: {"ready": True})
    monkeypatch.setattr(
        sr, "users_lemonade_url", lambda: "http://localhost:13305/api/v1"
    )
    runner = sr.SetupRunner()
    runner.start(skip_chat_model=False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["chat_model"] == "Qwen3.6-35B-A3B-GGUF"
    assert view["steps"][0]["label"] == "Use your Lemonade Server"
    assert "Qwen3.6-35B-A3B-GGUF" in view["steps"][1]["detail"]


def test_init_error_lines_are_not_wrapped_at_80_columns(monkeypatch):
    """The failure reason is the last ❌ line; Rich would wrap it in a pipe."""
    from rich.console import Console

    calls = []
    _popen(monkeypatch, _FakeProc(io.BytesIO(b"")), calls)
    monkeypatch.setattr(sr, "check", lambda *a, **k: {"ready": True})
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    _wait_done(runner)

    env = calls[0][1]["env"]
    assert env["PATH"] == __import__("os").environ["PATH"]
    message = "❌ " + "Could not reach the Agent Hub " * 10
    buf = io.StringIO()
    Console(file=buf, _environ=env).print(message)
    assert buf.getvalue().strip() == message.strip()


def test_progress_follows_the_output(monkeypatch):
    stdout = _BlockingStdout(_OUTPUT)
    proc = _FakeProc(stdout)
    _popen(monkeypatch, proc)
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    deadline = time.time() + 3
    while time.time() < deadline:
        view = runner.status()
        if view["text"] == "Downloading user.embeddinggemma-300m-GGUF":
            break
        time.sleep(0.02)
    assert view["text"] == "Downloading user.embeddinggemma-300m-GGUF"
    assert [s["status"] for s in view["steps"]] == ["done", "active", "pending"]
    # A phase line resets the percentage and size of the previous download.
    assert view["percent"] is None and view["size"] is None
    assert runner.cancel() is True
    _wait_done(runner)


def test_a_failed_run_reports_the_error_line(monkeypatch):
    out = _OUTPUT + b"   \xe2\x9d\x8c user.embeddinggemma-300m-GGUF - Request failed\n"
    _popen(monkeypatch, _FakeProc(io.BytesIO(out), returncode=1))
    monkeypatch.setattr(
        sr, "check", lambda *a, **k: pytest.fail("a failed run is not verified")
    )
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["state"] == "failed"
    assert "exit 1" in view["error"]
    assert "Request failed" in view["error"]
    assert [s["status"] for s in view["steps"]] == ["done", "failed", "pending"]


def test_a_failed_run_without_an_error_line_names_the_last_output(monkeypatch):
    _popen(
        monkeypatch, _FakeProc(io.BytesIO(b"Step 1/5: Starting Lemonade...\nboom\n"), 2)
    )
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["state"] == "failed"
    assert view["error"].endswith("boom")


_UNREACHABLE = {
    "kind": "server_unreachable",
    "message": "Your Lemonade Server at 127.0.0.1:9 isn't answering.",
    "start": "Start Lemonade Server from the Lemonade tray icon.",
    "use_gaia": True,
    "env_url": None,
}
_RAW_ERROR = (
    "Lemonade Server at http://127.0.0.1:9/api/v1 is not reachable: Request "
    "failed: HTTPConnectionPool(host='127.0.0.1', port=9): Max retries exceeded"
)


def test_an_unanswering_server_fails_in_plain_words(monkeypatch):
    out = f"Step 1/5: Starting Lemonade Server...\n   ❌ {_RAW_ERROR}\n".encode()
    _popen(monkeypatch, _FakeProc(io.BytesIO(out), returncode=1))
    monkeypatch.setattr(sr, "server_problem", lambda: _UNREACHABLE)
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["state"] == "failed"
    assert view["error"] == _UNREACHABLE["message"]
    assert view["problem"] == _UNREACHABLE
    # The exception text stays behind "Details".
    assert any("HTTPConnectionPool" in line for line in view["log_tail"])
    assert [s["status"] for s in view["steps"]] == ["failed", "pending", "pending"]


def test_a_later_failure_is_not_blamed_on_the_server(monkeypatch):
    out = _OUTPUT + "   ❌ user.embeddinggemma-300m-GGUF - failed\n".encode()
    _popen(monkeypatch, _FakeProc(io.BytesIO(out), returncode=1))
    monkeypatch.setattr(
        sr, "server_problem", lambda: pytest.fail("the server step passed")
    )
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["problem"] is None
    assert "embeddinggemma" in view["error"]


def test_use_gaias_own_runs_init_with_the_switch(monkeypatch):
    calls, forgot = [], []
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)), calls)
    monkeypatch.setattr(sr, "check", lambda *_a, **_k: {"ready": True})
    monkeypatch.setattr(sr, "_forget_previous_server", lambda: forgot.append(1))
    monkeypatch.setattr(sr, "users_lemonade_url", lambda: "http://127.0.0.1:9/api/v1")
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    runner = sr.SetupRunner()
    first = runner.start(False, gaia_lemonade=True, chat_model_for=_local_chat)
    # Setup now installs GAIA's own, not the user's server it was set to.
    assert first["steps"][0]["label"] == "Install the local model server"
    assert first["command"] == "gaia init --profile gaia --gaia-lemonade"
    view = _wait_done(runner)
    assert view["state"] == "ready" and view["gaia_lemonade"] is True
    assert calls[0][0][-1] == "--gaia-lemonade"
    assert forgot == [1]


def test_a_failed_switch_to_gaias_own_keeps_its_own_error(monkeypatch):
    """The user's server is still down, but it isn't what failed this time."""
    out = b"Step 1/5: Starting Lemonade Server...\n   \xe2\x9d\x8c Could not download\n"
    _popen(monkeypatch, _FakeProc(io.BytesIO(out), returncode=1))
    monkeypatch.setattr(sr, "server_problem", lambda: pytest.fail("not the cause"))
    monkeypatch.setattr(
        sr, "_forget_previous_server", lambda: pytest.fail("nothing was switched")
    )
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    runner = sr.SetupRunner()
    runner.start(False, gaia_lemonade=True, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["problem"] is None
    assert "Could not download" in view["error"]
    assert view["command"].endswith("--gaia-lemonade")


def test_a_switch_is_applied_even_when_the_check_then_fails(monkeypatch):
    forgot = []
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))
    monkeypatch.setattr(sr, "check", lambda *_a, **_k: {"ready": False})
    monkeypatch.setattr(sr, "_forget_previous_server", lambda: forgot.append(1))
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    runner = sr.SetupRunner()
    runner.start(False, gaia_lemonade=True, chat_model_for=_local_chat)
    assert _wait_done(runner)["state"] == "failed"
    assert forgot == [1]


def test_use_gaias_own_overrides_a_lemonade_base_url_from_the_app(monkeypatch):
    """The env server isn't answering; the app's choice must not dead-end on it."""
    from gaia.config import GaiaConfig

    calls = []
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)), calls)
    monkeypatch.setattr(sr, "check", lambda *_a, **_k: {"ready": True})
    monkeypatch.setattr(sr, "_forget_previous_server", lambda: None)
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("GAIA_LEMONADE_EMBEDDED", raising=False)
    runner = sr.SetupRunner()
    runner.start(False, gaia_lemonade=True, chat_model_for=_local_chat)
    # Saved before init runs, so it and the daemon set up GAIA's own.
    assert GaiaConfig.load().lemonade_env_url_ignored == "http://127.0.0.1:9"
    assert lemonade_choice.configured_source() is None
    assert _wait_done(runner)["state"] == "ready"
    assert calls[0][0][-1] == "--gaia-lemonade"


def test_a_plain_retry_does_not_override_lemonade_base_url(monkeypatch):
    from gaia.config import GaiaConfig

    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))
    monkeypatch.setattr(sr, "check", lambda *_a, **_k: {"ready": True})
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("GAIA_LEMONADE_EMBEDDED", raising=False)
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    _wait_done(runner)
    assert GaiaConfig.load().lemonade_env_url_ignored is None
    assert lemonade_choice.configured_source() == "env"


def test_verify_not_ready_fails_with_the_reasons(monkeypatch):
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))
    monkeypatch.setattr(
        sr, "check", lambda *a, **k: {"ready": False, "reasons": ["a", "b"]}
    )
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["state"] == "failed"
    assert view["error"] == "a; b"
    assert [s["status"] for s in view["steps"]] == ["done", "done", "failed"]


def test_verify_error_fails_the_run(monkeypatch):
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))

    def check(*_a, **_k):
        raise sr.SetupError("Checking setup took too long.")

    monkeypatch.setattr(sr, "check", check)
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["state"] == "failed"
    assert view["error"] == "Checking setup took too long."


def test_an_unexpected_error_fails_the_run_and_frees_the_runner(monkeypatch):
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))

    def check(*_a, **_k):
        raise OSError("marker file is read-only")

    monkeypatch.setattr(sr, "check", check)
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["state"] == "failed"
    # The cause stays in the server log, never in what the browser is sent.
    assert "marker file is read-only" not in view["error"]
    assert "GAIA server log" in view["error"]
    # Not stranded in "verifying": a retry is allowed.
    monkeypatch.setattr(sr, "check", lambda *a, **k: {"ready": True})
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))
    runner.start(False, chat_model_for=_local_chat)
    assert _wait_done(runner)["state"] == "ready"


def test_an_unexpected_error_is_named_in_the_report_but_not_on_screen(monkeypatch):
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))

    def check(*_a, **_k):
        raise OSError("marker file is read-only")

    monkeypatch.setattr(sr, "check", check)
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    assert "marker file" not in _wait_done(runner)["error"]
    every = [line for lines in runner.report()["logs"].values() for line in lines]
    assert "Setup failed: OSError: marker file is read-only" in every


def test_a_report_asked_for_as_the_failure_record_starts_waits_for_it(monkeypatch):
    # report() ran between the record thread being published and started, and
    # joining an unstarted thread raises.
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))

    def check(*_a, **_k):
        raise OSError("marker file is read-only")

    monkeypatch.setattr(sr, "check", check)
    runner = sr.SetupRunner()
    errors = []
    real_thread = threading.Thread

    def ask_for_report():
        try:
            runner.report()
        except RuntimeError as e:
            errors.append(e)

    class ReportFirst(real_thread):
        def start(self):
            if getattr(self, "_target", None) == runner._record_failure:
                asker = real_thread(target=ask_for_report)
                asker.start()
                asker.join(timeout=0.5)  # it gets in first, if nothing stops it
            super().start()

    monkeypatch.setattr(sr.threading, "Thread", ReportFirst)
    runner.start(False, chat_model_for=_local_chat)
    _wait_done(runner)
    runner.report()
    assert errors == []


def test_only_one_run_at_a_time_and_cancel(monkeypatch):
    proc = _FakeProc(_BlockingStdout())
    _popen(monkeypatch, proc)
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    with pytest.raises(sr.SetupError, match="already running"):
        runner.start(False, chat_model_for=_local_chat)
    assert runner.cancel() is True
    assert proc.killed
    view = _wait_done(runner)
    assert view["state"] == "cancelled"
    assert view["text"] == "Setup stopped"
    assert runner.cancel() is False


def test_cancel_with_nothing_running():
    assert sr.SetupRunner().cancel() is False


def test_a_command_that_cannot_start_is_an_error(monkeypatch):
    def popen(*_a, **_k):
        raise FileNotFoundError("no python")

    monkeypatch.setattr(sr.subprocess, "Popen", popen)
    runner = sr.SetupRunner()
    with pytest.raises(sr.SetupError, match="Could not start setup"):
        runner.start(False, chat_model_for=_local_chat)
    assert runner.status() is None


# ── server_problem ─────────────────────────────────────────────────────────


@pytest.fixture
def users_server(monkeypatch):
    """GAIA set to the user's server at *url*, chosen by *source*; probes recorded."""
    probed = []

    def use(url, source, status=lemonade_choice.UNREACHABLE, installed=True):
        monkeypatch.setattr(lemonade_client, "configured_lemonade_url", lambda: url)
        monkeypatch.setattr(lemonade_choice, "configured_source", lambda: source)

        def probe(base, api_key=None, **_kw):
            probed.append(base)
            return lemonade_choice.ProbeResult(
                base, status, version="9.0.0", minimum="10.0.0"
            )

        monkeypatch.setattr(lemonade_choice, "probe", probe)
        monkeypatch.setattr(
            lemonade_launcher,
            "resolve_lemonade",
            lambda: type("Tooling", (), {"found": installed})(),
        )
        monkeypatch.setattr(
            lemonade_launcher,
            "describe_start_hint",
            lambda: lemonade_launcher.StartHint(instruction="Start it from the tray."),
        )
        return probed

    return use


def test_an_env_server_that_does_not_answer_says_so_plainly(users_server):
    probed = users_server("http://127.0.0.1:9", "env")
    problem = _REAL_SERVER_PROBLEM()
    assert probed == ["http://127.0.0.1:9/api/v1"]
    assert problem["message"] == "Your Lemonade Server at 127.0.0.1:9 isn't answering."
    assert problem["start"] == "Start it from the tray."
    # Choosing GAIA's own here overrides LEMONADE_BASE_URL: no env to edit,
    # and the screen names the value it will ignore.
    assert problem["use_gaia"] is True
    assert problem["env_url"] == "http://127.0.0.1:9"
    assert "switch_hint" not in problem
    assert "lemonade-server serve" not in str(problem)


def test_a_saved_server_that_does_not_answer_offers_gaias_own(users_server):
    users_server("http://localhost:13305/api/v1", "config")
    problem = _REAL_SERVER_PROBLEM()
    assert problem["use_gaia"] is True
    assert problem["env_url"] is None
    assert "switch_hint" not in problem


def test_a_saved_server_that_is_too_old_names_both_versions_and_offers_gaias_own(
    users_server,
):
    users_server("http://localhost:13305/api/v1", "config", lemonade_choice.TOO_OLD)
    problem = _REAL_SERVER_PROBLEM()
    assert problem["kind"] == "server_too_old"
    assert "v9.0.0" in problem["message"] and "v10.0.0" in problem["message"]
    # Upgrading happens outside GAIA: no start hint, and no command anywhere.
    assert problem["start"] is None
    assert problem["use_gaia"] is True
    assert "gaia config" not in str(problem)


def test_a_too_old_env_server_offers_gaias_own(users_server):
    users_server("http://127.0.0.1:13305", "env", lemonade_choice.TOO_OLD)
    problem = _REAL_SERVER_PROBLEM()
    assert problem["kind"] == "server_too_old"
    assert problem["use_gaia"] is True
    assert "LEMONADE_BASE_URL" not in str(problem)


@pytest.mark.parametrize(
    "status", [lemonade_choice.OK, lemonade_choice.NEEDS_KEY, lemonade_choice.BAD_KEY]
)
def test_a_server_that_answers_well_enough_is_not_the_problem(users_server, status):
    users_server("http://localhost:13305/api/v1", "config", status)
    assert _REAL_SERVER_PROBLEM() is None


def test_no_start_hint_when_this_pc_has_no_lemonade_to_start(users_server):
    users_server("http://localhost:13305", "config", installed=False)
    assert _REAL_SERVER_PROBLEM()["start"] is None


def test_a_remote_server_is_started_where_it_runs(users_server):
    users_server("http://192.168.1.20:13305", "env")
    problem = _REAL_SERVER_PROBLEM()
    assert (
        problem["start"] == "Start it on the computer at 192.168.1.20, then try again."
    )


@pytest.mark.parametrize("url", ["http://0.0.0.0:13305", "http://[::]:13305"])
def test_a_wildcard_bind_address_is_this_pc(users_server, url):
    users_server(url, "env")
    assert _REAL_SERVER_PROBLEM()["start"] == "Start it from the tray."


def test_a_start_hint_that_cannot_be_worked_out_still_names_the_problem(
    users_server, monkeypatch, caplog
):
    users_server("http://127.0.0.1:9", "config")

    def unreadable():
        raise PermissionError("install dir")

    monkeypatch.setattr(lemonade_launcher, "resolve_lemonade", unreadable)
    problem = _REAL_SERVER_PROBLEM()
    assert problem["message"] == "Your Lemonade Server at 127.0.0.1:9 isn't answering."
    assert problem["start"] is None
    assert "install dir" in caplog.text


@pytest.mark.parametrize("status", [lemonade_choice.OK, lemonade_choice.NEEDS_KEY])
def test_a_server_that_answers_is_not_the_problem(users_server, status):
    users_server("http://127.0.0.1:9", "env", status=status)
    assert _REAL_SERVER_PROBLEM() is None


def test_gaias_own_server_is_never_probed(users_server):
    probed = users_server(None, None)
    assert _REAL_SERVER_PROBLEM() is None
    assert probed == []


# ── Routes ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    runner = sr.SetupRunner()
    monkeypatch.setattr(setup_router, "runner", runner)
    # The disk pre-flight has its own tests; here the drive always has room.
    monkeypatch.setattr(setup_router.plan_api, "run_disk_warning", lambda **_k: None)
    app = FastAPI()
    app.include_router(setup_router.router)
    app.state.db = ChatDatabase(":memory:")
    return TestClient(app, headers={"X-Gaia-UI": "1"}), runner


def test_check_route_adds_the_steps(client, monkeypatch):
    test_client, _ = client
    seen = {}

    def check(skip_chat_model, load, **_k):
        seen.update(skip_chat_model=skip_chat_model, load=load)
        return {"ready": False, "stage": "models", "reasons": ["x"]}

    monkeypatch.setattr(setup_router, "check", check)
    resp = test_client.get(
        "/api/setup/check", params={"skip_chat_model": "true", "load": "true"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage"] == "models"
    assert [s["key"] for s in body["steps"]] == list(sr.PHASES)
    assert seen == {"skip_chat_model": True, "load": True}


def test_check_route_also_returns_the_cloud_steps(client, monkeypatch):
    """A local check still tells the UI what a cloud choice would download."""
    test_client, _ = client
    monkeypatch.setattr(
        setup_router, "check", lambda **_k: {"ready": False, "reasons": ["x"]}
    )
    body = test_client.get("/api/setup/check").json()
    assert body["steps"] == sr.step_labels(False)
    assert body["cloud_steps"] == sr.step_labels(True)
    assert body["cloud_steps"] != body["steps"]


@pytest.mark.parametrize("seen_ready", [True, False])
def test_check_route_says_whether_setup_was_ever_seen_ready(
    client, monkeypatch, seen_ready
):
    """A set-up PC whose model server is down must not read as a first run."""
    test_client, _ = client
    down = {"ready": False, "stage": "server", "reasons": ["not reachable"]}
    monkeypatch.setattr(setup_router, "check", lambda **_k: dict(down))
    monkeypatch.setattr(setup_router, "is_initialized", lambda: seen_ready)
    body = test_client.get("/api/setup/check").json()
    assert body["stage"] == "server"
    assert body["initialized"] is seen_ready


def test_check_route_needs_the_ui_header(client, monkeypatch):
    """A cross-site GET carries no X-Gaia-UI, so it must not start a check."""
    test_client, _ = client
    called = []
    monkeypatch.setattr(setup_router, "check", lambda **k: called.append(k))
    resp = test_client.get(
        "/api/setup/check", params={"load": "true"}, headers={"X-Gaia-UI": ""}
    )
    assert resp.status_code == 403
    assert called == []


def test_check_route_steps_follow_use_mine(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(setup_router, "check", lambda **_k: {"ready": False})
    monkeypatch.setattr(
        setup_router, "users_lemonade_url", lambda: "http://localhost:13305/api/v1"
    )
    body = test_client.get("/api/setup/check").json()
    assert body["steps"][0]["label"] == "Use your Lemonade Server"
    assert body["cloud_steps"][0]["label"] == "Use your Lemonade Server"


def test_check_route_unanswered_is_503(client, monkeypatch):
    test_client, _ = client

    def check(**_k):
        raise sr.SetupError("Setup could not be checked (exit 2).")

    monkeypatch.setattr(setup_router, "check", check)
    resp = test_client.get("/api/setup/check")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "exit 2" not in detail
    assert "Try again" in detail and "server log" in detail
    assert "gaia init" not in detail


def test_status_route_idle(client):
    test_client, _ = client
    assert test_client.get("/api/setup/status").json() == {"state": "idle"}


def test_run_status_and_cancel_routes(client, monkeypatch):
    test_client, _ = client
    proc = _FakeProc(_BlockingStdout())
    _popen(monkeypatch, proc)
    started = test_client.post("/api/setup/run", json={"skip_chat_model": True})
    assert started.status_code == 200
    assert started.json()["skip_chat_model"] is True
    again = test_client.post("/api/setup/run", json={})
    assert again.status_code == 409
    assert test_client.get("/api/setup/status").json()["state"] == "running"
    assert test_client.post("/api/setup/cancel").json() == {"cancelled": True}
    assert test_client.post("/api/setup/cancel").json() == {"cancelled": False}


def test_run_route_passes_the_switch_to_gaias_own(client, monkeypatch):
    test_client, runner = client
    seen = []
    monkeypatch.setattr(
        runner, "start", lambda skip, gaia, **_k: seen.append((skip, gaia)) or {}
    )
    test_client.post("/api/setup/run", json={"gaia_lemonade": True})
    test_client.post("/api/setup/run", json={})
    assert seen == [(False, True), (False, False)]


@pytest.mark.parametrize(
    "raw, clean",
    [
        # SGR colour and bold.
        ("\x1b[1;36mGemma\x1b[0m", "Gemma"),
        # Cursor / erase CSI sequences, and the 8-bit CSI introducer.
        ("\x1b[2K\x1b[1GStep 2/5: Pulling", "Step 2/5: Pulling"),
        ("\x9b0mdone", "done"),
        # OSC 8 hyperlink, ST- and BEL-terminated: the link text survives.
        ("see \x1b]8;;https://x.example\x1b\\docs\x1b]8;;\x1b\\ now", "see docs now"),
        ("\x1b]0;window title\x07Ready", "Ready"),
        # Escapes cut off at the end of a captured line.
        ("Downloading 40%\x1b[1;3", "Downloading 40%"),
        ("Downloading 40%\x1b[", "Downloading 40%"),
        ("Downloading 40%\x1b]8;;https://x.example", "Downloading 40%"),
        ("Downloading 40%\x1b", "Downloading 40%"),
        # Charset selection and other two-byte ESC sequences.
        ("\x1b(BReady\x1b7", "Ready"),
        # Literal brackets with no ESC are content, not escapes.
        ("[1;36m is literal", "[1;36m is literal"),
        ("[bold]Model[/bold] [x] 45%", "[bold]Model[/bold] [x] 45%"),
        ("[2026-10-29 10:00:00] | INFO", "[2026-10-29 10:00:00] | INFO"),
        ("[####----] 45% 1.2 GB / 2.5 GB", "[####----] 45% 1.2 GB / 2.5 GB"),
    ],
)
def test_setup_output_strips_only_terminal_escapes(raw, clean):
    run = sr.SetupRun(False)
    sr.SetupRunner()._consume(run, raw)

    assert run.log == [clean]


def test_a_line_that_is_only_escapes_adds_nothing_to_the_log():
    run = sr.SetupRun(False)
    sr.SetupRunner()._consume(run, "\x1b[0m\x1b[2K\x1b]8;;\x1b\\")

    assert run.log == []


def test_progress_is_still_read_through_colour_codes():
    progress = sr.describe(
        sr._ANSI_RE.sub("", "\x1b[32m[####----]\x1b[0m \x1b[1m45%\x1b[0m")
    )

    assert progress is not None and progress.percent == 45


# ── per-model progress (#6226) ─────────────────────────────────────────────

GEMMA = "Gemma-4-E4B-it-GGUF"
EMBED = "user.embeddinggemma-300m-GGUF"
QWEN = "Qwen3.6-35B-A3B-GGUF"


def _plan_event(**extra):
    return json.dumps(
        {
            "event": "plan",
            "models": [
                {"id": EMBED, "bytes_total": 330_000_000, "downloaded": True},
                {"id": QWEN, "bytes_total": 23_300_000_000, "downloaded": False},
            ],
            "skipped": [GEMMA],
            **extra,
        }
    )


def _models(runner):
    return {m["id"]: m for m in runner.status()["models"]}


def test_run_args_ask_for_json_progress_and_pass_the_users_confirmations():
    args = sr.run_args(False, allow_low_disk=True, keep_trying=True)
    assert "--progress-json" in args
    assert "--allow-low-disk" in args and "--keep-trying" in args
    assert "--allow-low-disk" not in sr.run_args(False)
    assert "--keep-trying" not in sr.run_args(False)


def test_progress_json_lines_become_per_model_rows_not_log_lines():
    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._consume(run, _plan_event())
    rows = _models(runner)
    assert rows[EMBED]["state"] == "verifying"
    assert rows[QWEN]["state"] == "queued"
    assert rows[GEMMA]["state"] == "skipped"
    runner._consume(
        run,
        json.dumps(
            {
                "event": "progress",
                "model": QWEN,
                "bytes_done": 3_300_000_000,
                "bytes_total": 23_300_000_000,
                "speed_bps": 20_000_000,
            }
        ),
    )
    qwen = _models(runner)[QWEN]
    assert qwen["state"] == "downloading"
    assert qwen["eta_s"] == 1000
    # Bytes in the first event are not "already on this PC": a fresh pull's
    # first report already carries a few MB.
    assert qwen["downloaded_before_gb"] is None
    overall = runner.status()["overall"]
    assert overall["bytes_total"] == 23_630_000_000
    assert overall["speed_bps"] == 20_000_000
    assert overall["percent"] == 15
    assert run.log == []


def test_a_full_disk_fails_the_model_with_what_to_do():
    runner = sr.SetupRunner()
    runner._on_event(json.loads(_plan_event()))
    runner._on_event(
        {"event": "error", "model": QWEN, "message": "No space left on device"}
    )
    qwen = _models(runner)[QWEN]
    assert qwen["state"] == "failed" and qwen["error_kind"] == "disk_full"
    assert "Free some space" in qwen["error"] and qwen["can_retry"]
    assert runner.status()["overall"]["failed"] == 1


def test_a_path_too_long_for_windows_says_so_instead_of_pointing_at_a_log():
    runner = sr.SetupRunner()
    runner._on_event(json.loads(_plan_event()))
    runner._on_event(
        {
            "event": "error",
            "model": QWEN,
            "message": "Failed to create directory 'C:/Users/you/.cache/huggingface/"
            "hub/models--x/snapshots/abc': The filename or extension is too long.",
        }
    )
    qwen = _models(runner)[QWEN]
    assert qwen["state"] == "failed" and qwen["error_kind"] == "path_too_long"
    assert "path to the models folder is too long" in qwen["error"]
    assert "server log" not in qwen["error"]


def test_keep_trying_shows_the_wait_not_a_failure():
    runner = sr.SetupRunner()
    runner._on_event(json.loads(_plan_event()))
    runner._on_event({"event": "error", "model": QWEN, "message": "Connection reset"})
    runner._on_event({"event": "retrying", "model": QWEN, "until": 1_900_000_000})
    qwen = _models(runner)[QWEN]
    assert qwen["state"] == "downloading" and qwen["error"] is None
    assert qwen["retrying_until"] == 1_900_000_000


def test_a_finished_run_marks_what_loaded_ready_and_what_did_not_failed():
    runner = sr.SetupRunner()
    runner._on_event(json.loads(_plan_event()))
    runner._on_event({"event": "model_done", "model": QWEN, "downloaded": True})
    with runner._lock:
        runner._settle_models({EMBED}, {QWEN: "would not load"})
    rows = _models(runner)
    assert rows[EMBED]["state"] == "ready"
    assert rows[QWEN]["state"] == "failed" and rows[QWEN]["error_kind"] == "verify"
    # Details shows the row's log, so the reason is there, not only in the error.
    assert "Failed to load: would not load" in rows[QWEN]["log"]
    assert rows[GEMMA]["state"] == "skipped"


def test_a_downloaded_model_the_check_did_not_load_is_not_left_verifying():
    """Once init saves the chat model, ``--check --load`` drops Gemma; its row
    must still finish, or a front end waits on it forever."""
    runner = sr.SetupRunner()
    runner._on_event(
        {
            "event": "plan",
            "models": [
                {"id": QWEN, "bytes_total": 10, "downloaded": False},
                {"id": GEMMA, "bytes_total": 10, "downloaded": False},
            ],
        }
    )
    for model in (QWEN, GEMMA):
        runner._on_event({"event": "model_done", "model": model, "downloaded": True})
    with runner._lock:
        runner._settle_models(None, {})
    rows = _models(runner)
    assert rows[QWEN]["state"] == rows[GEMMA]["state"] == "ready"


def test_the_init_child_never_inherits_the_daemons_broker_url(monkeypatch):
    """Run from the daemon, the child got the broker URL with no credential and
    every model load failed; it must find the daemon itself instead."""
    from gaia.daemon.constants import BROKER_URL_ENV_VAR

    monkeypatch.setenv(BROKER_URL_ENV_VAR, "http://127.0.0.1:5555")
    monkeypatch.setenv("GAIA_HARNESS_MARK", "kept")
    calls = []
    _fake_run(monkeypatch, 0, '{"ready": true}\n', calls=calls)
    sr.check()
    env = calls[0][1]["env"]
    assert BROKER_URL_ENV_VAR not in env
    assert env["GAIA_HARNESS_MARK"] == "kept"

    seen = []
    proc = _FakeProc(io.BytesIO(b""))
    _popen(monkeypatch, proc, calls=seen)
    sr.SetupRunner().start(skip_chat_model=False, chat_model_for=_local_chat)
    env = seen[0][1]["env"]
    assert BROKER_URL_ENV_VAR not in env
    assert env["NO_COLOR"] == "1" and env["GAIA_HARNESS_MARK"] == "kept"


class _PullClient:
    """Lemonade for one in-process pull: *events* streamed, then *ok*."""

    def __init__(self, events, ok=True, gate=None, system_info=None):
        self.events, self.ok, self.gate = events, ok, gate
        self.system_info = system_info or {}
        self.calls = []

    def get_system_info(self, timeout=None):
        return self.system_info

    def ensure_model_downloaded(self, model_id, on_progress=None, **kwargs):
        self.calls.append((model_id, kwargs))
        if self.gate is not None:
            self.gate.wait(5)
        for event in self.events:
            on_progress(event)
        return self.ok


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_retry_downloads_one_model_in_process_with_a_valid_pull():
    runner = sr.SetupRunner()
    client = _PullClient(
        [
            {
                "event": "progress",
                "file": "m.gguf",
                "bytes_downloaded": 5,
                "bytes_total": 5,
            },
            {"event": "complete"},
        ]
    )
    runner.download_model(client, GEMMA)
    assert _wait_until(lambda: _models(runner)[GEMMA]["state"] == "ready")
    # A built-in pulls by name only; a recipe here 400s (#1655).
    assert client.calls == [(GEMMA, {"timeout": 7200})]


def test_a_failed_single_download_says_why_and_can_retry():
    runner = sr.SetupRunner()
    client = _PullClient(
        [{"event": "error", "error": "Connection reset by peer"}], ok=False
    )
    runner.download_model(client, GEMMA)
    assert _wait_until(lambda: _models(runner)[GEMMA]["state"] == "failed")
    row = _models(runner)[GEMMA]
    assert row["error_kind"] == "network" and row["can_retry"]
    assert row["log"][0].startswith(f"Downloading {GEMMA} through Lemonade")
    assert row["log"][-1].startswith("Failed:")


def test_one_model_cannot_download_twice_or_during_a_run():
    runner = sr.SetupRunner()
    gate = threading.Event()
    runner.download_model(_PullClient([], gate=gate), GEMMA)
    with pytest.raises(sr.SetupError, match="already downloading"):
        runner.download_model(_PullClient([]), GEMMA)
    with pytest.raises(sr.SetupError, match="still downloading"):
        runner.start(False, chat_model_for=_local_chat)
    gate.set()
    assert _wait_until(lambda: _models(runner)[GEMMA]["state"] == "ready")
    runner._run = sr.SetupRun(skip_chat_model=False)
    with pytest.raises(sr.SetupError, match="Setup is running"):
        runner.download_model(_PullClient([]), EMBED)


def test_cancelling_one_download_marks_it_cancelled_and_resumable():
    runner = sr.SetupRunner()
    gate = threading.Event()
    runner.download_model(_PullClient([], ok=False, gate=gate), GEMMA)
    assert runner.cancel(GEMMA) is True
    gate.set()
    assert _wait_until(lambda: _models(runner)[GEMMA]["state"] == "cancelled")
    assert _models(runner)[GEMMA]["can_retry"]
    assert _models(runner)[GEMMA]["log"][-1] == "Cancelled"
    assert runner.cancel("not-downloading") is False


def test_the_run_bar_still_moves_when_init_reports_progress_as_json():
    """The setup screen draws run.percent; --progress-json must keep it filled."""
    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._consume(run, _plan_event())
    assert run.view()["percent"] is None
    runner._consume(
        run,
        json.dumps(
            {
                "event": "progress",
                "model": QWEN,
                "bytes_done": 11_650_000_000,
                "bytes_total": 23_300_000_000,
                "speed_bps": 1,
            }
        ),
    )
    view = run.view()
    assert view["percent"] == 50
    assert view["size"] == "11.98 GB of 23.63 GB"


@pytest.mark.parametrize(
    "error, status, words",
    [
        (sr.SetupBusy("secret path C:/x"), 409, "already running"),
        (sr.SetupError("Could not start: [WinError 2] C:/secret"), 500, "server log"),
    ],
)
def test_run_route_answers_with_fixed_words_never_the_exception(
    client, monkeypatch, error, status, words
):
    test_client, runner = client

    def start(*_a, **_k):
        raise error

    monkeypatch.setattr(runner, "start", start)
    resp = test_client.post("/api/setup/run", json={})
    assert resp.status_code == status
    assert words in resp.json()["detail"]
    assert "secret" not in resp.json()["detail"]


def test_a_failed_pull_shows_fixed_words_and_logs_lemonades(caplog):
    runner = sr.SetupRunner()
    runner._on_event(json.loads(_plan_event()))
    runner._on_event(
        {"event": "error", "model": QWEN, "message": "HTTP 500 at /secret/path"}
    )
    row = _models(runner)[QWEN]
    assert row["error_kind"] == "lemonade"
    assert "/secret/path" not in row["error"] and "download log" in row["error"]
    # The row's own log, under its chevron, has Lemonade's words.
    assert "Failed: HTTP 500 at /secret/path" in row["log"]
    assert "/secret/path" in caplog.text


def test_progress_after_cancel_does_not_revive_a_cancelled_row():
    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._on_event(json.loads(_plan_event()))
    run.state = "cancelled"
    with runner._lock:
        for entry in runner._models.values():
            if entry.state == "queued":
                entry.state = "cancelled"
    runner._consume(
        run,
        json.dumps(
            {"event": "progress", "model": QWEN, "bytes_done": 5, "bytes_total": 9}
        ),
    )
    qwen = _models(runner)[QWEN]
    assert qwen["state"] == "cancelled" and qwen["can_retry"]
    assert not runner.busy_with(QWEN)


def test_resume_after_a_cancelled_run_still_reports_its_progress():
    """A single download feeds _on_event directly; the cancelled run must not mute it."""
    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._consume(run, _plan_event())
    run.state = "cancelled"
    with runner._lock:
        runner._models[QWEN].state = "cancelled"
    runner._on_event(
        {
            "event": "progress",
            "model": QWEN,
            "bytes_done": 11_650_000_000,
            "bytes_total": 23_300_000_000,
            "speed_bps": 10,
        }
    )
    qwen = _models(runner)[QWEN]
    assert qwen["state"] == "downloading" and qwen["bytes_done"] == 11_650_000_000
    assert runner.status()["overall"]["percent"] is not None
    runner._on_event({"event": "retrying", "model": QWEN, "until": 1_900_000_000})
    assert _models(runner)[QWEN]["retrying_until"] == 1_900_000_000
    runner._on_event(
        {"event": "error", "model": QWEN, "message": "No space left on device"}
    )
    qwen = _models(runner)[QWEN]
    assert qwen["state"] == "failed" and qwen["error_kind"] == "disk_full"


def test_a_failed_run_never_shows_unchecked_models_as_ready():
    runner = sr.SetupRunner()
    runner._on_event(json.loads(_plan_event()))
    with runner._lock:
        runner._settle_models(None, {}, run_failed=True)
    embed = _models(runner)[EMBED]
    # Not ready, and not failed either: nothing is wrong with the file.
    assert embed["state"] == "unchecked"
    assert embed["error_kind"] is None and embed["error"] is None
    assert not embed["can_retry"]
    assert runner.status()["overall"]["failed"] == 0


def test_a_later_download_failing_keeps_the_earlier_models(monkeypatch):
    """gaia init exits 1 when any one pull fails; what already downloaded stays."""
    lines = [
        _plan_event(),
        json.dumps({"event": "model_done", "model": QWEN, "downloaded": True}),
        json.dumps(
            {"event": "error", "model": GEMMA, "message": "Connection reset by peer"}
        ),
    ]
    _popen(
        monkeypatch,
        _FakeProc(io.BytesIO((chr(10).join(lines) + chr(10)).encode()), returncode=1),
    )
    monkeypatch.setattr(
        sr, "check", lambda *_a, **_k: pytest.fail("a failed run is not load-checked")
    )
    runner = sr.SetupRunner()
    runner.start(skip_chat_model=False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["state"] == "failed"
    rows = {m["id"]: m for m in view["models"]}
    for kept in (QWEN, EMBED):
        assert rows[kept]["state"] == "unchecked", rows[kept]
        assert rows[kept]["error_kind"] is None and not rows[kept]["can_retry"]
        assert rows[kept]["bytes_done"] == rows[kept]["bytes_total"]
    assert rows[GEMMA]["state"] == "failed" and rows[GEMMA]["can_retry"]
    # Only the real failure counts.
    assert view["overall"]["failed"] == 1
    # The run says a model is why it stopped, so the UI shows no second error.
    assert view["model_failed"] is True


def test_a_run_that_stopped_for_another_reason_names_no_model(monkeypatch):
    lines = [_plan_event(), json.dumps({"event": "model_done", "model": QWEN})]
    _popen(
        monkeypatch,
        _FakeProc(io.BytesIO((chr(10).join(lines) + chr(10)).encode()), returncode=1),
    )
    runner = sr.SetupRunner()
    runner.start(skip_chat_model=False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["state"] == "failed" and view["model_failed"] is False


def test_a_load_check_that_never_ran_leaves_models_unchecked(monkeypatch):
    lines = [_plan_event(), json.dumps({"event": "model_done", "model": QWEN})]
    _popen(monkeypatch, _FakeProc(io.BytesIO((chr(10).join(lines) + chr(10)).encode())))

    def broken(*_a, **_k):
        raise sr.SetupError("The model server stopped answering.")

    monkeypatch.setattr(sr, "check", broken)
    runner = sr.SetupRunner()
    runner.start(skip_chat_model=False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    assert view["state"] == "failed"
    assert {m["id"]: m["state"] for m in view["models"]}[QWEN] == "unchecked"
    assert view["overall"]["failed"] == 0


def test_only_a_real_load_failure_is_a_verify_failure():
    runner = sr.SetupRunner()
    runner._on_event(json.loads(_plan_event()))
    runner._on_event({"event": "model_done", "model": QWEN})
    with runner._lock:
        runner._settle_models({EMBED}, {QWEN: "would not load"})
    rows = _models(runner)
    assert rows[QWEN]["state"] == "failed" and rows[QWEN]["error_kind"] == "verify"
    assert rows[EMBED]["state"] == "ready"


def test_a_resume_starts_from_the_bytes_already_down():
    """The first poll after Resume must not read 0 and then jump to the partial."""
    runner = sr.SetupRunner()
    runner._on_event(json.loads(_plan_event()))
    runner._on_event(
        {
            "event": "progress",
            "model": QWEN,
            "bytes_done": 9_000_000_000,
            "bytes_total": 23_300_000_000,
        }
    )
    runner.cancel(QWEN)
    with runner._lock:
        runner._models[QWEN].state = "cancelled"
    gate = threading.Event()
    runner.download_model(_PullClient([], gate=gate), QWEN)
    try:
        row = _models(runner)[QWEN]
        assert row["state"] == "downloading"
        assert row["bytes_done"] == 9_000_000_000
        assert row["bytes_total"] == 23_300_000_000
    finally:
        gate.set()


def test_remove_is_refused_while_setup_runs_or_under_another_spelling(client):
    test_client, runner = client
    runner._run = sr.SetupRun(skip_chat_model=False)
    r = test_client.post("/api/setup/models/remove", json={"model_id": GEMMA})
    assert r.status_code == 409
    runner._run.state = "ready"
    runner._models[EMBED] = sr.ModelProgress(id=EMBED, state="downloading")
    for spelling in (EMBED.removeprefix("user."), EMBED.upper()):
        r = test_client.post("/api/setup/models/remove", json={"model_id": spelling})
        assert r.status_code == 409, spelling


def test_resumed_bytes_come_from_the_plan_not_the_first_progress_event():
    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._consume(run, _plan_event())
    runner._consume(
        run,
        json.dumps({"event": "progress", "model": QWEN, "bytes_done": 10_000_000}),
    )
    assert _models(runner)[QWEN]["downloaded_before_gb"] is None

    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    event = json.loads(_plan_event())
    event["models"][1]["bytes_on_disk"] = 13_700_000_000
    runner._consume(run, json.dumps(event))
    runner._consume(
        run,
        json.dumps({"event": "progress", "model": QWEN, "bytes_done": 13_900_000_000}),
    )
    assert _models(runner)[QWEN]["downloaded_before_gb"] == 13.7


def test_a_single_download_reads_what_is_already_on_disk(monkeypatch):
    from gaia.installer import init_command

    seen = {}

    def on_disk(_client, ids, store):
        seen["store"] = store
        return {m: 2_500_000_000 for m in ids}

    monkeypatch.setattr(init_command, "bytes_already_on_disk", on_disk)
    runner = sr.SetupRunner()
    gate = threading.Event()
    client = _PullClient(
        [],
        gate=gate,
        system_info={"model_storage": {"path": "/models", "free_bytes": 10**12}},
    )
    runner.download_model(client, GEMMA)
    try:
        assert _models(runner)[GEMMA]["downloaded_before_gb"] == 2.5
        assert seen["store"] == "/models"
    finally:
        gate.set()
    assert _wait_until(lambda: _models(runner)[GEMMA]["state"] == "ready")


def test_a_brand_new_single_download_is_not_called_resumed():
    runner = sr.SetupRunner()
    client = _PullClient(
        [
            {
                "event": "progress",
                "file": "m.gguf",
                "bytes_downloaded": 10_000_000,
                "bytes_total": 6_000_000_000,
            },
            {"event": "complete"},
        ]
    )
    runner.download_model(client, GEMMA)
    assert _wait_until(lambda: _models(runner)[GEMMA]["state"] == "ready")
    assert _models(runner)[GEMMA]["downloaded_before_gb"] is None


# ── Stage logs and the troubleshooting report ──────────────────────────────


def test_a_failed_piece_reports_whether_a_retry_may_help():
    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._on_event({"event": "component", "component": "server", "state": "done"})
    runner._on_event({"event": "component", "component": "agent", "state": "active"})
    runner._on_event(
        {"event": "component", "component": "agent", "state": "failed", "kind": "other"}
    )
    view = run.view()
    assert view["components"] == {"server": "done", "agent": "failed"}
    assert view["component_kinds"] == {"agent": "other"}

    runner._on_event({"event": "component", "component": "agent", "state": "done"})
    assert run.view()["component_kinds"] == {}


def test_each_stage_keeps_its_own_log():
    runner = sr.SetupRunner()
    check = sr.SetupRun(skip_chat_model=False, server_only=True)
    runner._run = check
    runner._consume(check, "Starting Lemonade Server...")
    runner._on_event({"event": "component", "component": "server", "state": "done"})

    # The full run replaces the server's run; the checks' log stays.
    runner._check_log = list(check.stage_logs["check"])
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._on_event({"event": "plan", "models": [{"id": "m", "downloaded": False}]})
    runner._on_event({"event": "model_done", "model": "m"})
    runner._on_event(
        {
            "event": "test",
            "job": "chat",
            "subject": "m",
            "state": "failed",
            "error": "boom",
            "seconds": 1.5,
        }
    )
    runner._consume(run, "after the tests")

    logs = runner.status()["logs"]
    assert logs["check"] == ["Starting Lemonade Server...", "server: done"]
    assert logs["download"] == ["Plan: m", "m: downloaded"]
    assert logs["test"] == ["Test chat m: failed in 1.5 s: boom", "after the tests"]


def test_the_screen_gets_the_tail_and_the_report_gets_everything():
    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    for i in range(sr.STAGE_LOG_SHOWN + 50):
        runner._consume(run, f"line {i}")

    assert len(runner.status()["logs"]["download"]) == sr.STAGE_LOG_SHOWN
    assert runner.status()["logs"]["download"][-1] == f"line {sr.STAGE_LOG_SHOWN + 49}"
    assert len(runner.report()["logs"]["download"]) == sr.STAGE_LOG_SHOWN + 50


def test_a_cancelled_run_says_so_in_its_log(monkeypatch):
    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
    )
    assert runner.cancel()
    runner._proc.wait(timeout=10)
    assert run.stage_logs["download"][-1] == "Stopped by the user"


def test_report_route_bundles_setups_record(client, gaia_home, monkeypatch):
    http, runner = client
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._consume(run, "something went wrong")
    monkeypatch.setattr(setup_router, "build_plan", lambda: {"profile": "gaia"})
    written = {}

    def fake_diagnostics(argv, **_kw):
        out = argv[argv.index("--output") + 1]
        written["record"] = json.loads((gaia_home / sr.SETUP_REPORT).read_text())
        with open(out, "wb") as f:
            f.write(b"tgz")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(sr.subprocess, "run", fake_diagnostics)
    res = http.post("/api/setup/report")
    assert res.status_code == 200, res.text
    body = res.json()
    assert sr.REPORT_NAME_RE.fullmatch(body["name"])
    assert written["record"]["logs"]["download"] == ["something went wrong"]
    assert written["record"]["plan"] == {"profile": "gaia"}

    got = http.get(f"/api/setup/report/{body['name']}")
    assert got.status_code == 200
    assert got.content == b"tgz"


@pytest.mark.parametrize("name", ["../config.json", "gaia.log", "diagnostics-1.tgz"])
def test_report_route_serves_nothing_but_a_report(client, gaia_home, name):
    http, _ = client
    gaia_home.mkdir(parents=True, exist_ok=True)
    (gaia_home / "gaia.log").write_text("secret")
    assert http.get(f"/api/setup/report/{name}").status_code == 404


def test_report_route_names_a_failed_bundle(client, monkeypatch):
    http, _ = client
    monkeypatch.setattr(setup_router, "build_plan", lambda: {})
    monkeypatch.setattr(
        sr.subprocess,
        "run",
        lambda argv, **_kw: subprocess.CompletedProcess(
            argv, 1, "", "❌ Error writing x: disk full"
        ),
    )
    res = http.post("/api/setup/report")
    assert res.status_code == 500
    assert "disk full" in res.json()["detail"]


# ── Each download's own log ────────────────────────────────────────────────


def test_each_download_keeps_its_own_lines_and_one_progress_bar():
    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._on_event(
        {
            "event": "plan",
            "models": [{"id": "a-GGUF"}, {"id": "b-GGUF", "downloaded": True}],
        }
    )
    runner._consume(run, "Step 2/5: Downloading models for 'gaia' profile...")
    runner._consume(run, "Downloading: a-GGUF")
    for pct in (10, 40, 90):
        runner._consume(run, f"[====------] {pct}% ({pct} MB/100 MB)")
    runner._on_event({"event": "model_done", "model": "a-GGUF"})
    runner._consume(run, "Step 3/5: Installing Python dependencies...")

    rows = {m["id"]: m for m in runner.status()["models"]}
    assert rows["a-GGUF"]["log"] == [
        "Downloading: a-GGUF",
        "[====------] 90% (90 MB/100 MB)",
        "Downloaded",
    ]
    assert rows["b-GGUF"]["log"] == ["Already on this PC"]


def test_a_failed_download_says_why_on_its_row():
    runner = sr.SetupRunner()
    runner._run = sr.SetupRun(skip_chat_model=False)
    runner._on_event({"event": "plan", "models": [{"id": "a-GGUF"}]})
    runner._on_event(
        {"event": "progress", "model": "a-GGUF", "bytes_done": 1, "bytes_total": 9}
    )
    runner._on_event({"event": "retrying", "model": "a-GGUF", "until": 1})
    runner._on_event(
        {"event": "error", "model": "a-GGUF", "message": "connection reset"}
    )
    log = runner.status()["models"][0]["log"]
    assert log == ["Connection dropped; still trying", "Failed: connection reset"]


def test_the_row_shows_the_tail_and_the_report_keeps_the_rest():
    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._on_event({"event": "plan", "models": [{"id": "a-GGUF"}]})
    runner._consume(run, "Downloading: a-GGUF")
    for i in range(sr.MODEL_LOG_SHOWN + 20):
        runner._consume(run, f"note {i}")
    assert len(runner.status()["models"][0]["log"]) == sr.MODEL_LOG_SHOWN
    assert len(runner.report()["models"][0]["log"]) == sr.MODEL_LOG_SHOWN + 21


def test_an_error_with_a_percent_is_kept_beside_the_progress_bar():
    m = sr.ModelProgress(id="a-GGUF")
    m.add_log("[=====>    ] 45%")
    m.add_log("Error: disk 95% full")
    m.add_log("[======>   ] 46%")
    assert m.log == ["[=====>    ] 45%", "Error: disk 95% full", "[======>   ] 46%"]
    m.add_log("Disk check: 40% of drive free")
    m.add_log("Lemonade: resuming at 12% done")
    assert m.log[-2:] == [
        "Disk check: 40% of drive free",
        "Lemonade: resuming at 12% done",
    ]


def test_output_after_a_failed_download_is_not_put_on_its_row():
    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._on_event({"event": "plan", "models": [{"id": "a-GGUF"}]})
    runner._consume(run, "Downloading: a-GGUF")
    runner._on_event({"event": "error", "model": "a-GGUF", "message": "boom"})
    runner._consume(run, "Testing chat model inference...")
    assert runner._models["a-GGUF"].log == ["Downloading: a-GGUF", "Failed: boom"]


def test_a_new_run_forgets_which_model_the_last_one_was_downloading(monkeypatch):
    _popen(monkeypatch, _FakeProc(io.BytesIO(b""), returncode=1))
    runner = sr.SetupRunner()
    runner._log_model = "a-GGUF"
    runner.start(False, chat_model_for=_local_chat)
    assert runner._log_model is None


def test_the_where_to_look_hint_stays_with_the_failed_download():
    runner = sr.SetupRunner()
    run = sr.SetupRun(skip_chat_model=False)
    runner._run = run
    runner._on_event({"event": "plan", "models": [{"id": "a-GGUF"}]})
    runner._consume(run, "Downloading: a-GGUF")
    runner._on_event({"event": "error", "model": "a-GGUF", "message": "boom"})
    runner._consume(
        run, "   Read /home/you/.gaia/lemonade/lemonade.log for the server's own error."
    )
    runner._consume(run, "Testing chat model inference...")
    assert runner._models["a-GGUF"].log == [
        "Downloading: a-GGUF",
        "Failed: boom",
        "Read /home/you/.gaia/lemonade/lemonade.log for the server's own error.",
    ]


def test_gaias_own_console_bar_collapses_too():
    m = sr.ModelProgress(id="a-GGUF")
    m.add_log("   [━━━━──────]  40%  2.00 GB / 5.00 GB")
    m.add_log("   [━━━━━━────]  60%  3.00 GB / 5.00 GB")
    assert m.log == ["   [━━━━━━────]  60%  3.00 GB / 5.00 GB"]


def _check_held_until_cancelled(monkeypatch, result):
    """A load check that waits for Cancel, then answers *result* (or raises it)."""
    entered, release = threading.Event(), threading.Event()

    def check(*_a, **_k):
        entered.set()
        release.wait(5)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(sr, "check", check)
    return entered, release


@pytest.mark.parametrize(
    "result",
    [
        {"ready": False, "reasons": ["Gemma would not load"], "models": []},
        sr.SetupError("Setup could not be checked (exit 2)."),
    ],
    ids=["not-ready", "check-error"],
)
def test_a_check_that_ends_after_cancel_leaves_the_run_cancelled(monkeypatch, result):
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))
    entered, release = _check_held_until_cancelled(monkeypatch, result)
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    assert entered.wait(5)
    assert runner.cancel() is True
    release.set()
    runner._thread.join(5)
    view = runner.status()
    assert view["state"] == "cancelled"
    assert view["error"] is None


def test_try_again_waits_for_a_cancelled_check_to_finish(monkeypatch):
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))
    entered, release = _check_held_until_cancelled(
        monkeypatch, {"ready": False, "reasons": ["late"], "models": []}
    )
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    assert entered.wait(5)
    runner.cancel()
    old = runner._thread
    threading.Timer(0.3, release.set).start()
    monkeypatch.setattr(sr, "check", lambda *a, **k: {"ready": True})
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))
    runner.start(False, chat_model_for=_local_chat)
    assert not old.is_alive()
    assert _wait_done(runner)["state"] == "ready"


def test_cancel_stops_the_running_load_check(monkeypatch):
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))
    check_proc = _CheckProc({}, 0, "", "")
    entered, release = threading.Event(), threading.Event()

    def check(*_a, on_start=None, **_k):
        on_start(check_proc)
        entered.set()
        release.wait(5)
        raise sr.SetupError("Setup could not be checked (exit -9).")

    monkeypatch.setattr(sr, "check", check)
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    assert entered.wait(5)
    runner.cancel()
    assert check_proc.killed
    release.set()
    runner._thread.join(5)
    assert runner.status()["state"] == "cancelled"


# ── an extra model's failed test: re-download only when the file is broken ──

VOICE = "kokoro-v1"


def _extra_run(monkeypatch, kind, error, job="voice"):
    """A run whose ticked extra *VOICE* failed its test with *kind*."""
    lines = [
        {
            "event": "plan",
            "models": [
                {"id": GEMMA, "bytes_total": 10, "downloaded": True},
                {"id": VOICE, "bytes_total": 10, "downloaded": True},
            ],
        },
        {"event": "test", "job": job, "subject": VOICE, "state": "running"},
        {
            "event": "test",
            "job": job,
            "subject": VOICE,
            "state": "failed",
            "error": error,
            "kind": kind,
            "seconds": 2.0,
        },
    ]
    out = "".join(json.dumps(line) + "\n" for line in lines).encode()
    _popen(monkeypatch, _FakeProc(io.BytesIO(out)))
    monkeypatch.setattr(
        sr,
        "check",
        lambda *_a, **_k: {"ready": True, "models": [{"id": GEMMA, "loaded": True}]},
    )
    monkeypatch.setattr(sr, "_agent_turn_problem", lambda model_id=None: None)
    runner = sr.SetupRunner()
    runner.start(
        skip_chat_model=False, extra_models=[VOICE], chat_model_for=_local_chat
    )
    view = _wait_done(runner)
    return runner, {m["id"]: m for m in view["models"]}[VOICE]


class _NoPullClient:
    """Lemonade for a retest: any download or delete is a test failure."""

    def get_system_info(self, timeout=None):
        return {}

    def ensure_model_downloaded(self, *_a, **_k):
        pytest.fail("a model whose file is fine must not be downloaded again")

    def delete_model(self, *_a, **_k):
        pytest.fail("a model whose file is fine must not be deleted")


@pytest.mark.parametrize(
    "kind,error,said",
    [
        ("memory", "Not enough free memory to load kokoro-v1", "enough free memory"),
        ("answer", "No audio came back", "nothing usable back (No audio came back)"),
        ("stuck", "It didn't finish within 5 minutes", "didn't answer"),
        ("window", "Context 4096 < 65536 required", "smaller context window"),
    ],
)
def test_an_extra_that_failed_its_test_for_a_fine_file_is_not_offered_a_redownload(
    monkeypatch, kind, error, said
):
    _runner, row = _extra_run(monkeypatch, kind, error)
    assert row["state"] == "failed" and row["can_retry"]
    assert row["error_kind"] == kind and row["error_kind"] != "verify"
    assert said in row["error"]
    assert "would not load" not in row["error"]
    assert "nothing is downloaded again" in row["error"]
    # Details keeps the test's own words, not only the friendly line.
    assert f"Test voice: {error}" in row["log"]


def test_an_extra_whose_file_did_not_load_is_still_offered_a_redownload(monkeypatch):
    runner, row = _extra_run(monkeypatch, "broken", "llama-server failed to start")
    assert row["state"] == "failed" and row["error_kind"] == "verify"
    assert "would not load: llama-server failed to start" in row["error"]
    # Retry is a download, not a retest.
    client = _PullClient([])
    runner.download_model(client, VOICE)
    assert _wait_until(lambda: _models(runner)[VOICE]["state"] == "ready")
    assert [c[0] for c in client.calls] == [VOICE]


def test_retry_on_a_fine_file_tests_it_again_without_downloading(monkeypatch):
    from gaia.installer import init_command

    runner, _row = _extra_run(monkeypatch, "memory", "Not enough free memory")
    gate, calls = threading.Event(), []

    def retest(client, model_id, job=None):
        calls.append((model_id, job))
        gate.wait(5)
        return (True, None, None)

    monkeypatch.setattr(init_command, "retest_model", retest)
    view = runner.download_model(_NoPullClient(), VOICE)
    row = {m["id"]: m for m in view["models"]}[VOICE]
    assert row["state"] == "verifying" and row["error"] is None
    # Remove must not delete a model under test.
    assert runner.busy_with(VOICE)
    gate.set()
    assert _wait_until(lambda: _models(runner)[VOICE]["state"] == "ready")
    assert calls == [(VOICE, "voice")]
    row = _models(runner)[VOICE]
    assert row["error_kind"] is None and row["log"][-1] == "Test passed"
    test_row = next(t for t in runner.status()["tests"] if t["subject"] == VOICE)
    assert test_row["state"] == "passed"


def test_a_second_timeout_on_model_retry_offers_a_redownload(monkeypatch):
    from gaia.installer import init_command

    runner, _row = _extra_run(monkeypatch, "stuck", "It didn't finish")
    assert runner._test_failures[VOICE] == 1
    monkeypatch.setattr(
        init_command,
        "retest_model",
        lambda client, model_id, job=None: (False, "It timed out again", "stuck"),
    )

    runner.download_model(_NoPullClient(), VOICE)

    assert _wait_until(lambda: _models(runner)[VOICE]["state"] == "failed")
    row = _models(runner)[VOICE]
    assert runner._test_failures[VOICE] == 2
    assert row["error_kind"] == "verify"
    assert "would not load" in row["error"]


def test_a_passing_model_retest_clears_its_timeout_count(monkeypatch):
    from gaia.installer import init_command

    runner, _row = _extra_run(monkeypatch, "stuck", "It didn't finish")
    assert runner._test_failures[VOICE] == 1
    monkeypatch.setattr(
        init_command,
        "retest_model",
        lambda client, model_id, job=None: (True, None, None),
    )

    runner.download_model(_NoPullClient(), VOICE)

    assert _wait_until(lambda: _models(runner)[VOICE]["state"] == "ready")
    assert VOICE not in runner._test_failures


def _failed_run_waiting_on_one_model_retest(only_test_failure):
    runner = sr.SetupRunner()
    runner._run = sr.SetupRun(
        skip_chat_model=False,
        state="failed",
        error=(
            "The final chat test failed"
            if not only_test_failure
            else "Model test failed"
        ),
        failed_by_tests=only_test_failure,
        chat_model_for=lambda _run: VOICE,
    )
    runner._models[VOICE] = sr.ModelProgress(
        id=VOICE,
        state="failed",
        error_kind="stuck",
        test_job="voice",
    )
    runner._record_test(
        runner._run,
        {
            "job": "voice",
            "subject": VOICE,
            "state": "failed",
            "error": "It timed out",
            "kind": "stuck",
        },
    )
    return runner


def test_the_last_model_retest_rechecks_setup_before_marking_ready(
    monkeypatch, gaia_home
):
    from gaia.installer import init_command

    runner = _failed_run_waiting_on_one_model_retest(only_test_failure=True)
    monkeypatch.setattr(
        init_command,
        "retest_model",
        lambda client, model_id, job=None: (True, None, None),
    )
    checks, turns = [], []

    def check(*_args, **_kwargs):
        checks.append(1)
        return {"ready": True, "models": [{"id": VOICE, "loaded": True}]}

    monkeypatch.setattr(sr, "check", check)
    monkeypatch.setattr(
        sr, "_agent_turn_problem", lambda model_id: turns.append(model_id)
    )

    runner.download_model(_NoPullClient(), VOICE)

    assert _wait_until(lambda: runner.status()["state"] == "ready")
    assert checks == [1] and turns == [VOICE]
    assert runner.status()["error"] is None
    assert (gaia_home / "chat" / "initialized").exists()


def test_a_retest_keeps_setup_failed_when_final_verification_fails(
    monkeypatch, gaia_home
):
    from gaia.installer import init_command

    runner = _failed_run_waiting_on_one_model_retest(only_test_failure=True)
    monkeypatch.setattr(
        init_command,
        "retest_model",
        lambda client, model_id, job=None: (True, None, None),
    )
    monkeypatch.setattr(
        sr,
        "check",
        lambda *_args, **_kwargs: {
            "ready": True,
            "models": [{"id": VOICE, "loaded": True}],
        },
    )
    monkeypatch.setattr(sr, "_agent_turn_problem", lambda _model_id: "no answer")

    runner.download_model(_NoPullClient(), VOICE)

    assert _wait_until(lambda: runner.status()["state"] == "failed")
    assert "got no answer" in runner.status()["error"]
    assert not (gaia_home / "chat" / "initialized").exists()


def test_a_model_retest_does_not_hide_another_setup_failure(monkeypatch, gaia_home):
    from gaia.installer import init_command

    runner = _failed_run_waiting_on_one_model_retest(only_test_failure=False)
    monkeypatch.setattr(
        init_command,
        "retest_model",
        lambda client, model_id, job=None: (True, None, None),
    )

    runner.download_model(_NoPullClient(), VOICE)

    assert _wait_until(lambda: _models(runner)[VOICE]["state"] == "ready")
    view = runner.status()
    assert view["state"] == "failed"
    assert view["error"] == "The final chat test failed"
    assert not (gaia_home / "chat" / "initialized").exists()


def test_a_retest_that_finds_the_file_broken_then_offers_a_redownload(monkeypatch):
    from gaia.installer import init_command

    runner, _row = _extra_run(monkeypatch, "stuck", "It didn't finish")
    monkeypatch.setattr(
        init_command,
        "retest_model",
        lambda client, model_id, job=None: (False, "bad magic", "broken"),
    )
    runner.download_model(_NoPullClient(), VOICE)
    assert _wait_until(lambda: _models(runner)[VOICE]["state"] == "failed")
    row = _models(runner)[VOICE]
    assert row["error_kind"] == "verify"
    assert "would not load: bad magic" in row["error"]


def test_a_retest_that_crashes_is_never_left_verifying(monkeypatch):
    from gaia.installer import init_command

    runner, _row = _extra_run(monkeypatch, "answer", "No audio came back")

    def retest(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(init_command, "retest_model", retest)
    runner.download_model(_NoPullClient(), VOICE)
    assert _wait_until(lambda: _models(runner)[VOICE]["state"] == "failed")
    row = _models(runner)[VOICE]
    # Still a fine file: Retry tests again, and the reason names the crash.
    assert row["error_kind"] in sr.TEST_KINDS
    assert "RuntimeError: boom" in row["error"]
    assert not runner.busy_with(VOICE)


def test_settle_takes_a_kind_per_model():
    runner = sr.SetupRunner()
    runner._on_event(json.loads(_plan_event()))
    runner._on_event({"event": "model_done", "model": QWEN, "downloaded": True})
    with runner._lock:
        runner._settle_models(
            {EMBED}, {QWEN: "short of memory"}, kinds={QWEN: "memory"}
        )
    rows = _models(runner)
    assert rows[QWEN]["state"] == "failed" and rows[QWEN]["error_kind"] == "memory"
    assert "Failed its test: short of memory" in rows[QWEN]["log"]


def test_cancel_during_the_load_check_leaves_no_row_verifying(monkeypatch):
    plan = json.dumps(
        {
            "event": "plan",
            "models": [{"id": GEMMA, "bytes_total": 10, "downloaded": True}],
        }
    )
    _popen(monkeypatch, _FakeProc(io.BytesIO((plan + "\n").encode())))
    check_proc = _CheckProc({}, 0, "", "")
    entered, release = threading.Event(), threading.Event()

    def check(*_a, on_start=None, **_k):
        on_start(check_proc)
        entered.set()
        release.wait(5)
        raise sr.SetupError("Setup could not be checked (exit -9).")

    monkeypatch.setattr(sr, "check", check)
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=_local_chat)
    assert entered.wait(5)
    assert _models(runner)[GEMMA]["state"] == "verifying"
    runner.cancel()
    release.set()
    runner._thread.join(5)
    assert _models(runner)[GEMMA]["state"] == "unchecked"


# ── a main-plan model's failed load: re-download only when the file is broken ──


def _load_failed_run(monkeypatch, kind, error="raw load error"):
    """A run whose load check failed Gemma, from the plan, with *kind*."""
    plan = json.dumps(
        {
            "event": "plan",
            "models": [
                {"id": GEMMA, "bytes_total": 10, "downloaded": True},
                {"id": EMBED, "bytes_total": 10, "downloaded": True},
            ],
        }
    )
    _popen(monkeypatch, _FakeProc(io.BytesIO((plan + "\n").encode())))
    monkeypatch.setattr(
        sr,
        "check",
        lambda *_a, **_k: {
            "ready": False,
            "stage": "load",
            "reasons": [f"Model '{GEMMA}' failed"],
            "models": [
                {"id": EMBED, "loaded": True},
                {"id": GEMMA, "loaded": False, "error": error, "kind": kind},
            ],
        },
    )
    runner = sr.SetupRunner()
    runner.start(skip_chat_model=False, chat_model_for=_local_chat)
    view = _wait_done(runner)
    rows = {m["id"]: m for m in view["models"]}
    assert rows[EMBED]["state"] == "ready"
    assert all(m["state"] != "verifying" for m in view["models"])
    return runner, rows[GEMMA]


@pytest.mark.parametrize(
    "kind, said",
    [
        ("memory", "enough free memory"),
        ("stuck", "didn't answer"),
        ("answer", "nothing usable back"),
        ("window", "smaller context window"),
    ],
)
def test_a_plan_model_that_failed_its_load_for_a_fine_file_is_not_offered_a_redownload(
    monkeypatch, kind, said
):
    _runner, row = _load_failed_run(monkeypatch, kind)
    assert row["state"] == "failed" and row["can_retry"]
    assert row["error_kind"] == kind
    assert said in row["error"] and "would not load" not in row["error"]
    assert "Load check: raw load error" in row["log"]


@pytest.mark.parametrize("kind", ["broken", None])
def test_a_plan_model_whose_file_did_not_load_is_still_offered_a_redownload(
    monkeypatch, kind
):
    _runner, row = _load_failed_run(monkeypatch, kind, "bad magic")
    assert row["error_kind"] == "verify"
    assert "would not load: bad magic" in row["error"]


def test_retry_on_a_plan_model_repeats_its_load_without_downloading(monkeypatch):
    from gaia.installer import init_command

    runner, _row = _load_failed_run(monkeypatch, "memory")
    calls = []

    def retest(client, model_id, job=None):
        calls.append((model_id, job))
        return (True, None, None)

    monkeypatch.setattr(init_command, "retest_model", retest)
    runner.download_model(_NoPullClient(), GEMMA)
    assert _wait_until(lambda: _models(runner)[GEMMA]["state"] == "ready")
    assert calls == [(GEMMA, init_command.LOAD_CHECK_JOB)]
    assert not runner.busy_with(GEMMA)
    # The load check is no Test-step row; a retest of it must not add one.
    assert not any(t["subject"] == GEMMA for t in runner.status()["tests"])


def test_report_waits_for_a_failed_runs_problem_report(monkeypatch):
    """A report asked for the moment a run fails still names its problem report."""
    runner = sr.SetupRunner()
    run = sr.SetupRun(False, state="failed")
    runner._run = run
    saving = threading.Event()

    def slow_record(_run, _cause):
        saving.set()
        time.sleep(0.5)
        return "GF-20261104-192233-3f2a"

    monkeypatch.setattr(sr, "_record_setup_failure", slow_record)
    with runner._lock:
        runner._recording = threading.Thread(
            target=runner._record_failure, args=(run, None), daemon=True
        )
    runner._recording.start()
    assert saving.wait(5)

    report = runner.report()

    assert any(
        "GF-20261104-192233-3f2a" in line
        for line in report["log_tail"]
        + [line for lines in report["logs"].values() for line in lines]
    )
    assert "problem_report" not in report


def test_report_says_when_the_problem_report_is_still_saving(monkeypatch):
    runner = sr.SetupRunner()
    runner._run = sr.SetupRun(False, state="failed")
    release = threading.Event()
    monkeypatch.setattr(sr, "_RECORD_WAIT_S", 0.1)
    runner._recording = threading.Thread(target=release.wait, args=(5,), daemon=True)
    runner._recording.start()
    try:
        assert "still being saved" in runner.report()["problem_report"]
    finally:
        release.set()


def test_failed_model_test_from_init_sets_failed_by_tests(monkeypatch):
    """The retry eligibility flag must come from real init progress, not fixtures."""
    output = "\n".join(
        [
            json.dumps(
                {
                    "event": "plan",
                    "models": [{"id": VOICE, "bytes_total": 10, "downloaded": True}],
                }
            ),
            json.dumps(
                {
                    "event": "test",
                    "job": "voice",
                    "subject": VOICE,
                    "state": "failed",
                    "error": "It did not finish",
                    "kind": "stuck",
                }
            ),
            "",
        ]
    ).encode()
    _popen(monkeypatch, _FakeProc(io.BytesIO(output), returncode=1))
    monkeypatch.setattr(sr, "_record_setup_failure", lambda *_args: None)

    runner = sr.SetupRunner()
    runner.start(skip_chat_model=False, chat_model_for=_local_chat)
    view = _wait_done(runner)

    assert view["state"] == "failed"
    assert view["model_failed"] is False
    assert view["tests"][0]["state"] == "failed"
    assert view["tests"][0]["subject"] == VOICE
    row = _models(runner)[VOICE]
    assert row["state"] == "failed"
    assert row["error_kind"] == "stuck"
    assert (
        view["failed_by_tests"]
        if "failed_by_tests" in view
        else runner._run.failed_by_tests
    )


def test_failed_model_test_does_not_hide_a_failed_component(monkeypatch):
    """A component failure keeps setup failed even when a model test also failed."""
    output = "\n".join(
        [
            json.dumps(
                {
                    "event": "plan",
                    "models": [{"id": VOICE, "bytes_total": 10, "downloaded": True}],
                }
            ),
            json.dumps(
                {
                    "event": "component",
                    "component": "server",
                    "state": "failed",
                    "kind": "other",
                }
            ),
            json.dumps(
                {
                    "event": "test",
                    "job": "voice",
                    "subject": VOICE,
                    "state": "failed",
                    "error": "It did not finish",
                    "kind": "stuck",
                }
            ),
            "",
        ]
    ).encode()
    _popen(monkeypatch, _FakeProc(io.BytesIO(output), returncode=1))
    monkeypatch.setattr(sr, "_record_setup_failure", lambda *_args: None)

    runner = sr.SetupRunner()
    runner.start(skip_chat_model=False, chat_model_for=_local_chat)
    view = _wait_done(runner)

    assert view["state"] == "failed"
    assert view["model_failed"] is False
    assert runner._run.failed_by_tests is False
    assert view["components"] == {"server": "failed"}
    assert _models(runner)[VOICE]["state"] == "failed"
