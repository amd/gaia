# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What GAIA asks the model server for, from each state a user can start in.

Bugs that only fire from one starting state pass every test written on a PC
that is already set up. rc18: a cloud-only user had a 5 GB local model
downloaded and loaded behind their back by the start-up warm-up, by Memory >
Run Inference and by a scheduled run, and no test had ever started there.

Each state below is a config plus what is downloaded; each entry point is real
GAIA code talking over HTTP to the installer journeys' stand-in Lemonade. That
server writes down every request, so a cell asserts what reached it, not which
method was called. A new
state or entry point is one more row; every combination is then covered.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import threading
import time
from dataclasses import dataclass

import pytest

from gaia import config as config_mod
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, LARGE_DEFAULT_MODEL_NAME
from tests.fixtures.fake_lemonade import FakeLemonade, _server_module

# The recording server is a socket on this machine; nothing leaves it.
pytestmark = pytest.mark.allow_network

CLOUD = "fireworks.deepseek-v4p1-flash"
EMBEDDER = "user.embeddinggemma-300m-GGUF"

#: Models a "cloud-only" user never chose and must never be given.
LOCAL_CHAT_MODELS = (DEFAULT_MODEL_NAME, LARGE_DEFAULT_MODEL_NAME)


@dataclass
class State:
    """One starting state: what is saved, and what is on disk."""

    config: dict
    #: Local chat models already downloaded.
    downloaded: tuple = ()
    #: Set through the Agent UI's settings endpoint before the entry point runs.
    custom_model: str | None = None

    def on_the_server(self) -> tuple:
        """What the server starts with: the cloud model is always reachable."""
        return (CLOUD, EMBEDDER, *self.downloaded)


STATES = {
    # `gaia config set default_model <cloud id>`, nothing local on disk.
    "cloud_default_model": State({"default_model": CLOUD}),
    # First-run setup in the Agent UI with "Use a cloud model": the pick is
    # saved as last_model and default_model stays unset.
    "cloud_picked_in_app": State({"last_model": CLOUD, "last_provider": "fireworks"}),
    # Settings > Advanced > Custom model ID names the cloud model; a local model
    # is on disk from earlier.
    "cloud_custom_model_id": State(
        {}, downloaded=(DEFAULT_MODEL_NAME,), custom_model=CLOUD
    ),
    # A new install before any model is chosen or downloaded.
    "empty_cache": State({}),
    # The default local setup: the control row, where using Gemma is right.
    "local_model_on_disk": State({}, downloaded=(DEFAULT_MODEL_NAME,)),
}


# ---------------------------------------------------------------------------
# Entry points: each returns the models GAIA decided to use, if it says so
# ---------------------------------------------------------------------------


def start_up_warm_up(_tmp_path) -> list:
    """The model the flagship sidecar loads when the app starts."""
    # The unit job has no flagship package; test_gaia_agent.yml runs this.
    sidecar = pytest.importorskip("gaia_agent.server")

    model = sidecar._model_to_warm()
    return [model] if model else []


def memory_run_inference(_tmp_path) -> list:
    """Memory > Profile setup > Run Inference."""
    from gaia.ui.routers import memory

    response = memory.stream_inference(include_browser=False)

    async def _drain():
        async for _chunk in response.body_iterator:
            pass

    asyncio.run(_drain())
    return []


def scheduled_run(_tmp_path) -> list:
    """`gaia schedule run`, and every run the schedule daemon fires."""
    from gaia.llm.lemonade_client import LemonadeClientError
    from gaia.schedule import runner
    from gaia.schedule.store import Schedule

    try:
        runner.fire(
            Schedule(
                name="t",
                cron="0 7 1 1 *",
                prompt="Reply with exactly: hi",
                sink="stdout",
            )
        )
    except (LemonadeClientError, runner.ScheduleModelError):
        # A refused load still reached the server; the cell judges its log.
        pass
    return []


ENTRY_POINTS = {
    "start_up_warm_up": start_up_warm_up,
    "memory_run_inference": memory_run_inference,
    "scheduled_run": scheduled_run,
}

# ---------------------------------------------------------------------------
# What each cell must hold
# ---------------------------------------------------------------------------

#: No local chat model is downloaded, loaded or asked to chat.
NO_LOCAL_MODEL = "no local chat model is touched"
#: Nothing is downloaded that the user did not ask for.
NO_DOWNLOAD = "nothing is downloaded"
#: The local default is the model in use.
USES_LOCAL = "the local default model is used"

