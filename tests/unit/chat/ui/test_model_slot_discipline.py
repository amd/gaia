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
    monkeypatch.setattr(lemonade_client, "resolve_ctx_size", lambda *_a, **_k: 65536)

    class FakeClient:
        # A class, not a lambda: code under test also isinstance-checks it.
        def __new__(cls, **_kw):
            return client

    monkeypatch.setattr(lemonade_client, "LemonadeClient", FakeClient)
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
    leases = []

    @contextmanager
    def lease(model, **kwargs):
        leases.append((model, kwargs.get("priority")))
        yield None

    monkeypatch.setattr(broker_client, "model_lease", lease)
    client.leases = leases
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
    # One lease spans the residency check and the call.
    assert client.leases == [("Gemma-4-E4B-it-GGUF", "background")]
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
        if helpers.model_load_lock.locked():
            order.append("lock")
        loads.append((name, kwargs.get("ctx_size")))

    client.load_model.side_effect = load
    monkeypatch.setattr(lemonade_client, "LemonadeClient", lambda **_kw: client)
    order = []

    @contextmanager
    def lease(_model, **_kwargs):
        order.append("lock" if helpers.model_load_lock.locked() else "lease")
        yield None

    monkeypatch.setattr(broker_client, "model_lease", lease)
    app = create_app(db_path=":memory:")
    return TestClient(app), loads, held, order


def _wait_for(predicate):
    for _ in range(200):
        if predicate():
            return
        threading.Event().wait(0.01)
    raise AssertionError("condition not reached in time")


@pytest.mark.allow_network
def test_settings_load_goes_through_the_model_client_and_load_lock(ui):
    client, loads, held, order = ui

    resp = client.post(
        "/api/system/load-model", json={"model_name": "Mine-GGUF", "ctx_size": 65536}
    )

    assert resp.status_code == 202
    _wait_for(lambda: loads)
    assert loads == [("Mine-GGUF", 65536)]
    assert held == [True]
    # Lease first, then the lock: the order every load in this process uses.
    assert order == ["lease", "lock"]


@pytest.mark.allow_network
def test_settings_load_asks_first_while_a_chat_is_answering(ui, monkeypatch):
    from gaia.ui.run_manager import run_manager

    client, loads, _held, _order = ui
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


def test_names_are_matched_the_way_lemonade_reports_them(lemonade):
    helpers.note_model_loaded("user.Mine-Old-GGUF")
    lemonade["resident"] = [_model("mine-old-gguf"), _model("Other-App-GGUF")]
    loads = []

    def load(name, **_kwargs):
        loads.append(name)
        if len(loads) == 1:
            raise InsufficientMemoryError("Not enough free memory to load it")

    lemonade["client"].load_model.side_effect = load

    helpers._maybe_load_expected_model("Mine-New-GGUF")

    unloaded = [c.args[0] for c in lemonade["client"].unload_model.call_args_list]
    assert unloaded == ["mine-old-gguf"]


# ── A turn stopped during the pre-flight never sends its query ───────────────


class _CountingAgent:
    def __init__(self):
        self.model_id = "Mine-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.queries = 0

    def _register_tools(self):
        pass

    def process_query(self, _message):
        self.queries += 1
        return {"result": "answered"}


@pytest.mark.parametrize("stopped_by", ["stop", "turn_cancel"])
@pytest.mark.parametrize("wait_ends", ["returns", "raises"])
def test_a_turn_stopped_while_waiting_for_the_model_never_queries(
    lemonade, monkeypatch, wait_ends, stopped_by
):
    import json
    from unittest.mock import AsyncMock

    from gaia.ui.database import ChatDatabase
    from gaia.ui.models import ChatRequest
    from gaia.ui.run_manager import RunManager

    db = ChatDatabase(":memory:")
    session = db.create_session(model="Mine-GGUF", agent_type="chat")
    sid = session["id"]
    agent = _CountingAgent()
    helpers._agent_cache.clear()
    helpers._store_agent(sid, "Mine-GGUF", [], agent, "chat")
    monkeypatch.setattr("gaia.ui.run_manager.run_manager", RunManager())
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_maybe_update_session_title", AsyncMock())

    def press_stop_while_waiting(_model, _kwargs):
        handler = helpers._active_sse_handlers[sid]
        if stopped_by == "stop":
            handler.cancelled.set()
        else:
            # The turn's own cancel (teardown, the idle stop), not the Stop flag.
            helpers._agent_cache[sid]["agent"]._cancel_event.set()
        if wait_ends == "raises":
            # What a broker wait cancelled by the turn's Stop raises.
            raise RuntimeError("stopped while waiting for the model slot")

    lemonade["lease"] = press_stop_while_waiting

    async def run_turn():
        events = []
        request = ChatRequest(session_id=sid, message="hi", stream=True)
        async for chunk in helpers._stream_chat_response(db, session, request):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))
        return events

    try:
        events = asyncio.run(run_turn())
    finally:
        helpers._agent_cache.clear()
        db.close()

    assert agent.queries == 0, "the query went to a model nobody loaded"
    lemonade["client"].load_model.assert_not_called()
    if stopped_by == "stop":
        assert not [e for e in events if e.get("type") == "error"]
        done = [e for e in events if e.get("type") == "done"]
        assert done and done[-1].get("cancelled") is True


