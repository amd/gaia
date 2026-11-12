# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""First-run setup's corner cases: cancel, crashes, odd events, odd test results."""

import io
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from gaia.installer.init_command import (
    ChatModelChoice,
    CheckTimedOut,
    InitCommand,
    InitEvents,
)
from gaia.ui import setup_runner as sr


def _local_chat(run):
    """The model the app's chat opens on, for runs that don't test that choice."""
    return "Gemma-4-E4B-it-GGUF"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(sr, "users_lemonade_url", lambda: None)
    monkeypatch.setattr(sr, "server_problem", lambda: None)
    monkeypatch.setattr(sr, "_agent_turn_problem", lambda model_id=None: None)
    monkeypatch.setattr(sr, "check", lambda *a, **k: pytest.fail("no check expected"))


class _Proc:
    def __init__(self, data: bytes, returncode=0):
        self.stdout = io.BufferedReader(io.BytesIO(data))
        self.returncode = returncode
        self.killed = False
        self.waited = False

    def kill(self):
        self.killed = True

    def poll(self):
        return -9 if self.killed else None

    def wait(self, timeout=None):
        self.waited = True
        return -9 if self.killed else self.returncode


def _lines(*events):
    return b"".join(
        (json.dumps(e) if isinstance(e, dict) else e).encode() + b"\n" for e in events
    )


def _start(monkeypatch, proc, **kw):
    monkeypatch.setattr(sr.subprocess, "Popen", lambda *a, **k: proc)
    runner = sr.SetupRunner()
    runner.start(False, **kw, chat_model_for=_local_chat)
    return runner


def _wait(runner, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        v = runner.status()
        if v and v["state"] not in ("running", "verifying"):
            return v
        time.sleep(0.02)
    raise AssertionError(runner.status())


# ── runner ───────────────────────────────────────────────────────────────


def test_cancel_settles_a_downloaded_model_as_unchecked():
    """A model on disk was waiting for its load check: kept, never shown loading."""
    runner = sr.SetupRunner()
    runner._run = sr.SetupRun(skip_chat_model=False)
    runner._proc = MagicMock()
    runner._on_event(
        {
            "event": "plan",
            "models": [
                {
                    "id": "user.embeddinggemma-300m-GGUF",
                    "bytes_total": 1,
                    "downloaded": True,
                },
                {"id": "Gemma-4-E4B-it-GGUF", "bytes_total": 10},
            ],
        }
    )
    assert runner.cancel() is True
    states = {m["id"]: m["state"] for m in runner.status()["models"]}
    assert states["Gemma-4-E4B-it-GGUF"] == "cancelled"
    assert states["user.embeddinggemma-300m-GGUF"] == "unchecked", states


def test_a_failed_run_leaves_no_component_or_test_working(monkeypatch):
    proc = _Proc(
        _lines(
            {"event": "component", "component": "server", "state": "done"},
            {"event": "component", "component": "agent", "state": "active"},
            {"event": "test", "job": "chat", "subject": "m", "state": "running"},
            "Unexpected error: boom",
        ),
        returncode=1,
    )
    runner = _start(monkeypatch, proc)
    view = _wait(runner)
    assert view["state"] == "failed"
    assert view["components"] == {"server": "done", "agent": "failed"}
    assert view["tests"][0]["state"] == "failed"
    assert view["tests"][0]["kind"] == "stuck"


def test_cancel_sets_a_working_component_back():
    runner = sr.SetupRunner()
    runner._run = sr.SetupRun(skip_chat_model=False)
    runner._proc = MagicMock()
    runner._on_event({"event": "component", "component": "backend", "state": "active"})
    runner.cancel()
    assert "backend" not in runner.status()["components"]


def test_a_new_part_does_not_show_the_last_parts_percent():
    runner = sr.SetupRunner()
    runner._run = sr.SetupRun(skip_chat_model=False)
    runner._run.percent, runner._run.size = 100, "120 MB"
    runner._on_event({"event": "component", "component": "server", "state": "done"})
    assert runner.status()["percent"] == 100, "a part finishing keeps its figure"
    runner._on_event({"event": "component", "component": "backend", "state": "failed"})
    assert runner.status()["percent"] == 100, "only a part starting clears it"
    runner._on_event({"event": "component", "component": "backend", "state": "active"})
    assert runner.status()["percent"] is None
    assert runner.status()["size"] is None


def test_a_malformed_event_stops_the_child_it_can_no_longer_read(monkeypatch):
    proc = _Proc(
        _lines(
            {"event": "progress", "model": "m", "bytes_done": "lots"},
            "Step 3/5: Downloading models for 'gaia' profile...",
        ),
        returncode=0,
    )
    runner = _start(monkeypatch, proc)
    view = _wait(runner)
    assert view["state"] == "failed"
    second = _Proc(b"", returncode=1)
    monkeypatch.setattr(sr.subprocess, "Popen", lambda *a, **k: second)
    assert proc.killed and proc.waited
    runner.start(False, chat_model_for=_local_chat)


def test_an_event_missing_a_field_is_ignored():
    runner = sr.SetupRunner()
    runner._run = sr.SetupRun(skip_chat_model=False)
    runner._on_event({"event": "component", "state": "active"})
    runner._on_event({"event": "plan", "models": [{"bytes_total": 3}]})
    assert runner.status()["components"] == {}
    assert runner.status()["models"] == []


# ── init_command ─────────────────────────────────────────────────────────


class _Events(InitEvents):
    def __init__(self):
        super().__init__(True)
        self.seen = []

    def emit(self, event, **fields):
        self.seen.append({"event": event, **fields})


def _command(**kw):
    cmd = InitCommand(profile="gaia", yes=True, **kw)
    cmd.events = _Events()
    return cmd


def test_no_backend_is_installed_on_a_lemonade_base_url_server(monkeypatch):
    cmd = _command()
    cmd._lemonade_base_url = "http://127.0.0.1:1"
    installed = []
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.LemonadeClient._default_llamacpp_backend",
        lambda self: "vulkan",
    )
    monkeypatch.setattr(
        cmd, "_install_backend", lambda spec, recipe: installed.append(spec) or True
    )
    assert cmd._ensure_llamacpp_backend() is True
    assert installed == [], f"installed {installed} on the user's server"


