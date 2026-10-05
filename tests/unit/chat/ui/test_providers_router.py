# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""AI provider and model selection for the Agent UI (``/api/providers``).

Lemonade is faked at the HTTP layer and every outgoing request is recorded, so
the tests assert the exact request shapes Lemonade must accept — the same ones
``tui/internal/lemonade/cloud.go`` sends — not merely that a call happened. The
OS credential store is never touched: ``gaia.llm.cloud_keys`` is mocked.
"""

import json
import logging
import re
from pathlib import Path

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia import config as config_mod
from gaia.connectors.errors import ConnectorsError
from gaia.llm import cloud_keys, lemonade_client
from gaia.llm.cloud_keys import CloudKeyError
from gaia.llm.gateway import (
    DEFAULT_AUTH_HEADER_NAME,
    DEFAULT_AUTH_HEADER_PREFIX,
    DEFAULT_GATEWAY_BASE_URL,
)
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.llm.recommended_models import RECOMMENDED_MODELS, rank
from gaia.ui.database import ChatDatabase
from gaia.ui.routers import providers

REPO_ROOT = Path(__file__).resolve().parents[4]
CLOUD_GO = REPO_ROOT / "tui" / "internal" / "lemonade" / "cloud.go"

BASE = "http://127.0.0.1:13305/api/v1"
KEY = "fw_SECRET_key_do_not_leak_123"


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        return self._body


class FakeLemonade:
    """Lemonade's HTTP surface: records every request, answers from a table."""

    def __init__(self):
        self.calls = []
        self.cloud = {}  # provider -> system-info entry
        self.models = []
        self.answers = {}  # (method, path) -> (status, body) or exception
        self.down = False

    def request(self, method, url, json=None, headers=None, **kwargs):
        assert url.startswith(BASE + "/"), url
        path = url[len(BASE) + 1 :]
        self.calls.append({"method": method, "path": path, "json": json, **kwargs})
        if self.down:
            raise requests.ConnectionError("connection refused")
        answer = self.answers.get((method, path))
        if isinstance(answer, Exception):
            raise answer
        if answer is not None:
            return _Resp(*answer)
        if (method, path) == ("GET", "system-info"):
            return _Resp(200, {"cloud": {"providers": list(self.cloud.values())}})
        if (method, path) == ("GET", "models?show_all=true"):
            return _Resp(200, {"data": self.models})
        if (method, path) == ("POST", "install"):
            self.cloud.setdefault(json["provider"], {"name": json["provider"]})
            self.cloud[json["provider"]]["base_url"] = json["base_url"]
            return _Resp(200, {"status": "ok"})
        if (method, path) == ("POST", "cloud/auth"):
            entry = self.cloud.setdefault(json["provider"], {"name": json["provider"]})
            entry.update(runtime_key_set=True, models_discovered=True)
            return _Resp(200, {"models_discovered": True})
        if method == "DELETE" and path.startswith("cloud/auth/"):
            return _Resp(200, {})
        raise AssertionError(f"unexpected Lemonade call {method} {path}")

    def paths(self, method=None):
        return [c["path"] for c in self.calls if method in (None, c["method"])]

    def call(self, method, path):
        matches = [c for c in self.calls if (c["method"], c["path"]) == (method, path)]
        assert matches, f"no {method} {path} in {self.paths()}"
        return matches[-1]


@pytest.fixture
def lemonade(monkeypatch):
    fake = FakeLemonade()
    monkeypatch.setattr(providers.requests, "request", fake.request)
    monkeypatch.setattr(providers, "_base_url", lambda: BASE)
    monkeypatch.setattr(
        lemonade_client, "resolve_lemonade_api_key", lambda *a, **k: None
    )
    return fake


