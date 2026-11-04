# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Setup's test chat runs on the model the first chat after setup opens on.

A setup that tests one model and then chats on another can say "GAIA is
ready" while the chat fails (#6632: the NPU model was picked, the test ran on
the agent's own default).
"""

import io
import time
from unittest.mock import patch

import pytest

from gaia.config import GaiaConfig
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, NPU_DEFAULT_MODEL_NAME
from gaia.ui import setup_runner as sr
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar import daemon_client
from gaia.ui.routers import providers

CLOUD = "fireworks.deepseek-v4p1-flash"
QWEN = "Qwen3.6-35B-A3B-GGUF"


@pytest.fixture(autouse=True)
def gaia_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setattr(sr, "users_lemonade_url", lambda: None)
    monkeypatch.setattr(sr, "server_problem", lambda: None)
    # Every saved model is downloaded and runs; restore applies it.
    monkeypatch.setattr(providers, "_check_model_available", lambda _m: None)
    providers.reset_restore_state()
    yield
    providers.reset_restore_state()


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


def _save(**keys):
    for key, value in keys.items():
        GaiaConfig.save_key(key, value)


# ── Which model ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("set_up", [NPU_DEFAULT_MODEL_NAME, DEFAULT_MODEL_NAME, QWEN])
def test_a_local_agent_ui_setup_tests_the_model_it_set_up(db, set_up):
    """The Ready screen switches chat to it, whatever was picked before."""
    _save(last_model=CLOUD)
    db.set_setting("custom_model", CLOUD)
    run = sr.SetupRun(skip_chat_model=False, chat_model=set_up)
    assert sr.agent_ui_chat_model(db, run) == set_up


def test_a_local_setup_that_named_no_chat_model_says_so(db):
    with pytest.raises(sr.SetupError, match="didn't say which chat model"):
        sr.agent_ui_chat_model(db, sr.SetupRun(skip_chat_model=False))


@pytest.mark.parametrize(
    "saved, expected",
    [
        # Connecting the provider saved its model; chat restores it.
        ({"last_model": CLOUD}, CLOUD),
        # Nothing cloud saved: the local default, which the run then refuses.
        ({}, DEFAULT_MODEL_NAME),
    ],
)
def test_a_cloud_agent_ui_setup_tests_the_model_a_new_chat_opens_on(
    db, saved, expected
):
    _save(**saved)
    run = sr.SetupRun(skip_chat_model=True)
    assert sr.agent_ui_chat_model(db, run) == expected
    # The chip's "model a new chat starts on" names the same one.
    assert providers._active_view(db)["model"] == expected


@pytest.mark.parametrize(
    "saved, expected",
    [
        ({"default_model": NPU_DEFAULT_MODEL_NAME}, NPU_DEFAULT_MODEL_NAME),
        ({}, DEFAULT_MODEL_NAME),
        ({"default_model": NPU_DEFAULT_MODEL_NAME, "last_model": QWEN}, QWEN),
        ({"last_model": CLOUD}, CLOUD),
    ],
)
def test_setup_tests_the_model_the_tui_opens_on(saved, expected):
    _save(**saved)
    assert sr.tui_chat_model(sr.SetupRun(skip_chat_model=False)) == expected


# ── The run ────────────────────────────────────────────────────────────────


class _Done(io.BytesIO):
    """``gaia init`` that printed nothing and exited 0."""


class _Proc:
    def __init__(self):
        self.stdout = io.BufferedReader(_Done(b""))
        self.returncode = 0

    def kill(self):
        pass

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class _Proxy:
    """The flagship sidecar: records what each query asked for."""

    agent_id = "gaia"

    def __init__(self, fails_on=()):
        self.bodies = []
        self.fails_on = set(fails_on)

    def init(self):
        return 200, {}

    def query_stream(self, body, read_timeout=None, on_response=None):
        self.bodies.append(body)
        if body.get("model") in self.fails_on:
            yield {
                "type": "error",
                "detail": "Couldn't reach the local LLM (LemonadeNetworkError)",
            }
            return
        yield {"type": "final", "answer": "ok"}

    def cancel_query(self, _rid):
        pass


class _Handle:
    api_version = "2.30"

    def __init__(self, proxy):
        self._proxy = proxy

    def proxy(self):
        return self._proxy


@pytest.fixture
def sidecar(monkeypatch):
    """The real test turn, relayed to a recording sidecar, as a chat turn is."""
    monkeypatch.setattr(sr, "_agent_turn_problem", sr._agent_turn_problem.real)
    monkeypatch.setattr(sr, "check", lambda *a, **k: {"ready": True})
    monkeypatch.setattr(sr.subprocess, "Popen", lambda *a, **k: _Proc())
    acquired = []

    def run(fails_on=()):
        proxy = _Proxy(fails_on)

        def acquire(agent_type):
            acquired.append(agent_type)
            return _Handle(proxy)

        monkeypatch.setattr(daemon_client, "acquire_handle", acquire)
        return proxy, acquired

    with (
        patch("gaia.hub.installer.read_sentinel", return_value=object()),
        patch("gaia.ui._chat_helpers._replay_cloud_key_for_relay"),
    ):
        yield run


def _wait(runner, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        view = runner.status()
        if view and view["state"] not in ("running", "verifying"):
            return view
        time.sleep(0.02)
    raise AssertionError(runner.status())


def _agent_row(view):
    return next(t for t in view["tests"] if t["job"] == "agent")


@pytest.mark.parametrize(
    "chat_model, skip_chat_model",
    [
        (NPU_DEFAULT_MODEL_NAME, False),
        (DEFAULT_MODEL_NAME, False),
        (CLOUD, True),
    ],
)
def test_the_test_chat_is_sent_to_the_flagship_on_the_chosen_model(
    sidecar, chat_model, skip_chat_model
):
    proxy, acquired = sidecar()
    runner = sr.SetupRunner()
    runner.start(skip_chat_model, chat_model_for=lambda run: chat_model)
    view = _wait(runner)
    assert view["state"] == "ready"
    # One query, to the same sidecar chat relays to, naming the chosen model.
    assert acquired == ["gaia"]
    assert [b.get("model") for b in proxy.bodies] == [chat_model]
    assert _agent_row(view)["subject"] == chat_model


def test_a_chosen_model_that_fails_its_chat_fails_setup_though_the_default_answers(
    sidecar,
):
    proxy, _ = sidecar(fails_on={NPU_DEFAULT_MODEL_NAME})
    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=lambda run: NPU_DEFAULT_MODEL_NAME)
    view = _wait(runner)
    assert [b.get("model") for b in proxy.bodies] == [NPU_DEFAULT_MODEL_NAME]
    assert view["state"] == "failed"
    assert not sr.is_initialized()
    row = _agent_row(view)
    assert row["state"] == "failed" and row["kind"] == "agent"
    assert row["subject"] == NPU_DEFAULT_MODEL_NAME
    # A plain sentence naming the model and the device; the agent's own
    # words go to the log, which the setup report carries.
    assert view["error"] == row["error"]
    assert "Gemma 4 E4B NPU" in row["error"] and "NPU" in row["error"]
    assert "LemonadeNetworkError" not in row["error"]
    assert any(
        "LemonadeNetworkError" in line for line in runner.report()["logs"]["test"]
    )


def test_a_cloud_setup_whose_chat_opens_on_a_local_model_has_nothing_to_test(
    sidecar,
):
    proxy, _ = sidecar()
    runner = sr.SetupRunner()
    runner.start(True, chat_model_for=lambda run: DEFAULT_MODEL_NAME)
    view = _wait(runner)
    assert view["state"] == "failed"
    assert view["error"] == sr.NO_CLOUD_MODEL
    assert proxy.bodies == []


def test_a_model_setup_cant_name_fails_the_chat_test_with_its_reason(sidecar):
    proxy, _ = sidecar()

    def unknown(run):
        raise sr.SetupError("Setup didn't say which chat model it set up.")

    runner = sr.SetupRunner()
    runner.start(False, chat_model_for=unknown)
    view = _wait(runner)
    assert view["state"] == "failed"
    assert _agent_row(view)["error"] == "Setup didn't say which chat model it set up."
    assert proxy.bodies == []


@pytest.mark.parametrize(
    "device, where",
    [("cpu", "this PC's CPU"), ("gpu", "this PC's GPU"), (None, "this PC,")],
)
def test_a_failed_chat_names_where_the_model_runs_not_always_the_gpu(
    monkeypatch, device, where
):
    from gaia.ui import warmup

    seen = []
    monkeypatch.setattr(
        warmup, "_device_for", lambda _c, model: seen.append(model) or device
    )
    error = sr._agent_turn_failure(DEFAULT_MODEL_NAME)
    assert seen == [DEFAULT_MODEL_NAME]
    assert f"loads on {where}" in error
    if device != "gpu":
        assert "GPU" not in error