def _verify(cmd, client, inference, vision=(True, None), vision_model=None):
    config = MagicMock()
    config.load.return_value.vision_model = vision_model
    with (
        patch.object(cmd, "_test_model_inference", side_effect=inference),
        patch.object(cmd, "_test_vision", return_value=vision),
        patch.object(cmd, "_chat_model", return_value="Gemma-4-E4B-it-GGUF"),
        patch.object(
            cmd,
            "_chat_model_choice",
            return_value=ChatModelChoice(
                model_id="Gemma-4-E4B-it-GGUF", user_set=False, skipped=[]
            ),
        ),
        patch("gaia.config.GaiaConfig", config),
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client),
        patch("gaia.llm.lemonade_client.fit_ctx_to_free_memory", return_value=65536),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            return_value=True,
        ),
        patch.object(cmd, "_skip_list", return_value=[]),
    ):
        return cmd._verify_setup()


def _client():
    client = MagicMock()
    client.health_check.return_value = {"status": "ok"}
    client.check_model_available.return_value = True
    return client


def test_a_failed_load_test_is_not_overwritten_by_the_vision_test():
    vl = "Qwen3-VL-4B-Instruct-GGUF"
    cmd = _command(extra_models=[vl])

    def inference(_client, model_id):
        return (
            (False, "Context 4096 < 32768 required") if model_id == vl else (True, None)
        )

    ok = _verify(cmd, _client(), inference, vision=(True, None), vision_model=vl)
    assert ok is False
    run = sr.SetupRun(skip_chat_model=False)
    for e in cmd.events.seen:
        if e["event"] == "test":
            sr.SetupRunner._record_test(run, e)
    rows = [r for r in run.tests.values() if r["subject"] == vl]
    assert any(r["state"] == "failed" for r in rows), rows


def test_a_timed_out_test_does_not_tell_the_user_to_delete_their_models():
    cmd = _command()
    printed = []
    cmd.console = MagicMock()
    cmd.console.print.side_effect = lambda *a, **k: printed.append(
        " ".join(map(str, a))
    )

    def inference(_client, model_id):
        if model_id == "Gemma-4-E4B-it-GGUF":
            return (False, CheckTimedOut("It didn't finish within 5 minutes"))
        return (True, None)

    assert _verify(cmd, _client(), inference) is False
    text = "\n".join(printed)
    assert "--force-models" not in text, text
    assert "stopped answering" in text


def test_a_model_asked_for_by_name_overrides_the_skip_list():
    cmd = _command(extra_models=["kokoro-v1"])
    cmd._chat_choice = ChatModelChoice(
        model_id="Gemma-4-E4B-it-GGUF", user_set=False, skipped=[]
    )
    with patch.object(cmd, "_skip_list", return_value=["kokoro-v1"]):
        ids = cmd._models_to_ensure(MagicMock())
    assert "kokoro-v1" in ids, ids