@pytest.fixture
def keys(monkeypatch):
    """The OS credential store and the stored-key replay, faked."""
    state = {"stored": {}, "remembered": [], "ensure": [], "ensure_result": True}

    def remember_key(provider, key):
        if state.get("remember_error"):
            raise CloudKeyError(state["remember_error"])
        state["remembered"].append((provider, key))
        state["stored"][provider] = key

    def recall_key(provider):
        return state["stored"].get(provider)

    def forget_key(provider):
        if state.get("forget_error"):
            raise state["forget_error"]
        return state["stored"].pop(provider, None) is not None

    def ensure_authenticated(provider, client=None):
        state["ensure"].append(provider)
        if isinstance(state["ensure_result"], Exception):
            raise state["ensure_result"]
        return state["ensure_result"]

    monkeypatch.setattr(cloud_keys, "remember_key", remember_key)
    monkeypatch.setattr(cloud_keys, "recall_key", recall_key)
    monkeypatch.setattr(cloud_keys, "forget_key", forget_key)
    monkeypatch.setattr(cloud_keys, "ensure_authenticated", ensure_authenticated)
    return state


@pytest.fixture(autouse=True)
def _fresh_restore():
    providers.reset_restore_state()
    yield
    providers.reset_restore_state()


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(providers.router)
    app.state.db = db
    return TestClient(app, headers={"X-Gaia-UI": "1"})


