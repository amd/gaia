# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""First-run setup: ``gaia init --check`` / ``gaia init`` driven from the Agent UI.

No real ``gaia init`` runs: ``subprocess.run`` / ``subprocess.Popen`` are
replaced with fakes that assert the argv the runner would have used.
"""

import io
import subprocess
import sys
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.ui import setup_runner as sr
from gaia.ui.routers import setup as setup_router


@pytest.fixture(autouse=True)
def gaia_home(tmp_path, monkeypatch):
    home = tmp_path / "gaia-home"
    monkeypatch.setenv("GAIA_HOME", str(home))
    # GAIA's own Lemonade unless a test says the user chose theirs.
    monkeypatch.setattr(sr, "users_lemonade_url", lambda: None)
    monkeypatch.setattr(setup_router, "users_lemonade_url", lambda: None)
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
    assert "--skip-chat-model" not in args
    assert sr.run_args(True)[-1] == "--skip-chat-model"


def test_run_command_is_what_a_person_would_type():
    assert sr.run_command(False) == "gaia init --profile gaia"
    assert sr.run_command(True) == "gaia init --profile gaia --skip-chat-model"
    assert sr.run_command(False, server_only=True) == (
        "gaia init --profile gaia --skip-models"
    )


def test_run_args_ask_for_machine_readable_progress():
    assert "--progress-json" in sr.run_args(False)
    assert "--progress-json" not in sr.run_command(False)


def test_a_run_sets_up_the_chat_model_the_screen_showed():
    args = sr.run_args(False, chat_model="Gemma-4-E4B-it-GGUF")
    assert args[-2:] == ["--chat-model", "Gemma-4-E4B-it-GGUF"]
    assert sr.run_command(False, chat_model="Gemma-4-E4B-it-GGUF") == (
        "gaia init --profile gaia --chat-model Gemma-4-E4B-it-GGUF"
    )


def test_run_route_pins_the_planned_chat_model(client, monkeypatch):
    test_client, _ = client
    calls = []
    _popen(monkeypatch, _FakeProc(_BlockingStdout()), calls)
    started = test_client.post(
        "/api/setup/run", json={"chat_model": "Gemma-4-E4B-it-GGUF"}
    )
    assert started.status_code == 200
    assert started.json()["chat_model"] == "Gemma-4-E4B-it-GGUF"
    argv = calls[0][0]
    assert argv[argv.index("--chat-model") + 1] == "Gemma-4-E4B-it-GGUF"
    test_client.post("/api/setup/cancel")


def test_a_server_only_run_downloads_no_models():
    args = sr.run_args(False, server_only=True)
    assert "--skip-models" in args
    assert "--skip-models" not in sr.run_args(False)


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


def _fake_run(monkeypatch, returncode, stdout="", stderr="", calls=None):
    def run(argv, **kwargs):
        if calls is not None:
            calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(sr.subprocess, "run", run)


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


def test_check_timeout_is_an_error(monkeypatch):
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(sr.subprocess, "run", run)
    with pytest.raises(sr.SetupError, match="took too long"):
        sr.check()


# ── marker ─────────────────────────────────────────────────────────────────


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

    def wait(self):
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

    def check(skip_chat_model, load):
        checks.append((skip_chat_model, load))
        return {"ready": True}

    monkeypatch.setattr(sr, "check", check)
    runner = sr.SetupRunner()
    first = runner.start(skip_chat_model=True)
    assert first["state"] == "running"
    view = _wait_done(runner)
    assert view["state"] == "ready"
    assert [s["status"] for s in view["steps"]] == ["done", "done", "done"]
    assert checks == [(True, True)]
    argv, kwargs = calls[0]
    assert argv[3:] == sr.run_args(True)
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert "[====] 100% (5 MB/5 MB)" in view["log_tail"]


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
    runner.start(skip_chat_model=False)
    view = _wait_done(runner)
    assert view["chat_model"] == "Qwen3.6-35B-A3B-GGUF"
    assert view["steps"][0]["label"] == "Use your Lemonade Server"
    assert "Qwen3.6-35B-A3B-GGUF" in view["steps"][1]["detail"]


_JSON_OUTPUT = (
    b"Step 1/5: Starting Lemonade Server...\n"
    b'{"event": "chat_model", "model": "Gemma-4-E4B-it-GGUF", "user_set": false, '
    b'"skipped": [{"id": "Qwen3.6-35B-A3B-GGUF", "reason": "not enough free memory '
    b'right now: it needs about 26.1 GB and only 20.4 GB is free"}]}\n'
    b"Step 2/5: Downloading models for 'gaia' profile...\n"
    b'{"event": "downloads", "models": [{"id": "Gemma-4-E4B-it-GGUF", "size_gb": 5.6}, '
    b'{"id": "user.embeddinggemma-300m-GGUF", "size_gb": 0.3}], "total_gb": 5.9}\n'
    b"   Downloading: Gemma-4-E4B-it-GGUF\n"
    b'{"event": "download", "model": "Gemma-4-E4B-it-GGUF", "bytes_done": 1000000000, '
    b'"bytes_total": 4000000000, "speed_bps": 50000000.0}\n'
)


def test_json_progress_names_the_model_bytes_speed_and_time_left(monkeypatch):
    stdout = _BlockingStdout(_JSON_OUTPUT)
    _popen(monkeypatch, _FakeProc(stdout))
    runner = sr.SetupRunner()
    runner.start(False)
    deadline = time.time() + 3
    while time.time() < deadline:
        view = runner.status()
        if view.get("download"):
            break
        time.sleep(0.02)
    assert view["download"] == {
        "model": "Gemma-4-E4B-it-GGUF",
        "index": 1,
        "count": 2,
        "bytes_done": 1000000000,
        "bytes_total": 4000000000,
        "speed_bps": 50000000.0,
        "eta_s": 60,
    }
    assert view["percent"] == 25
    assert view["download_gb"] == 5.9
    assert view["chat_model"] == "Gemma-4-E4B-it-GGUF"
    assert view["passed_over"][0]["id"] == "Qwen3.6-35B-A3B-GGUF"
    assert "20.4 GB is free" in view["passed_over"][0]["reason"]
    assert "5.9 GB" in view["steps"][1]["detail"]
    # The JSON is for the screen; the details stay readable.
    assert not any(line.startswith("{") for line in view["log_tail"])
    assert runner.cancel() is True
    _wait_done(runner)


def _download(done, total, speed):
    return {
        "event": "download",
        "model": "m",
        "bytes_done": done,
        "bytes_total": total,
        "speed_bps": speed,
    }


def test_a_download_with_no_total_shows_bytes_without_a_percentage():
    run = sr.SetupRun(skip_chat_model=False)
    run.apply(_download(500_000_000, None, 50_000_000.0))
    view = run.view()
    assert view["download"]["bytes_done"] == 500_000_000
    assert view["download"]["bytes_total"] is None
    assert view["download"]["eta_s"] is None
    assert view["percent"] is None


def test_a_download_that_goes_quiet_stops_claiming_a_speed(monkeypatch):
    """Lemonade sends nothing while it stalls or hashes a file; stale numbers lie."""
    clock = [100.0]
    monkeypatch.setattr(sr.time, "monotonic", lambda: clock[0])
    run = sr.SetupRun(skip_chat_model=False)
    run.apply(_download(1_000_000_000, 4_000_000_000, 50_000_000.0))
    assert run.view()["download"]["eta_s"] == 60
    clock[0] += sr.STALE_DOWNLOAD_S + 1
    view = run.view()
    assert view["download"]["speed_bps"] == 0.0
    assert view["download"]["eta_s"] is None
    assert view["download"]["bytes_done"] == 1_000_000_000


def test_unknown_or_broken_json_lines_are_kept_as_output(monkeypatch):
    out = b'{"event": "something_new"}\n{not json\n'
    _popen(monkeypatch, _FakeProc(io.BytesIO(out)))
    monkeypatch.setattr(sr, "check", lambda *_a, **_k: {"ready": True})
    runner = sr.SetupRunner()
    runner.start(False)
    view = _wait_done(runner)
    assert "{not json" in view["log_tail"]


def test_a_server_only_run_is_ready_without_loading_models(monkeypatch):
    calls = []
    _popen(
        monkeypatch, _FakeProc(io.BytesIO(b"Step 1/2: Starting Lemonade...\n")), calls
    )
    monkeypatch.setattr(
        sr, "check", lambda *a, **k: pytest.fail("no models to check yet")
    )
    runner = sr.SetupRunner()
    runner.start(False, server_only=True)
    view = _wait_done(runner)
    assert view["state"] == "ready"
    assert view["server_only"] is True
    assert [s["status"] for s in view["steps"]] == ["done", "pending", "pending"]
    assert "--skip-models" in calls[0][0]
    assert not sr.is_initialized()


def test_init_error_lines_are_not_wrapped_at_80_columns(monkeypatch):
    """The failure reason is the last ❌ line; Rich would wrap it in a pipe."""
    from rich.console import Console

    calls = []
    _popen(monkeypatch, _FakeProc(io.BytesIO(b"")), calls)
    monkeypatch.setattr(sr, "check", lambda *a, **k: {"ready": True})
    runner = sr.SetupRunner()
    runner.start(False)
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
    runner.start(False)
    deadline = time.time() + 3
    while time.time() < deadline:
        view = runner.status()
        if view["text"] == "Downloading user.embeddinggemma-300m-GGUF":
            break
        time.sleep(0.02)
    assert view["text"] == "Downloading user.embeddinggemma-300m-GGUF"
    assert [s["status"] for s in view["steps"]] == ["done", "active", "pending"]
    # A phase line resets the percentage of the previous download.
    assert view["percent"] is None
    assert runner.cancel() is True
    _wait_done(runner)


def test_a_failed_run_reports_the_error_line(monkeypatch):
    out = _OUTPUT + b"   \xe2\x9d\x8c user.embeddinggemma-300m-GGUF - Request failed\n"
    _popen(monkeypatch, _FakeProc(io.BytesIO(out), returncode=1))
    monkeypatch.setattr(
        sr, "check", lambda *a, **k: pytest.fail("a failed run is not verified")
    )
    runner = sr.SetupRunner()
    runner.start(False)
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
    runner.start(False)
    view = _wait_done(runner)
    assert view["state"] == "failed"
    assert view["error"].endswith("boom")


def test_verify_not_ready_fails_with_the_reasons(monkeypatch):
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))
    monkeypatch.setattr(
        sr, "check", lambda *a, **k: {"ready": False, "reasons": ["a", "b"]}
    )
    runner = sr.SetupRunner()
    runner.start(False)
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
    runner.start(False)
    view = _wait_done(runner)
    assert view["state"] == "failed"
    assert view["error"] == "Checking setup took too long."


def test_an_unexpected_error_fails_the_run_and_frees_the_runner(monkeypatch):
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))

    def check(*_a, **_k):
        raise OSError("marker file is read-only")

    monkeypatch.setattr(sr, "check", check)
    runner = sr.SetupRunner()
    runner.start(False)
    view = _wait_done(runner)
    assert view["state"] == "failed"
    assert "marker file is read-only" in view["error"]
    # Not stranded in "verifying": a retry is allowed.
    monkeypatch.setattr(sr, "check", lambda *a, **k: {"ready": True})
    _popen(monkeypatch, _FakeProc(io.BytesIO(_OUTPUT)))
    runner.start(False)
    assert _wait_done(runner)["state"] == "ready"


def test_only_one_run_at_a_time_and_cancel(monkeypatch):
    proc = _FakeProc(_BlockingStdout())
    _popen(monkeypatch, proc)
    runner = sr.SetupRunner()
    runner.start(False)
    with pytest.raises(sr.SetupError, match="already running"):
        runner.start(False)
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
    with pytest.raises(sr.SetupError, match="Run it in a terminal"):
        runner.start(False)
    assert runner.status() is None


# ── Routes ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    runner = sr.SetupRunner()
    monkeypatch.setattr(setup_router, "runner", runner)
    app = FastAPI()
    app.include_router(setup_router.router)
    return TestClient(app, headers={"X-Gaia-UI": "1"}), runner


def test_check_route_adds_the_steps(client, monkeypatch):
    test_client, _ = client
    seen = {}

    def check(skip_chat_model, load):
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
    assert "exit 2" in resp.json()["detail"]


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


def test_run_route_can_start_only_the_server(client, monkeypatch):
    test_client, _ = client
    calls = []
    _popen(monkeypatch, _FakeProc(_BlockingStdout()), calls)
    started = test_client.post("/api/setup/run", json={"server_only": True})
    assert started.status_code == 200
    assert started.json()["server_only"] is True
    assert "--skip-models" in calls[0][0]
    test_client.post("/api/setup/cancel")