RULES = {
    ("cloud_default_model", "start_up_warm_up"): NO_LOCAL_MODEL,
    ("cloud_default_model", "memory_run_inference"): NO_LOCAL_MODEL,
    ("cloud_default_model", "scheduled_run"): NO_LOCAL_MODEL,
    ("cloud_picked_in_app", "start_up_warm_up"): NO_LOCAL_MODEL,
    ("cloud_picked_in_app", "memory_run_inference"): NO_LOCAL_MODEL,
    ("cloud_picked_in_app", "scheduled_run"): NO_LOCAL_MODEL,
    ("cloud_custom_model_id", "start_up_warm_up"): NO_LOCAL_MODEL,
    # Profile inference stays on this PC by design, and the model is on disk.
    ("cloud_custom_model_id", "memory_run_inference"): NO_DOWNLOAD,
    ("cloud_custom_model_id", "scheduled_run"): NO_LOCAL_MODEL,
    ("empty_cache", "start_up_warm_up"): NO_DOWNLOAD,
    ("empty_cache", "memory_run_inference"): NO_DOWNLOAD,
    # A scheduled run refuses a model that isn't on disk rather than fetch it.
    ("empty_cache", "scheduled_run"): NO_DOWNLOAD,
    ("local_model_on_disk", "start_up_warm_up"): USES_LOCAL,
    ("local_model_on_disk", "memory_run_inference"): USES_LOCAL,
    ("local_model_on_disk", "scheduled_run"): USES_LOCAL,
}

#: Cells that fail today, each naming its issue. Strict: fixing one without
#: deleting its line fails the run.
KNOWN_BUGS = {}


def _cells():
    for state in STATES:
        for entry in ENTRY_POINTS:
            key = (state, entry)
            if key not in RULES:
                raise RuntimeError(f"no rule for {key}: add one to RULES")
            if RULES[key] is None:
                continue
            marks = []
            if key in KNOWN_BUGS:
                # Only the bug's own assertion counts; a crash must not pass as it.
                marks.append(
                    pytest.mark.xfail(
                        reason=KNOWN_BUGS[key], strict=True, raises=AssertionError
                    )
                )
            yield pytest.param(state, entry, id=f"{state}-{entry}", marks=marks)


@pytest.fixture
def start(tmp_path, monkeypatch):
    """Put GAIA in a starting state against a recording server."""

    def _start(state: State) -> FakeLemonade:
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(state.config), encoding="utf-8")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)
        # The warm-up is skipped on a small PC; a CI runner must not hide it.
        monkeypatch.setattr("gaia.llm.model_fit.ram_total_gb", lambda: 128.0)
        # Profile inference reads the real PC; the matrix is about the model.
        from gaia.agents.base.discovery import SystemDiscovery

        for name in dir(SystemDiscovery):
            if name.startswith("scan_"):
                monkeypatch.setattr(SystemDiscovery, name, lambda *_a, **_k: [])
        monkeypatch.setattr(
            SystemDiscovery,
            "scan_installed_apps",
            lambda *_a, **_k: [{"content": "Installed app: Example Editor"}],
        )
        server = FakeLemonade(downloaded=state.on_the_server())
        server.__enter__()
        started.append(server)
        monkeypatch.setenv("LEMONADE_BASE_URL", server.base_url)
        if state.custom_model:
            _save_custom_model(state.custom_model, tmp_path)
        return server

    started: list = []
    yield _start
    for server in started:
        server.__exit__(None, None, None)


