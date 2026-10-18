# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The Agent UI shares the model server: it never takes a model from under anyone.

* The chat pre-flight never unloads a model another process loaded, makes room
  only by unloading its own idle models when a load would not fit, and ends the
  turn with an actionable error when it cannot get the model.
* A stopped turn's pre-flight does not go on to load a model.
* An auto-title runs on the side slot under the same lease and lock as a turn,
  and never loads a model.
* Settings → Load goes through the lease and the load lock, and asks first
  while a chat is answering.
"""

import asyncio
import threading
from contextlib import contextmanager
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from gaia.daemon import broker_client
from gaia.daemon.broker_client import BrokerUnavailableError
from gaia.llm import lemonade_choice, lemonade_client
from gaia.llm.lemonade_client import InsufficientMemoryError
from gaia.llm.lemonade_manager import LemonadeManager
from gaia.ui import _chat_helpers as helpers
from gaia.ui.sse_handler import SSEOutputHandler

_URL = "http://127.0.0.1:9/api/v1"


def _model(name, ctx=65536):
    return {
        "model_name": name,
        "type": "llm",
        "recipe": "llamacpp",
        "recipe_options": {"ctx_size": ctx},
    }


@pytest.fixture
def lemonade(monkeypatch):
    """A fake model server: what /health reports, and a recording client."""
    state = {"resident": [], "lease": None}
    client = MagicMock()

    def health_get(url, **_kwargs):
        assert url == f"{_URL}/health"
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"all_models_loaded": list(state["resident"])}
        return resp

    def load(name, **_kwargs):
        state["resident"].append(_model(name))

    def unload(name, **_kwargs):
        state["resident"] = [m for m in state["resident"] if m["model_name"] != name]

    client.load_model.side_effect = load
    client.unload_model.side_effect = unload

    @contextmanager
    def lease(model, **kwargs):
        if state["lease"] is not None:
            state["lease"](model, kwargs)
        yield None

    monkeypatch.setenv("LEMONADE_BASE_URL", _URL)
    monkeypatch.setattr(LemonadeManager, "get_base_url", lambda: _URL)
    monkeypatch.setattr(httpx, "get", health_get)
    monkeypatch.setattr(lemonade_choice, "is_users_server", lambda _url: False)
    monkeypatch.setattr(lemonade_client, "resolve_ctx_size", lambda _model: 65536)
    monkeypatch.setattr(lemonade_client, "LemonadeClient", lambda **_kw: client)
    monkeypatch.setattr(broker_client, "model_lease", lease)
    monkeypatch.setattr(helpers, "_models_loaded_here", set(), raising=False)
    state["client"] = client
    return state


# ── The pre-flight never takes another process's model ───────────────────────


def test_preflight_leaves_a_model_another_process_loaded(lemonade):
    lemonade["resident"] = [_model("Other-App-GGUF")]

    helpers._maybe_load_expected_model("Mine-GGUF")

    lemonade["client"].unload_model.assert_not_called()
    lemonade["client"].load_model.assert_called_once()
    assert lemonade["client"].load_model.call_args.args[0] == "Mine-GGUF"


def test_two_chats_on_two_models_do_not_swap_while_both_fit(lemonade):
    helpers._maybe_load_expected_model("Chat-A-GGUF")
    helpers._maybe_load_expected_model("Chat-B-GGUF")
    helpers._maybe_load_expected_model("Chat-A-GGUF")
    helpers._maybe_load_expected_model("Chat-B-GGUF")

    lemonade["client"].unload_model.assert_not_called()
    assert lemonade["client"].load_model.call_count == 2


def test_preflight_makes_room_only_by_unloading_its_own_idle_models(lemonade):
    helpers._maybe_load_expected_model("Mine-Old-GGUF")
    lemonade["resident"].append(_model("Other-App-GGUF"))
    client = lemonade["client"]
    loads = []

    def load(name, **_kwargs):
        loads.append(name)
        if len(loads) == 1:
            raise InsufficientMemoryError("Not enough free memory to load it")
        lemonade["resident"].append(_model(name))

    client.load_model.side_effect = load
    handler = SSEOutputHandler()

    helpers._maybe_load_expected_model("Mine-New-GGUF", handler)

    assert [c.args[0] for c in client.unload_model.call_args_list] == ["Mine-Old-GGUF"]
    assert loads == ["Mine-New-GGUF", "Mine-New-GGUF"]
    messages = []
    while not handler.event_queue.empty():
        messages.append(handler.event_queue.get_nowait().get("message") or "")
    assert any("Mine-Old-GGUF" in m for m in messages)


def test_no_room_and_nothing_of_ours_to_unload_ends_the_turn(lemonade):
    lemonade["resident"] = [_model("Other-App-GGUF")]
    lemonade["client"].load_model.side_effect = InsufficientMemoryError(
        "Not enough free memory to load Mine-GGUF"
    )

    with pytest.raises(InsufficientMemoryError, match="Not enough free memory"):
        helpers._maybe_load_expected_model("Mine-GGUF")
    lemonade["client"].unload_model.assert_not_called()


def test_a_lease_timeout_ends_the_turn_instead_of_racing_the_slot(lemonade):
    def refuse(model, _kwargs):
        raise BrokerUnavailableError(
            f"model-slot broker refused the lease for '{model}' (HTTP 504): "
            "not granted within 300.0s",
            status_code=504,
        )

    lemonade["lease"] = refuse

    with pytest.raises(BrokerUnavailableError, match="not granted"):
        helpers._maybe_load_expected_model("Mine-GGUF", SSEOutputHandler())
    lemonade["client"].load_model.assert_not_called()


# ── A stopped turn's pre-flight loads nothing ─────────────────────────────────


def test_turn_stopped_during_the_lease_wait_loads_nothing(lemonade):
    handler = SSEOutputHandler()
    lemonade["lease"] = lambda _model, _kwargs: handler.cancelled.set()

    helpers._maybe_load_expected_model("Mine-GGUF", handler)

    lemonade["client"].load_model.assert_not_called()


def test_waiting_for_another_load_is_shown_and_stop_ends_it(lemonade):
    handler = SSEOutputHandler()
    finished = threading.Event()

    def preflight():
        helpers._maybe_load_expected_model("Mine-GGUF", handler)
        finished.set()

    with helpers.model_load_lock:  # Another chat is loading its model.
        worker = threading.Thread(target=preflight, daemon=True)
        worker.start()
        status = handler.event_queue.get(timeout=5)
        while status.get("message") == "Loading LLM model...":
            status = handler.event_queue.get(timeout=5)
        assert "another chat" in status["message"].lower()
        handler.cancelled.set()
        assert finished.wait(5), "Stop must end the wait for the load lock"
    lemonade["client"].load_model.assert_not_called()


# ── Auto-title: side slot, same discipline, never loads ───────────────────────


@pytest.fixture
def title_client(monkeypatch):
    client = MagicMock()
    client.health_check.return_value = {"all_models_loaded": []}
    client.slot_count.return_value = 2
    client.chat_completions.return_value = {
        "choices": [{"message": {"content": "Trip To Lisbon"}}]
    }
    made = []

    def make(**kwargs):
        made.append(kwargs)
        return client

    monkeypatch.setattr(lemonade_client, "LemonadeClient", make)
    posts = []

    class NoRawPosts:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, url, **_kwargs):
            posts.append(url)
            raise AssertionError("the title must not bypass the model client")

    monkeypatch.setattr(httpx, "AsyncClient", NoRawPosts)
    return client, made, posts


def test_title_never_loads_a_model_that_is_not_resident(title_client):
    client, _made, posts = title_client

    title = asyncio.run(
        helpers._generate_session_title(
            _URL, "Gemma-4-E4B-it-GGUF", "plan a trip", "ok"
        )
    )

    assert title == "plan a trip"
    client.chat_completions.assert_not_called()
    assert posts == []


def test_title_runs_on_the_side_slot_under_the_lease(title_client):
    client, made, posts = title_client
    client.health_check.return_value = {
        "all_models_loaded": [_model("Gemma-4-E4B-it-GGUF")]
    }

    title = asyncio.run(
        helpers._generate_session_title(
            _URL, "Gemma-4-E4B-it-GGUF", "plan a trip", "ok"
        )
    )

    assert title == "Trip To Lisbon"
    assert posts == []
    assert made[-1]["model_lease_priority"] == "background"
    kwargs = client.chat_completions.call_args.kwargs
    assert kwargs["id_slot"] == lemonade_client.SIDE_SLOT
    assert kwargs["auto_download"] is False


# ── Settings → Load: through the lease, and asks while a chat answers ────────


@pytest.fixture
def ui(monkeypatch):
    from gaia.ui.server import create_app

    monkeypatch.setenv("LEMONADE_BASE_URL", _URL)
    loads = []
    held = []
    client = MagicMock()

    def load(name, **kwargs):
        held.append(helpers.model_load_lock.locked())
        loads.append((name, kwargs.get("ctx_size")))

    client.load_model.side_effect = load
    monkeypatch.setattr(lemonade_client, "LemonadeClient", lambda **_kw: client)
    app = create_app(db_path=":memory:")
    return TestClient(app), loads, held


def _wait_for(predicate):
    for _ in range(200):
        if predicate():
            return
        threading.Event().wait(0.01)
    raise AssertionError("condition not reached in time")


@pytest.mark.allow_network
def test_settings_load_goes_through_the_model_client_and_load_lock(ui):
    client, loads, held = ui

    resp = client.post(
        "/api/system/load-model", json={"model_name": "Mine-GGUF", "ctx_size": 65536}
    )

    assert resp.status_code == 202
    _wait_for(lambda: loads)
    assert loads == [("Mine-GGUF", 65536)]
    assert held == [True]


@pytest.mark.allow_network
def test_settings_load_asks_first_while_a_chat_is_answering(ui, monkeypatch):
    from gaia.ui.run_manager import run_manager

    client, loads, _held = ui
    monkeypatch.setitem(run_manager._runs, "busy-session", object())

    resp = client.post("/api/system/load-model", json={"model_name": "Mine-GGUF"})
    assert resp.status_code == 409
    assert "answering" in resp.json()["detail"]
    assert loads == []

    resp = client.post(
        "/api/system/load-model", json={"model_name": "Mine-GGUF", "force": True}
    )
    assert resp.status_code == 202
    _wait_for(lambda: loads)