def _config():
    return json.loads(config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8"))


def _write_config(data):
    config_mod.GAIA_CONFIG_FILE.write_text(json.dumps(data), encoding="utf-8")


# ── GET /api/providers ─────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/api/providers", "/api/providers/active"])
def test_side_effecting_reads_need_the_ui_header(client, monkeypatch, path):
    """A cross-site GET carries no X-Gaia-UI, so it must not push a key or restore."""
    called = []
    monkeypatch.setattr(providers, "_cloud_entry", lambda p: called.append(p))
    monkeypatch.setattr(providers, "restore_last_model", lambda db: called.append(db))
    resp = client.get(path, headers={"X-Gaia-UI": ""})
    assert resp.status_code == 403
    assert called == []


def test_list_when_lemonade_is_unreachable(client, lemonade, keys):
    lemonade.down = True
    body = client.get("/api/providers").json()
    assert "not reachable" in body["lemonade_error"]
    assert [p["id"] for p in body["providers"]] == ["local", "fireworks", "amd"]
    assert all(not p.get("registered") for p in body["providers"][1:])
    assert body["active"]["model"] == DEFAULT_MODEL_NAME
    assert body["active"]["is_default"] is True


def test_list_does_not_repeat_a_provider_when_a_later_lookup_fails(
    client, lemonade, keys, monkeypatch
):
    seen = []
    real = lemonade.request

    def flaky(method, url, **kwargs):
        seen.append(url)
        if len(seen) > 1:
            raise requests.ConnectionError("dropped")
        return real(method, url, **kwargs)

    monkeypatch.setattr(providers.requests, "request", flaky)
    body = client.get("/api/providers").json()
    assert body["lemonade_error"]
    assert [p["id"] for p in body["providers"]] == ["local", "fireworks", "amd"]


@pytest.mark.parametrize(
    "entry,stored,expected",
    [
        ({"env_var_set": True, "runtime_key_set": True}, None, "environment"),
        ({"runtime_key_set": True}, "k", "lemonade"),
        ({}, None, None),
    ],
)
def test_key_source(client, lemonade, keys, entry, stored, expected):
    lemonade.cloud["fireworks"] = {"name": "fireworks", **entry}
    if stored:
        keys["stored"]["fireworks"] = stored
    fw = client.get("/api/providers").json()["providers"][1]
    assert fw["id"] == "fireworks"
    assert fw["registered"] is True
    assert fw["key_source"] == expected
    assert fw["env_var"] == "LEMONADE_FIREWORKS_API_KEY"
    assert keys["ensure"] == []


def test_stored_key_is_handed_to_a_lemonade_that_lost_it(
    client, lemonade, keys, monkeypatch
):
    lemonade.cloud["fireworks"] = {"name": "fireworks"}
    keys["stored"]["fireworks"] = KEY

    def ensure(provider, client=None):
        keys["ensure"].append(provider)
        lemonade.cloud[provider].update(runtime_key_set=True, models_discovered=True)
        return True

    monkeypatch.setattr(cloud_keys, "ensure_authenticated", ensure)
    resp = client.get("/api/providers")
    fw = resp.json()["providers"][1]
    assert keys["ensure"] == ["fireworks"]
    assert fw["key_source"] == "lemonade"
    assert fw["models_discovered"] is True
    assert KEY not in resp.text


def test_stored_key_replay_failure_is_reported_on_the_provider(client, lemonade, keys):
    lemonade.cloud["fireworks"] = {"name": "fireworks"}
    keys["stored"]["fireworks"] = KEY
    keys["ensure_result"] = CloudKeyError("Lemonade answered POST /cloud/auth with 500")
    fw = client.get("/api/providers").json()["providers"][1]
    assert fw["key_source"] == "stored"
    assert "500" in fw["error"]


# ── GET /api/providers/active ──────────────────────────────────────────────


def _cloud_models():
    return [
        {
            "id": "fireworks.accounts/fireworks/models/glm-5p3-flash",
            "recipe": "cloud",
            "labels": [],
        },
        {"id": "fireworks.some-other-model", "recipe": "cloud", "labels": []},
    ]


def test_active_with_nothing_saved(client, lemonade, keys, db):
    body = client.get("/api/providers/active").json()
    assert body["restore"] == {"status": "none", "message": None}
    assert body["model"] == DEFAULT_MODEL_NAME
    assert db.get_setting("custom_model") is None
    assert lemonade.calls == []


def test_active_restores_the_last_model_once_per_run(client, lemonade, keys, db):
    model = "fireworks.accounts/fireworks/models/glm-5p3-flash"
    _write_config({"last_model": model})
    lemonade.models = _cloud_models()
    body = client.get("/api/providers/active").json()
    assert body["restore"]["status"] == "restored"
    assert body["model"] == model
    assert body["provider"] == "fireworks"
    assert body["remote"] is True
    assert body["label"] == "glm-5p3-flash · Fireworks AI"
    assert db.get_setting("custom_model") == model

    _write_config({"last_model": "something-else"})
    calls = len(lemonade.calls)
    again = client.get("/api/providers/active").json()
    assert again["restore"]["status"] == "restored"
    assert len(lemonade.calls) == calls


def test_active_never_switches_model_when_the_saved_one_cannot_run(
    client, lemonade, keys, db
):
    db.set_setting("custom_model", "Qwen3-4B-GGUF")
    _write_config({"last_model": "fireworks.gone-model"})
    lemonade.models = _cloud_models()
    body = client.get("/api/providers/active").json()
    assert body["restore"]["status"] == "failed"
    assert "no longer listed" in body["restore"]["message"]
    assert "Pick a model" in body["restore"]["message"]
    assert db.get_setting("custom_model") == "Qwen3-4B-GGUF"
    assert body["model"] == "Qwen3-4B-GGUF"


def test_active_restore_fails_without_a_working_key(client, lemonade, keys, db):
    _write_config({"last_model": "fireworks.glm-5p3-flash"})
    keys["ensure_result"] = False
    body = client.get("/api/providers/active").json()
    assert body["restore"]["status"] == "failed"
    assert "Fireworks AI has no working key" in body["restore"]["message"]
    assert db.get_setting("custom_model") is None


def test_active_restore_fails_for_a_local_model_not_downloaded(
    client, lemonade, keys, db
):
    _write_config({"last_model": DEFAULT_MODEL_NAME})
    lemonade.models = [{"id": DEFAULT_MODEL_NAME, "downloaded": False, "labels": []}]
    body = client.get("/api/providers/active").json()
    assert body["restore"]["status"] == "failed"
    assert "not downloaded" in body["restore"]["message"]


def test_active_restore_reports_a_corrupt_config(client, lemonade, keys, db):
    config_mod.GAIA_CONFIG_FILE.write_text("{not json", encoding="utf-8")
    body = client.get("/api/providers/active").json()
    assert body["restore"]["status"] == "failed"
    assert db.get_setting("custom_model") is None


# ── POST /api/providers/{p}/connect ────────────────────────────────────────


def test_connect_fireworks_sends_the_tui_request_shapes(client, lemonade, keys):
    resp = client.post("/api/providers/fireworks/connect", json={"api_key": KEY})
    assert resp.status_code == 200, resp.text
    assert lemonade.call("POST", "install")["json"] == {
        "backend": "cloud",
        "provider": "fireworks",
        "wire_format": "openai",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "auth_header_name": "Authorization",
        "auth_header_prefix": "Bearer ",
    }
    assert lemonade.call("POST", "cloud/auth")["json"] == {
        "provider": "fireworks",
        "api_key": KEY,
    }
    assert lemonade.paths() == ["install", "cloud/auth", "system-info"]
    assert all(c["allow_redirects"] is False for c in lemonade.calls)
    body = resp.json()
    assert body["remembered"] is True and body["remember_error"] is None
    assert body["models_discovered"] is True
    assert keys["remembered"] == [("fireworks", KEY)]
    assert KEY not in resp.text


def test_connect_amd_uses_the_gateway_defaults(client, lemonade, keys):
    resp = client.post("/api/providers/amd/connect", json={"api_key": KEY})
    assert resp.status_code == 200, resp.text
    assert lemonade.call("POST", "install")["json"] == {
        "backend": "cloud",
        "provider": "amd",
        "wire_format": "openai",
        "base_url": DEFAULT_GATEWAY_BASE_URL,
        "auth_header_name": DEFAULT_AUTH_HEADER_NAME,
        "auth_header_prefix": DEFAULT_AUTH_HEADER_PREFIX,
    }


def test_connect_amd_takes_a_custom_gateway(client, lemonade, keys):
    client.post(
        "/api/providers/amd/connect",
        json={
            "api_key": KEY,
            "base_url": "  https://gw.example/v1  ",
            "auth_header_name": " X-Key ",
            "auth_header_prefix": "",
        },
    )
    payload = lemonade.call("POST", "install")["json"]
    assert payload["base_url"] == "https://gw.example/v1"
    assert payload["auth_header_name"] == "X-Key"
    assert payload["auth_header_prefix"] == ""


def test_connect_ignores_gateway_fields_for_fireworks(client, lemonade, keys):
    client.post(
        "/api/providers/fireworks/connect",
        json={"api_key": KEY, "base_url": "https://evil.example"},
    )
    assert (
        lemonade.call("POST", "install")["json"]["base_url"]
        == "https://api.fireworks.ai/inference/v1"
    )


def test_connect_reports_a_key_the_store_did_not_keep(client, lemonade, keys):
    keys["remember_error"] = "The OS credential store did not keep the key"
    resp = client.post("/api/providers/fireworks/connect", json={"api_key": KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["remembered"] is False
    assert "did not keep" in body["remember_error"]


def test_connect_drops_a_key_that_discovers_no_models(client, lemonade, keys):
    lemonade.answers[("POST", "cloud/auth")] = (200, {"models_discovered": False})
    resp = client.post("/api/providers/fireworks/connect", json={"api_key": KEY})
    assert resp.status_code == 401
    assert "did not accept that key" in resp.json()["detail"]
    assert "DELETE" in [c["method"] for c in lemonade.calls]
    assert lemonade.call("DELETE", "cloud/auth/fireworks")
    assert keys["remembered"] == []
    assert KEY not in resp.text


@pytest.mark.parametrize("status", [401, 403])
def test_connect_rejected_key_is_401(client, lemonade, keys, status):
    lemonade.answers[("POST", "cloud/auth")] = (status, {"error": {"message": KEY}})
    resp = client.post("/api/providers/fireworks/connect", json={"api_key": KEY})
    assert resp.status_code == 401
    assert "rejected the key" in resp.json()["detail"]
    assert KEY not in resp.text
    assert keys["remembered"] == []


def test_connect_environment_key_wins_409(client, lemonade, keys):
    lemonade.answers[("POST", "cloud/auth")] = (409, {})
    resp = client.post("/api/providers/fireworks/connect", json={"api_key": KEY})
    assert resp.status_code == 409
    assert "LEMONADE_FIREWORKS_API_KEY" in resp.json()["detail"]


def test_connect_old_lemonade_409(client, lemonade, keys):
    lemonade.answers[("POST", "install")] = (404, {})
    resp = client.post("/api/providers/fireworks/connect", json={"api_key": KEY})
    assert resp.status_code == 409
    assert "Update Lemonade" in resp.json()["detail"]
    assert "cloud/auth" not in lemonade.paths()


def test_connect_never_echoes_lemonade_errors_about_the_key(
    client, lemonade, keys, caplog
):
    lemonade.answers[("POST", "cloud/auth")] = (
        500,
        {"error": {"message": f"bad key {KEY}"}},
    )
    with caplog.at_level(logging.DEBUG):
        resp = client.post("/api/providers/fireworks/connect", json={"api_key": KEY})
    assert resp.status_code == 502
    assert "HTTP 500" in resp.json()["detail"]
    assert KEY not in resp.text
    assert KEY not in caplog.text


def test_connect_install_errors_are_named(client, lemonade, keys):
    lemonade.answers[("POST", "install")] = (500, {"error": {"message": "disk full"}})
    resp = client.post("/api/providers/fireworks/connect", json={"api_key": KEY})
    assert resp.status_code == 502
    assert "disk full" in resp.json()["detail"]


def test_connect_refuses_a_key_over_the_tunnel(client, lemonade, keys):
    resp = client.post(
        "/api/providers/fireworks/connect",
        json={"api_key": KEY},
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert resp.status_code == 403
    assert lemonade.calls == []
    assert KEY not in resp.text


def test_connect_without_a_key_over_the_tunnel_is_allowed(client, lemonade, keys):
    lemonade.cloud["fireworks"] = {
        "name": "fireworks",
        "runtime_key_set": True,
        "models_discovered": True,
    }
    resp = client.post(
        "/api/providers/fireworks/connect",
        json={},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert resp.status_code == 200, resp.text
    assert keys["ensure"] == ["fireworks"]
    assert "cloud/auth" not in lemonade.paths()


def test_connect_refuses_a_remote_lemonade(client, lemonade, keys, monkeypatch):
    monkeypatch.setattr(providers, "_base_url", lambda: "http://10.0.0.5:13305/api/v1")
    resp = client.post("/api/providers/fireworks/connect", json={"api_key": KEY})
    assert resp.status_code == 409
    assert "not on this PC" in resp.json()["detail"]
    assert lemonade.calls == []


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_loopback_hosts_are_local(host):
    providers._require_loopback(f"http://{host}:13305/api/v1")


def test_connect_blank_key_replays_the_stored_one(client, lemonade, keys):
    lemonade.cloud["fireworks"] = {"name": "fireworks", "models_discovered": True}
    resp = client.post("/api/providers/fireworks/connect", json={"api_key": "  "})
    assert resp.status_code == 200
    assert keys["ensure"] == ["fireworks"]
    assert resp.json()["remembered"] is False


def test_connect_blank_key_replay_failure_is_502(client, lemonade, keys):
    keys["ensure_result"] = CloudKeyError("Lemonade is not reachable")
    resp = client.post("/api/providers/fireworks/connect", json={})
    assert resp.status_code == 502


def test_connect_registered_without_models_is_401(client, lemonade, keys):
    resp = client.post("/api/providers/fireworks/connect", json={})
    assert resp.status_code == 401
    assert "discovered no models" in resp.json()["detail"]


def test_connect_local_and_unknown(client, lemonade, keys):
    assert client.post("/api/providers/local/connect", json={}).status_code == 422
    assert client.post("/api/providers/openai/connect", json={}).status_code == 404
    assert lemonade.calls == []


# ── DELETE /api/providers/{p}/key ──────────────────────────────────────────


def test_forget_key_clears_lemonade_and_the_store(client, lemonade, keys):
    keys["stored"]["fireworks"] = KEY
    resp = client.delete("/api/providers/fireworks/key")
    assert resp.status_code == 200
    assert resp.json() == {"removed": True}
    assert lemonade.paths("DELETE") == ["cloud/auth/fireworks"]
    assert "fireworks" not in keys["stored"]


def test_forget_key_tolerates_a_lemonade_without_one(client, lemonade, keys):
    lemonade.answers[("DELETE", "cloud/auth/amd")] = (404, {})
    resp = client.delete("/api/providers/amd/key")
    assert resp.json() == {"removed": False}


def test_forget_key_lemonade_error_is_502(client, lemonade, keys):
    lemonade.answers[("DELETE", "cloud/auth/fireworks")] = (500, {})
    keys["stored"]["fireworks"] = KEY
    resp = client.delete("/api/providers/fireworks/key")
    assert resp.status_code == 502
    assert "fireworks" in keys["stored"]


def test_forget_key_store_error_is_503(client, lemonade, keys):
    keys["forget_error"] = ConnectorsError("keyring locked")
    resp = client.delete("/api/providers/fireworks/key")
    assert resp.status_code == 503
    assert "keyring locked" in resp.json()["detail"]


def test_forget_key_local_is_422(client, lemonade, keys):
    assert client.delete("/api/providers/local/key").status_code == 422


# ── GET /api/providers/{p}/models ──────────────────────────────────────────

_CATALOG = [
    {"id": "Qwen3-4B-GGUF", "downloaded": True, "labels": ["tool-calling"]},
    {"id": "Llama-3-8B-GGUF", "downloaded": False, "labels": []},
    {"id": DEFAULT_MODEL_NAME, "downloaded": False, "labels": ["vision"]},
    {"id": "nomic-embed-GGUF", "downloaded": True, "labels": ["embeddings"]},
    {"id": "Whisper-Large", "downloaded": True, "labels": ["transcription"]},
    {"id": "Whisper-RT", "downloaded": True, "labels": ["realtime-transcription"]},
    {"id": "SDXL-Turbo", "downloaded": True, "labels": ["image"]},
    {"id": "bge-reranker", "downloaded": True, "labels": ["Reranker"]},
    {"id": "fireworks.zz-model", "recipe": "cloud", "labels": []},
    {
        "id": "fireworks.accounts/fireworks/models/deepseek-v4p1-flash",
        "recipe": "cloud",
        "labels": [],
    },
    {"id": "fireworks.glm-5p3-flash", "cloud_provider": "fireworks", "labels": []},
    {"id": "fireworks.aa-model", "recipe": "cloud", "labels": []},
    {"id": "fireworks.embed", "recipe": "cloud", "labels": ["embedding"]},
    {"id": "amd.gpt-oss", "recipe": "cloud", "labels": [], "downloaded": True},
]


def test_local_models_are_downloaded_chat_models_plus_the_default(client, lemonade):
    lemonade.models = _CATALOG
    resp = client.get("/api/providers/local/models")
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["models"]]
    assert sorted(ids) == sorted([DEFAULT_MODEL_NAME, "Qwen3-4B-GGUF"])
    by_id = {m["id"]: m for m in resp.json()["models"]}
    assert by_id[DEFAULT_MODEL_NAME]["downloaded"] is False
    assert by_id["Qwen3-4B-GGUF"]["rank"] is None


def test_cloud_models_are_prefixed_and_ranked_first(client, lemonade):
    lemonade.models = _CATALOG
    models = client.get("/api/providers/fireworks/models").json()["models"]
    assert [m["id"] for m in models] == [
        "fireworks.glm-5p3-flash",
        "fireworks.accounts/fireworks/models/deepseek-v4p1-flash",
        "fireworks.aa-model",
        "fireworks.zz-model",
    ]
    top = models[0]
    assert (top["rank"], top["note"]) == (1, "best overall, cheapest")
    assert top["evidence"] == RECOMMENDED_MODELS[0].evidence
    assert top["downloaded"] is True
    assert models[1]["rank"] == 2
    assert models[2]["note"] is None and models[2]["evidence"] is None


def test_amd_models_only(client, lemonade):
    lemonade.models = _CATALOG
    models = client.get("/api/providers/amd/models").json()["models"]
    assert [m["id"] for m in models] == ["amd.gpt-oss"]


def test_models_lemonade_error_is_502(client, lemonade):
    lemonade.answers[("GET", "models?show_all=true")] = (500, {})
    resp = client.get("/api/providers/fireworks/models")
    assert resp.status_code == 502


def test_models_unknown_provider_is_404(client, lemonade):
    assert client.get("/api/providers/nope/models").status_code == 404


# ── POST /api/providers/select ─────────────────────────────────────────────


def test_select_remembers_the_model_and_keeps_other_config(client, lemonade, keys, db):
    _write_config({"full_access": True, "written_by_tui": {"x": 1}})
    lemonade.models = _CATALOG
    resp = client.post("/api/providers/select", json={"model": "fireworks.aa-model"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["model"] == "fireworks.aa-model"
    assert resp.json()["is_default"] is False
    assert db.get_setting("custom_model") == "fireworks.aa-model"
    assert _config() == {
        "full_access": True,
        "written_by_tui": {"x": 1},
        "last_provider": "fireworks",
        "last_model": "fireworks.aa-model",
    }
    assert keys["ensure"] == ["fireworks"]


def test_select_local_model(client, lemonade, keys, db):
    lemonade.models = _CATALOG
    resp = client.post("/api/providers/select", json={"model": "Qwen3-4B-GGUF"})
    assert resp.status_code == 200
    assert _config()["last_provider"] == "local"
    assert keys["ensure"] == []


def test_select_supersedes_a_failed_restore_notice(client, lemonade, keys, db):
    _write_config({"last_model": "fireworks.gone-model"})
    lemonade.models = _CATALOG
    failed = client.get("/api/providers/active").json()["restore"]
    assert failed["status"] == "failed"

    assert (
        client.post("/api/providers/select", json={"model": "Qwen3-4B-GGUF"})
    ).status_code == 200
    after = client.get("/api/providers/active").json()
    assert after["restore"] == {"status": "none", "message": None}
    assert after["model"] == "Qwen3-4B-GGUF"


def test_a_refused_select_keeps_the_restore_notice(client, lemonade, keys, db):
    _write_config({"last_model": "fireworks.gone-model"})
    lemonade.models = _CATALOG
    client.get("/api/providers/active")
    client.post("/api/providers/select", json={"model": "Llama-3-8B-GGUF"})
    assert client.get("/api/providers/active").json()["restore"]["status"] == "failed"


def test_select_unavailable_model_is_409_and_changes_nothing(
    client, lemonade, keys, db
):
    lemonade.models = _CATALOG
    resp = client.post("/api/providers/select", json={"model": "Llama-3-8B-GGUF"})
    assert resp.status_code == 409
    assert "Llama-3-8B-GGUF" in resp.json()["detail"]
    assert db.get_setting("custom_model") is None
    assert not config_mod.GAIA_CONFIG_FILE.exists()


def test_select_empty_model_is_422(client, lemonade, keys):
    assert client.post("/api/providers/select", json={"model": " "}).status_code == 422


def test_select_with_corrupt_config_is_500(client, lemonade, keys, db):
    config_mod.GAIA_CONFIG_FILE.write_text("{bad", encoding="utf-8")
    lemonade.models = _CATALOG
    resp = client.post("/api/providers/select", json={"model": "Qwen3-4B-GGUF"})
    assert resp.status_code == 500
    assert "not valid JSON" in resp.json()["detail"]
    assert db.get_setting("custom_model") is None


# ── helpers ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model_id,provider,label",
    [
        ("fireworks.glm-5p3-flash", "fireworks", "glm-5p3-flash · Fireworks AI"),
        ("amd.gpt-oss", "amd", "gpt-oss · AMD LLM Gateway"),
        ("Qwen3.5-35B-A3B-GGUF", "local", "Qwen3.5-35B-A3B-GGUF · local"),
    ],
)
def test_provider_of_and_label(model_id, provider, label):
    assert providers.provider_of(model_id) == provider
    assert providers.model_label(model_id) == label


def test_rank_matches_both_cloud_id_forms():
    assert rank("fireworks.accounts/fireworks/models/glm-5p3-flash")[0] == 1
    assert rank("fireworks.glm-5p3-flash")[0] == 1
    assert rank("amd.glm-5p3-flash") is None
    assert rank("glm-5p3-flash") is None


# ── Drift against the TUI ──────────────────────────────────────────────────

_GO_FIELD = re.compile(r'(\w+):\s*"((?:[^"\\]|\\.)*)"')


def _go_recommended_models():
    text = CLOUD_GO.read_text(encoding="utf-8")
    start = text.index("var RecommendedModels = []Recommendation{")
    end = text.index("\n}\n", start)
    body = text[start + len("var RecommendedModels = []Recommendation{") : end]
    entries = []
    for block in re.findall(r"\{([^{}]*)\}", body):
        fields = {k: json.loads(f'"{v}"') for k, v in _GO_FIELD.findall(block)}
        entries.append(
            (fields["ID"], fields.get("Note", ""), fields.get("Evidence", ""))
        )
    return entries


def test_recommended_models_match_the_tui():
    go = _go_recommended_models()
    assert go, f"parsed no RecommendedModels from {CLOUD_GO}"
    py = [(r.id, r.note, r.evidence) for r in RECOMMENDED_MODELS]
    assert py == go, (
        "gaia.llm.recommended_models.RECOMMENDED_MODELS drifted from "
        "RecommendedModels in tui/internal/lemonade/cloud.go; edit both together."
    )


# ── Remote Lemonade and a server still starting ────────────────────────────


def test_list_never_replays_a_stored_key_to_a_remote_lemonade(
    client, lemonade, keys, monkeypatch
):
    lemonade.cloud["fireworks"] = {"name": "fireworks"}
    keys["stored"]["fireworks"] = KEY
    monkeypatch.setattr(providers, "_base_url", lambda: "http://10.0.0.5:13305/api/v1")
    monkeypatch.setattr(providers, "_cloud_entry", lambda p: lemonade.cloud.get(p))
    fw = client.get("/api/providers").json()["providers"][1]
    assert keys["ensure"] == []
    assert "not on this PC" in fw["error"]


def test_restore_waits_for_a_starting_server_instead_of_failing(
    client, lemonade, keys, db
):
    _write_config({"last_model": DEFAULT_MODEL_NAME})
    lemonade.down = True
    first = client.get("/api/providers/active").json()
    assert first["restore"] == {"status": "pending", "message": None}

    lemonade.down = False
    lemonade.models = [{"id": DEFAULT_MODEL_NAME, "downloaded": True, "labels": []}]
    second = client.get("/api/providers/active").json()
    assert second["restore"]["status"] == "restored"
    assert db.get_setting("custom_model") == DEFAULT_MODEL_NAME


def test_restore_of_a_cloud_model_never_goes_to_a_remote_lemonade(
    client, lemonade, keys, db, monkeypatch
):
    _write_config({"last_model": "fireworks.glm-5p3-flash"})
    monkeypatch.setattr(providers, "_base_url", lambda: "http://10.0.0.5:13305/api/v1")
    body = client.get("/api/providers/active").json()
    assert body["restore"]["status"] == "failed"
    assert keys["ensure"] == []


def test_select_while_the_server_is_starting_is_503(client, lemonade, keys, db):
    lemonade.down = True
    resp = client.post("/api/providers/select", json={"model": DEFAULT_MODEL_NAME})
    assert resp.status_code == 503
    assert db.get_setting("custom_model") is None