# ── A restart still knows which models are the Agent UI's own ──────────────


@pytest.fixture
def record(monkeypatch):
    """Restart the Agent UI: forget what is in memory; the record (a tmp file,
    see tests/conftest.py) stays."""
    monkeypatch.setattr(helpers, "_models_loaded_here", None)

    def restart():
        monkeypatch.setattr(helpers, "_models_loaded_here", None)

    return restart


def test_a_restart_still_knows_the_models_it_loaded(record):
    helpers.note_model_loaded("user.Mine-Old-GGUF")
    record()  # The Agent UI restarts: in-memory state is gone.

    assert helpers._loaded_here("mine-old-gguf")
    assert not helpers._loaded_here("Email-Agent-Model-GGUF")


def test_after_a_restart_only_its_own_models_make_room(lemonade, record):
    helpers.note_model_loaded("Mine-Old-GGUF")
    record()
    # Another GAIA process (the email agent) has a model loaded too.
    lemonade["resident"] = [_model("Mine-Old-GGUF"), _model("Email-Agent-GGUF")]
    loads = []

    def load(name, **_kwargs):
        loads.append(name)
        if len(loads) == 1:
            raise InsufficientMemoryError("Not enough free memory to load it")

    lemonade["client"].load_model.side_effect = load

    helpers._maybe_load_expected_model("Mine-New-GGUF")

    unloaded = [c.args[0] for c in lemonade["client"].unload_model.call_args_list]
    assert unloaded == ["Mine-Old-GGUF"]
    record()
    assert not helpers._loaded_here(
        "Mine-Old-GGUF"
    ), "the record kept an unloaded model"
    assert helpers._loaded_here("Mine-New-GGUF")


def _config_error(message):
    from gaia.config import GaiaConfigError

    return GaiaConfigError(message)


def _client_error(message):
    from gaia.llm.lemonade_client import LemonadeClientError

    return LemonadeClientError(message)


@pytest.mark.allow_network
@pytest.mark.parametrize("make_error", [_client_error, _config_error])
def test_a_failed_settings_load_shows_in_the_status(ui, monkeypatch, make_error):
    from gaia.ui.routers import system as system_mod

    client, loads, _held, _order = ui
    failing = MagicMock()
    failing.load_model.side_effect = make_error("backend rocm is missing")

    class FailingClient:
        def __new__(cls, **_kw):
            return failing

    monkeypatch.setattr(lemonade_client, "LemonadeClient", FailingClient)
    monkeypatch.setattr(system_mod, "_last_load_failure", None)

    system_mod._load_through_the_slot("Mine-GGUF", 65536, "Load Mine-GGUF")

    warnings = client.get("/api/system/status").json()["probe_warnings"]
    assert any("backend rocm is missing" in w for w in warnings)


# ── A too-small copy is reloaded only when nobody else chose that window ─────


def test_another_apps_smaller_window_is_not_reloaded_out_from_under_it(lemonade):
    lemonade["resident"] = [_model("Mine-GGUF", ctx=32768)]  # Another app's load.

    with pytest.raises(helpers.ModelInUseError, match="another app"):
        helpers._maybe_load_expected_model("Mine-GGUF")

    lemonade["client"].unload_model.assert_not_called()
    lemonade["client"].load_model.assert_not_called()


@pytest.mark.parametrize("ours", [True, False])
def test_its_own_or_an_auto_loaded_small_copy_is_reloaded(lemonade, ours):
    window = 32768 if ours else 4096  # 4096: Lemonade's own auto-load default.
    if ours:
        helpers.note_model_loaded("Mine-GGUF")
    lemonade["resident"] = [_model("Mine-GGUF", ctx=window)]

    helpers._maybe_load_expected_model("Mine-GGUF")

    lemonade["client"].unload_model.assert_called_once()
    lemonade["client"].load_model.assert_called_once()


