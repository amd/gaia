# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A chat's Internet switch reaches a relayed GAIA agent (#5883).

On a release install the flagship runs as its own program and every turn is
relayed to it. The relay used to drop the chat's Internet setting, so the
switch was locked in every GAIA chat. These check what leaves for the sidecar,
what a sidecar too old to take it is told, and what the session panel reports.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.ui import session_environment
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar import daemon_client, relay
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    internet_supported,
)
from gaia.ui.models import ChatRequest
from gaia.ui.sse_handler import SSEOutputHandler


@pytest.mark.parametrize(
    "version, expected", [("2.22", True), ("3.0", True), ("2.21", False), (None, False)]
)
def test_only_a_flagship_at_2_22_takes_the_switch(version, expected):
    assert internet_supported(GAIA_PROFILE, version) is expected


def test_email_never_takes_the_switch():
    assert EMAIL_PROFILE.min_internet_api_version is None
    assert internet_supported(EMAIL_PROFILE, "9.9") is False


class _Proxy:
    agent_id = "gaia"

    def __init__(self):
        self.bodies: list = []

    def init(self):
        return 200, {}

    def query_stream(self, body, read_timeout, on_response):
        self.bodies.append(body)
        yield {"type": "final", "answer": "ok"}

    def cancel_query(self, _rid):
        pass


