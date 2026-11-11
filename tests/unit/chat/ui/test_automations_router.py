# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Settings → Automations: the list, pause, resume and remove routes."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.automations import store
from gaia.ui.routers import automations

LOCAL = ("127.0.0.1", 50000)


@pytest.fixture(autouse=True)
def gaia_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_HOME", raising=False)


def make_client(peer=LOCAL, headers=None) -> TestClient:
    app = FastAPI()
    app.include_router(automations.router)
    return TestClient(app, client=peer, headers=headers or {"X-Gaia-UI": "1"})


def _add(name, kind=store.KIND_SUMMARY, **kw) -> store.Automation:
    kw.setdefault("prompt", "Summarize my inbox")
    kw.setdefault("every_seconds", 86400 if kind != store.KIND_REMINDER else 0)
    return store.add(store.Automation("", name, kind, next_at=time.time() + 600, **kw))


def test_an_empty_store_lists_nothing():
    body = make_client().get("/api/automations").json()
    assert body == {"automations": [], "delivers": False}


def test_the_list_carries_raw_values_for_the_app_to_word():
    _add("Inbox digest", quiet="22:00-07:00", days="mon,tue")
    _add("Heartbeat", store.KIND_ALERT, every_seconds=1800)
    rows = make_client().get("/api/automations").json()["automations"]
    digest, beat = rows
    assert digest["name"] == "Inbox digest"
    assert digest["every_seconds"] == 86400
    assert digest["quiet"] == "22:00-07:00"
    assert digest["heartbeat"] is False
    assert isinstance(digest["next_at"], float)
    assert beat["heartbeat"] is True


def test_pause_then_resume_by_id():
    item = _add("Inbox digest")
    client = make_client()
    paused = client.post(f"/api/automations/{item.id}/pause").json()
    assert paused["paused"] is True
    assert store.find(store.load(), item.id).paused is True
    resumed = client.post(f"/api/automations/{item.id}/resume").json()
    assert resumed["paused"] is False
    assert store.find(store.load(), item.id).paused is False


def test_remove_deletes_only_that_one():
    keep = _add("Keep me")
    gone = _add("Remove me")
    response = make_client().delete(f"/api/automations/{gone.id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Remove me"
    assert [a.id for a in store.load()] == [keep.id]


def test_a_missing_automation_is_a_404_with_the_names():
    _add("Inbox digest")
    response = make_client().post("/api/automations/nope/pause")
    assert response.status_code == 404
    assert "Inbox digest" in response.json()["detail"]


def test_an_unknown_action_is_refused():
    item = _add("Inbox digest")
    response = make_client().post(f"/api/automations/{item.id}/run")
    assert response.status_code == 422


def test_an_unreadable_store_is_a_500_that_says_so():
    store.path().parent.mkdir(parents=True, exist_ok=True)
    store.path().write_text('{"automations": [{"bogus": 1}]}', encoding="utf-8")
    response = make_client().get("/api/automations")
    assert response.status_code == 500
    assert "automations.json" in response.json()["detail"]


def test_delivers_follows_the_discord_pairing(monkeypatch):
    from gaia.messaging.discord import state

    class Paired:
        paired = True

    monkeypatch.setattr(state, "load_config", lambda: Paired())
    assert make_client().get("/api/automations").json()["delivers"] is True


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/automations"),
        ("post", "/api/automations/x/pause"),
        ("delete", "/api/automations/x"),
    ],
)
def test_mobile_access_is_refused(method, path):
    client = make_client(headers={"X-Gaia-UI": "1", "X-Forwarded-For": "203.0.113.9"})
    assert getattr(client, method)(path).status_code == 403


def test_another_computer_is_refused():
    client = make_client(peer=("192.168.1.20", 50000))
    assert client.get("/api/automations").status_code == 403


def test_a_write_without_the_ui_header_is_refused():
    item = _add("Inbox digest")
    client = make_client(headers={"X-Gaia-UI": ""})
    assert client.delete(f"/api/automations/{item.id}").status_code == 403
    assert len(store.load()) == 1


def test_delivers_is_unknown_when_discord_state_is_unreadable(monkeypatch):
    from gaia.automations import files
    from gaia.messaging.discord import state

    def broken():
        raise files.CorruptStateFile("config.json is not usable")

    monkeypatch.setattr(state, "load_config", broken)
    assert make_client().get("/api/automations").json()["delivers"] is None


def test_an_id_wins_over_an_earlier_automation_named_like_it():
    target = _add("Inbox digest")
    store.save(
        [store.Automation("zzzz0000", target.id, store.KIND_SUMMARY)] + store.load()
    )
    make_client().post(f"/api/automations/{target.id}/pause")
    by_id = {a.id: a for a in store.load()}
    assert by_id[target.id].paused is True
    assert by_id["zzzz0000"].paused is False