def test_a_model_that_left_lemonade_is_no_longer_counted_as_its_own(lemonade):
    helpers.note_model_loaded("Mine-Old-GGUF")
    lemonade["resident"] = [_model("Other-App-GGUF")]  # Mine-Old was evicted.

    helpers._maybe_load_expected_model("Mine-GGUF")

    assert not helpers._loaded_here("Mine-Old-GGUF")
    assert helpers._loaded_here("Mine-GGUF")


def test_a_stop_during_the_retry_reload_does_not_run_the_query_again(
    lemonade, monkeypatch
):
    import json
    from unittest.mock import AsyncMock

    from gaia.llm.providers.lemonade import LemonadeModelNotLoadedError
    from gaia.ui.database import ChatDatabase
    from gaia.ui.models import ChatRequest
    from gaia.ui.run_manager import RunManager

    db = ChatDatabase(":memory:")
    session = db.create_session(model="Mine-GGUF", agent_type="chat")
    sid = session["id"]
    agent = _CountingAgent()

    def evicted(_message):
        agent.queries += 1
        raise LemonadeModelNotLoadedError()

    agent.process_query = evicted
    helpers._agent_cache.clear()
    helpers._store_agent(sid, "Mine-GGUF", [], agent, "chat")
    monkeypatch.setattr("gaia.ui.run_manager.run_manager", RunManager())
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_maybe_update_session_title", AsyncMock())
    preflights = []

    def preflight(_model, handler=None, _cancel=None, loads=None):
        preflights.append(1)
        if len(preflights) == 2:  # The retry's reload: the user presses Stop.
            handler.cancelled.set()

    monkeypatch.setattr(helpers, "_maybe_load_expected_model", preflight)

    async def run_turn():
        events = []
        request = ChatRequest(session_id=sid, message="hi", stream=True)
        async for chunk in helpers._stream_chat_response(db, session, request):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))
        return events

    try:
        events = asyncio.run(run_turn())
    finally:
        helpers._agent_cache.clear()
        db.close()

    assert agent.queries == 1, "the query ran again after the Stop"
    assert not any("retrying" in (e.get("message") or "") for e in events)


@pytest.mark.parametrize(
    "window,reloaded", [(65536, True), (32768, False)], ids=["auto", "chosen"]
)
def test_a_copy_at_the_servers_own_window_counts_as_an_auto_load(
    lemonade, monkeypatch, window, reloaded
):
    # GAIA's supervisor starts Lemonade with the profile window (64K here), so
    # that is what a request-triggered load comes up with; this chat needs 128K.
    monkeypatch.setattr(lemonade_client, "resolve_ctx_size", lambda *_a, **_k: 131072)
    monkeypatch.setattr(lemonade_client, "active_profile_ctx_size", lambda: 65536)
    lemonade["resident"] = [_model("Mine-GGUF", ctx=window)]

    if reloaded:
        helpers._maybe_load_expected_model("Mine-GGUF")
        lemonade["client"].unload_model.assert_called_once()
    else:
        with pytest.raises(helpers.ModelInUseError):
            helpers._maybe_load_expected_model("Mine-GGUF")
        lemonade["client"].unload_model.assert_not_called()


def test_a_reload_by_the_uis_own_agent_counts_as_its_own(monkeypatch):
    from contextlib import nullcontext

    client = lemonade_client.LemonadeClient(verbose=False)
    monkeypatch.setattr(client, "_model_slot_lease", lambda _m: nullcontext())
    monkeypatch.setattr(client, "_load_model_leased", lambda *_a, **_k: {})

    client.load_model(
        "Mine-Reloaded-GGUF", prompt=False
    )  # As _ensure_model_loaded does.

    assert helpers._loaded_here("Mine-Reloaded-GGUF")


def test_a_non_streamed_turn_lets_its_idle_stop_end_the_model_wait(monkeypatch):
    from gaia.ui.database import ChatDatabase
    from gaia.ui.models import ChatRequest

    db = ChatDatabase(":memory:")
    session = db.create_session(model="Mine-GGUF", agent_type="chat")
    agent = _CountingAgent()
    helpers._agent_cache.clear()
    helpers._store_agent(session["id"], "Mine-GGUF", [], agent, "chat")
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "wait_for_warm_up", lambda *a, **k: None)
    seen = []

    def preflight(_model, _handler=None, cancel_event=None):
        seen.append(cancel_event)
        cancel_event.set()  # The idle stop fires while the wait runs.

    monkeypatch.setattr(helpers, "_maybe_load_expected_model", preflight)
    request = ChatRequest(session_id=session["id"], message="hi", stream=False)
    try:
        asyncio.run(helpers._get_chat_response(db, session, request))
    finally:
        helpers._agent_cache.clear()
        db.close()

    assert seen and seen[0] is not None, "the turn's cancel never reached the wait"
    assert agent.queries == 0