def _events(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _dispatch(monkeypatch, version, internet_off, agent_type="gaia"):
    proxy = _Proxy()
    handle = SimpleNamespace(api_version=version, proxy=lambda: proxy)
    monkeypatch.setattr(daemon_client, "acquire_handle", lambda _id: handle)
    monkeypatch.setattr(helpers, "_replay_cloud_key_for_relay", lambda *_a: None)
    handler = SSEOutputHandler()
    helpers._dispatch_sidecar_query(
        handler,
        ChatRequest(session_id="s", message="weather today", agent_type=agent_type),
        [],
        None,
        agent_type,
        internet_off=internet_off,
    )
    return proxy.bodies, _events(handler)


@pytest.mark.parametrize("internet_off", [True, False])
def test_a_current_flagship_is_told_the_switch_every_turn(monkeypatch, internet_off):
    bodies, events = _dispatch(monkeypatch, "2.22", internet_off)
    [body] = bodies
    assert body["internet_off"] is internet_off
    assert not [e for e in events if e["type"] == "agent_error"]


def test_an_internet_off_turn_is_stopped_before_a_flagship_too_old_to_honour_it(
    monkeypatch,
):
    bodies, events = _dispatch(monkeypatch, "2.21", True)
    assert bodies == []
    assert events == [
        {"type": "agent_error", "content": GAIA_PROFILE.internet_upgrade_message}
    ]


def test_an_internet_on_turn_still_runs_on_an_older_flagship(monkeypatch):
    bodies, events = _dispatch(monkeypatch, "2.21", False)
    [body] = bodies
    assert "internet_off" not in body
    assert not [e for e in events if e["type"] == "agent_error"]


def test_the_email_sidecar_is_never_sent_the_switch(monkeypatch):
    proxy = _Proxy()
    proxy.agent_id = "email"
    relay.relay_query(
        SSEOutputHandler(), proxy, query="q", context=[], profile=EMAIL_PROFILE
    )
    [body] = proxy.bodies
    assert "internet_off" not in body


def test_the_relay_omits_a_switch_it_was_not_given():
    proxy = _Proxy()
    relay.relay_query(
        SSEOutputHandler(), proxy, query="q", context=[], profile=GAIA_PROFILE
    )
    assert "internet_off" not in proxy.bodies[0]


# ── The chat's setting reaches the relay ───────────────────────────────────


@pytest.fixture
def sidecar_flagship(tmp_path, monkeypatch):
    """The registry after a binary install of the flagship: every turn relays."""
    from gaia.agents.registry import AgentRegistry
    from gaia.hub.installer import register_installed_sidecars

    root = tmp_path / "agents"
    (root / "gaia").mkdir(parents=True)
    (root / "gaia" / ".installed").write_text(
        json.dumps(
            {
                "id": "gaia",
                "version": "0.2.0",
                "language": "python",
                "installed_at": "2026-10-22T00:00:00Z",
                "artifact_sha256": "0" * 64,
                "artifact_kind": "binary",
                "executable": "gaia-agent-gaia",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gaia.hub.installer.default_install_root", lambda: root, raising=False
    )
    registry = AgentRegistry()
    registry._agents.clear()
    register_installed_sidecars(registry)
    monkeypatch.setattr(helpers, "_agent_registry", registry)


@pytest.mark.parametrize("off", [True, False])
def test_a_streamed_chat_relays_the_switch_the_panel_saved(
    monkeypatch, sidecar_flagship, off
):
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:18767/api/v1")
    monkeypatch.setenv("LEMONADE_API_KEY", "test-lemonade-key")
    relayed = {}

    def _fake_dispatch(sse_handler, request, history_pairs, model_id, agent, **kw):
        relayed.update(kw)
        sse_handler._emit({"type": "answer", "content": "done"})

    monkeypatch.setattr(helpers, "_dispatch_sidecar_query", _fake_dispatch)
    db = ChatDatabase(":memory:")
    session = db.create_session(title="Weather", agent_type="gaia")
    session_environment.set_internet(db, session["id"], not off)
    db.begin_turn(session["id"], "weather today")
    request = ChatRequest(
        session_id=session["id"],
        message="weather today",
        stream=True,
        agent_type="gaia",
    )

    async def _drive():
        async for _ in helpers._stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            pass
        await asyncio.gather(*list(helpers._background_tasks))

    asyncio.run(_drive())

    assert relayed["internet_off"] is off


# ── The session panel ──────────────────────────────────────────────────────


@pytest.fixture
def relayed_db(monkeypatch):
    monkeypatch.setattr(session_environment, "relayed", lambda _session: True)
    monkeypatch.setattr(session_environment, "_live_agent", lambda _sid: None)
    return ChatDatabase(":memory:")


def test_a_relayed_flagship_chat_can_switch_the_internet(relayed_db):
    session = relayed_db.create_session(agent_type="gaia")

    body = session_environment.describe(relayed_db, session)
    assert body["internet_enforced"] is True
    assert body["enforced"] is False

    session_environment.set_internet(relayed_db, session["id"], False)
    assert session_environment.load(relayed_db, session["id"])["internet_off"] is True
    assert session_environment.describe(relayed_db, session)["internet"] is False


def test_a_relayed_flagship_chat_still_refuses_folder_changes(relayed_db, tmp_path):
    session = relayed_db.create_session(agent_type="gaia")
    with pytest.raises(session_environment.EnvironmentChangeError):
        session_environment.add_folder(relayed_db, session["id"], str(tmp_path))


def test_a_relayed_email_chat_keeps_its_own_network_rules(relayed_db):
    session = relayed_db.create_session(agent_type="email")

    assert (
        session_environment.describe(relayed_db, session)["internet_enforced"] is False
    )

    with pytest.raises(session_environment.EnvironmentChangeError) as refusal:
        session_environment.set_internet(relayed_db, session["id"], False)
    assert "network rules" in str(refusal.value)
    assert "start a chat" not in str(refusal.value).lower()
    assert session_environment.load(relayed_db, session["id"])["internet_off"] is False


def test_an_in_process_chat_enforces_both(monkeypatch):
    monkeypatch.setattr(session_environment, "relayed", lambda _session: False)
    monkeypatch.setattr(session_environment, "_live_agent", lambda _sid: None)
    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="gaia")

    body = session_environment.describe(db, session)

    assert body["internet_enforced"] is True
    assert body["enforced"] is True
