# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A chat's folders and working folder reach a relayed flagship (contract 2.18).

The desktop app relays the flagship to its daemon sidecar, and the Agent UI
used to refuse adding a folder to such a chat because the sidecar was never
told. These drive the real chat stream against the real flagship app.
"""

from __future__ import annotations

import json

import pytest

import gaia.ui._chat_helpers as chat_helpers_module
from gaia.ui import session_environment
from gaia.ui.email_sidecar import daemon_client as daemon_client_module
from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    folders_supported,
)
from gaia.ui.models import ChatRequest
from gaia.ui.sse_handler import SSEOutputHandler
from tests.unit.test_relay_flagship_documents import (  # noqa: F401
    _point_ui_at,
    chat,
    live_gaia_sidecar,
    sidecar_flagship_registry,
)

pytestmark = pytest.mark.allow_network


@pytest.mark.parametrize(
    "version, expected", [("2.18", True), ("3.0", True), ("2.17", False), (None, False)]
)
def test_only_a_flagship_at_2_18_takes_folders(version, expected):
    assert folders_supported(GAIA_PROFILE, version) is expected


def test_email_never_takes_folders():
    assert folders_supported(EMAIL_PROFILE, "9.9") is False


def test_the_scope_is_sent_whole_in_the_query_body():
    body: dict = {}
    relay._add_scope(
        body, {"folders": ["/a"], "withheld": ["/a/b"], "working_folder": "/w"}
    )
    assert body == {
        "folders": ["/a"],
        "withheld_folders": ["/a/b"],
        "working_folder": "/w",
    }
    untouched: dict = {}
    relay._add_scope(untouched, None)
    assert untouched == {}


def test_the_default_working_folder_is_sent_when_none_was_picked():
    body: dict = {}
    relay._add_scope(
        body,
        {
            "folders": [],
            "withheld": [],
            "working_folder": None,
            "default_working_folder": "/d",
        },
    )
    assert body["working_folder"] == "/d"


def _fake_handle(monkeypatch, version):
    class _Handle:
        api_version = version

        def proxy(self):
            class _Proxy:
                def init(self):
                    return 200, {}

            return _Proxy()

    monkeypatch.setattr(daemon_client_module, "acquire_handle", lambda _id: _Handle())
    calls: list = []
    monkeypatch.setattr(relay, "relay_query", lambda *a, **k: calls.append(k))
    return calls


def _dispatch(scope):
    handler = SSEOutputHandler()
    chat_helpers_module._dispatch_sidecar_query(
        handler,
        ChatRequest(session_id="s", message="hi", agent_type="gaia"),
        [],
        None,
        "gaia",
        scope=scope,
    )
    return handler


def test_an_older_flagship_with_folders_says_it_needs_an_update(monkeypatch):
    calls = _fake_handle(monkeypatch, "2.17")
    handler = _dispatch({"folders": ["/a"], "withheld": [], "working_folder": None})
    assert calls == []
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    assert any("contract 2.18" in json.dumps(e) for e in events), events


def test_an_older_flagship_without_folders_still_chats(monkeypatch):
    calls = _fake_handle(monkeypatch, "2.17")
    _dispatch({"folders": [], "withheld": [], "working_folder": None})
    [kwargs] = calls
    assert kwargs["scope"] is None


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


_DEFAULT_ONLY = {
    "folders": [],
    "withheld": [],
    "working_folder": None,
    "default_working_folder": "/env/project",
}


def test_an_older_flagship_still_chats_with_only_the_env_default(monkeypatch):
    """GAIA_WORKING_DIR alone must not stop every chat on a 2.17 agent."""
    calls = _fake_handle(monkeypatch, "2.17")
    handler = _dispatch(_DEFAULT_ONLY)
    [kwargs] = calls
    assert kwargs["scope"] is None
    events = _drain(handler)
    assert not any(e.get("type") == "agent_error" for e in events), events
    [warning] = [e for e in events if e.get("status") == "warning"]
    assert "GAIA_WORKING_DIR" in warning["message"]
    assert "/env/project" in warning["message"]


def test_an_older_flagship_with_a_picked_folder_says_it_needs_an_update(
    monkeypatch,
):
    calls = _fake_handle(monkeypatch, "2.17")
    handler = _dispatch({**_DEFAULT_ONLY, "working_folder": "/picked"})
    assert calls == []
    assert any("contract 2.18" in json.dumps(e) for e in _drain(handler))


def test_a_current_flagship_is_sent_the_env_default(monkeypatch):
    calls = _fake_handle(monkeypatch, "2.18")
    _dispatch(_DEFAULT_ONLY)
    [kwargs] = calls
    assert kwargs["scope"] == _DEFAULT_ONLY


def test_the_relayed_scope_keeps_the_pick_apart_from_the_env_default(
    monkeypatch, chat, tmp_path  # noqa: F811
):
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    monkeypatch.setenv("GAIA_WORKING_DIR", str(env_dir))
    scope = session_environment.relayed_scope(chat.db, chat.session_id)
    assert scope["working_folder"] is None
    assert scope["default_working_folder"] == str(env_dir.resolve())

    picked = tmp_path / "picked"
    picked.mkdir()
    session_environment.set_working_folder(chat.db, chat.session_id, str(picked))
    scope = session_environment.relayed_scope(chat.db, chat.session_id)
    assert scope["working_folder"] == str(picked.resolve())
    assert scope["default_working_folder"] is None


def test_folders_added_to_a_relayed_chat_reach_the_flagship(
    monkeypatch, live_gaia_sidecar, chat, tmp_path  # noqa: F811
):
    base_url, agents, api_version = live_gaia_sidecar
    _point_ui_at(monkeypatch, base_url, api_version)
    db = chat.db
    project = tmp_path / "project"
    project.mkdir()
    picked = tmp_path / "picked"
    picked.mkdir()

    session_environment.set_working_folder(db, chat.session_id, str(picked))
    session_environment.add_folder(db, chat.session_id, str(project))
    chat.send("hi")

    [agent] = agents
    validator = agent.path_validator
    assert validator.is_path_allowed(str(project / "x.txt"), prompt_user=False)
    assert validator.is_path_allowed(str(picked / "x.txt"), prompt_user=False)

    session_environment.remove_folder(db, chat.session_id, str(project))
    chat.send("and now?")
    assert not validator.is_path_allowed(str(project / "x.txt"), prompt_user=False)


def test_a_working_dir_deleted_after_startup_names_the_fix(
    monkeypatch, chat, tmp_path  # noqa: F811
):
    """The remedy reaches the user, not a generic "try again"."""
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.setenv("GAIA_WORKING_DIR", str(gone))
    gone.rmdir()
    events = chat.send("hi")
    [error] = [e for e in events if e.get("type") == "error"]
    assert "GAIA_WORKING_DIR" in error["content"]
    assert "is not a folder" in error["content"]

    # The panel and the start screen still load.
    session = chat.db.get_session(chat.session_id)
    assert session_environment.describe(chat.db, session)["session_id"]
    from gaia.ui.start_screen import working_folder

    assert working_folder()["gaia_folder"] is True