# ── The record and lock nits, each guarded ────────────────────────────────────


def test_the_load_lock_wait_ends_on_the_turns_own_cancel(lemonade):
    cancel = threading.Event()
    finished = threading.Event()

    def preflight():
        helpers._maybe_load_expected_model("Mine-GGUF", None, cancel)
        finished.set()

    with helpers.model_load_lock:  # Another chat is loading.
        threading.Thread(target=preflight, daemon=True).start()
        threading.Event().wait(0.3)
        cancel.set()  # The idle stop: no handler, no Stop flag.
        assert finished.wait(5), "the turn's cancel did not end the lock wait"
    lemonade["client"].load_model.assert_not_called()


def test_a_failed_record_write_keeps_the_previous_record(monkeypatch):
    import os

    helpers.note_model_loaded("Mine-Old-GGUF")
    path = helpers._owned_models_path()
    before = path.read_text(encoding="utf-8")

    def crash(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", crash)
    helpers.note_model_loaded("Mine-New-GGUF")

    assert path.read_text(encoding="utf-8") == before


def test_concurrent_loads_all_land_in_the_record(monkeypatch):
    import json as json_mod
    import pathlib

    real_write = pathlib.Path.write_text

    def slow_first_write(self, data, *args, **kwargs):
        if json_mod.loads(data) == ["first-gguf"]:
            threading.Event().wait(0.3)  # Still writing when the second lands.
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", slow_first_write)
    first = threading.Thread(target=helpers.note_model_loaded, args=("First-GGUF",))
    first.start()
    threading.Event().wait(0.05)
    helpers.note_model_loaded("Second-GGUF")
    first.join(5)

    monkeypatch.setattr(helpers, "_models_loaded_here", None)  # Restart.
    assert helpers._loaded_here("First-GGUF") and helpers._loaded_here("Second-GGUF")


def test_a_copy_that_reports_no_window_is_reloaded(lemonade):
    # The broken state #1030 describes: loaded, but no ctx_size reported.
    copy = _model("Mine-GGUF")
    copy["recipe_options"] = {}
    lemonade["resident"] = [copy]

    helpers._maybe_load_expected_model("Mine-GGUF")

    lemonade["client"].unload_model.assert_called_once()
    lemonade["client"].load_model.assert_called_once()


# ── The panel's load record names only what was really unloaded ──────────────


def test_a_load_that_made_room_records_the_model_it_unloaded(lemonade):
    helpers._maybe_load_expected_model("Mine-Old-GGUF")
    lemonade["resident"].append(_model("Other-App-GGUF"))
    attempts = []

    def load(name, **_kwargs):
        attempts.append(name)
        if len(attempts) == 1:
            raise InsufficientMemoryError("Not enough free memory to load it")
        lemonade["resident"].append(_model(name))

    lemonade["client"].load_model.side_effect = load
    loads = []

    helpers._maybe_load_expected_model("Mine-New-GGUF", loads=loads)

    assert [(r["model"], r["replaced"]) for r in loads] == [
        ("Mine-New-GGUF", ["Mine-Old-GGUF"])
    ]


def test_a_reload_at_a_bigger_window_records_the_copy_it_replaced(lemonade):
    helpers.note_model_loaded("Mine-GGUF")
    lemonade["resident"] = [_model("Mine-GGUF", ctx=32768), _model("Other-App-GGUF")]
    loads = []

    helpers._maybe_load_expected_model("Mine-GGUF", loads=loads)

    assert [r["replaced"] for r in loads] == [["Mine-GGUF"]]


@pytest.mark.parametrize("refusal", ["memory", "in_use"])
def test_a_refused_load_records_nothing(lemonade, refusal):
    if refusal == "memory":
        lemonade["resident"] = [_model("Other-App-GGUF")]
        lemonade["client"].load_model.side_effect = InsufficientMemoryError(
            "Not enough free memory to load Mine-GGUF"
        )
        expected = InsufficientMemoryError
    else:
        lemonade["resident"] = [_model("Mine-GGUF", ctx=32768)]  # Another app's.
        expected = helpers.ModelInUseError
    loads = []

    with pytest.raises(expected):
        helpers._maybe_load_expected_model("Mine-GGUF", loads=loads)

    assert loads == []


def test_a_load_ended_by_stop_records_nothing(lemonade):
    cancel = threading.Event()

    def stop_then_fail(name, **_kwargs):
        cancel.set()
        raise RuntimeError("load interrupted")

    lemonade["client"].load_model.side_effect = stop_then_fail
    loads = []

    helpers._maybe_load_expected_model("Mine-GGUF", None, cancel, loads=loads)

    assert loads == []