def _save_custom_model(model_id: str, tmp_path) -> None:
    """Save Settings > Advanced > Custom model ID the way the app does."""
    from fastapi.testclient import TestClient

    from gaia.ui.server import create_app

    client = TestClient(create_app(db_path=str(tmp_path / "ui.db")))
    response = client.put(
        "/api/settings", json={"custom_model": model_id}, headers={"X-Gaia-UI": "1"}
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("state_name, entry_name", list(_cells()))
def test_starting_state(state_name, entry_name, start, tmp_path):
    server = start(STATES[state_name])
    before = len(server.requests)

    decided = ENTRY_POINTS[entry_name](tmp_path)

    requests = server.requests[before:]
    asked = [r.model for r in requests if r.method == "POST" and r.model]
    used = set(decided) | set(asked)
    trace = "\n".join(f"  {r.method} {r.path} {r.model or ''}" for r in requests)
    rule = RULES[(state_name, entry_name)]
    if rule == NO_LOCAL_MODEL:
        touched = sorted(used & set(LOCAL_CHAT_MODELS))
        assert not touched, (
            f"{entry_name} from {state_name} used the local model {touched}; "
            f"GAIA chose {decided}, and the server saw:\n{trace}"
        )
    if rule in (NO_LOCAL_MODEL, NO_DOWNLOAD):
        assert server.downloads == [] and server.pulls == [], (
            f"{entry_name} from {state_name} downloaded {server.downloads} "
            f"(pulls: {server.pulls}); the server saw:\n{trace}"
        )
    if rule == USES_LOCAL:
        assert DEFAULT_MODEL_NAME in used, (
            f"{entry_name} from {state_name} never used {DEFAULT_MODEL_NAME}; "
            f"GAIA chose {decided}, and the server saw:\n{trace}"
        )
        assert server.downloads == []


def test_every_known_bug_names_a_real_cell():
    """A stale entry would silently stop marking anything."""
    cells = {(s, e) for s in STATES for e in ENTRY_POINTS}
    assert set(KNOWN_BUGS) <= cells
    assert set(RULES) == cells


def test_the_server_keeps_answering_past_a_full_pipe():
    """It logs a stderr line per request; a long test must not stall it."""
    import urllib.request

    # ~200 KB of log lines, well past any OS pipe buffer.
    url_with_padding = "/health?pad=" + "x" * 1000
    with FakeLemonade() as server:
        for _ in range(200):
            with urllib.request.urlopen(server.base_url + url_with_padding, timeout=10):
                pass
        assert len(server.requests) == 200


@pytest.fixture
def slow_logging_server(tmp_path, monkeypatch):
    """The fake server in-process, its log write slowed to widen any race."""
    module = _server_module()
    log = tmp_path / "requests.jsonl"
    monkeypatch.setattr(module, "_log_path", log)
    monkeypatch.setattr(module, "_catalog", dict(module.CATALOG))
    downloaded = ("kokoro-v1", DEFAULT_MODEL_NAME)
    monkeypatch.setattr(module, "_downloaded", {m: True for m in downloaded})
    monkeypatch.setenv("FAKE_LEMONADE_PULL_SECONDS", "0")
    real_log = module._log

    def slow_log(entry):
        time.sleep(0.3)
        real_log(entry)

    monkeypatch.setattr(module, "_log", slow_log)
    server = module.LoopbackServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], log
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _logged(log):
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize(
    "method, path, body, status",
    [
        ("GET", "/api/v1/health", None, 200),
        ("GET", "/api/v1/models/no-such-model", None, 404),
        ("GET", "/api/v1/nothing-here", None, 404),
        ("POST", "/api/v1/nothing-here", {}, 404),
        ("POST", "/api/v1/load", {"model_name": "Qwen3-0.6B-GGUF"}, 404),
        ("POST", "/api/v1/pull", {"model_name": "user.x"}, 400),
        ("POST", "/api/v1/pull", {"model_name": "Qwen3-0.6B-GGUF"}, 200),
        (
            "POST",
            "/api/v1/pull",
            {"model_name": "Qwen3-0.6B-GGUF", "stream": True},
            200,
        ),
        ("POST", "/api/v1/audio/speech", {"model": "kokoro-v1"}, 200),
        ("POST", "/api/v1/chat/completions", {"model": DEFAULT_MODEL_NAME}, 200),
        (
            "POST",
            "/api/v1/chat/completions",
            {"model": DEFAULT_MODEL_NAME, "stream": True},
            200,
        ),
    ],
)
def test_a_reply_never_arrives_before_its_request_is_logged(
    slow_logging_server, method, path, body, status
):
    """Reading the log right after the reply must find the request, once."""
    port, log = slow_logging_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        payload = None if body is None else json.dumps(body)
        headers = {} if body is None else {"Content-Type": "application/json"}
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        response.read()
        assert response.status == status
        entries = _logged(log)
    finally:
        conn.close()
    assert [(e["method"], e["path"], e["status"]) for e in entries] == [
        (method, path, status)
    ]


def test_each_request_on_a_kept_alive_connection_is_logged_once(
    slow_logging_server,
):
    """The handler object is reused across a keep-alive connection."""
    port, log = slow_logging_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        for count in range(1, 4):
            conn.request("GET", "/api/v1/health")
            conn.getresponse().read()
            assert len(_logged(log)) == count
        conn.request(
            "POST",
            "/api/v1/chat/completions",
            body=json.dumps({"model": "Qwen3-0.6B-GGUF"}),
            headers={"Content-Type": "application/json"},
        )
        conn.getresponse().read()
        assert [e["status"] for e in _logged(log)] == [200, 200, 200, 404]
    finally:
        conn.close()
