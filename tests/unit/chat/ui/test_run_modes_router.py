# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Run modes for the Agent UI (``/api/run-modes``): Local or Cloud.

Lemonade is faked at the HTTP layer by the providers tests' fixtures, so a
switch is checked by what it asked Lemonade and what it saved, not by a stub
that says "ok".
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia import config as config_mod
from gaia.llm import local_only
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
from gaia.llm.recommended_models import RECOMMENDED_MODELS
from gaia.ui.routers import providers, run_modes

from . import test_providers_router as _providers_tests

# The providers tests' Lemonade, credential store and database, reused as they are.
_CATALOG = _providers_tests._CATALOG
_fresh_restore = _providers_tests._fresh_restore
_no_fireworks_env = _providers_tests._no_fireworks_env
db = _providers_tests.db
keys = _providers_tests.keys
lemonade = _providers_tests.lemonade
reach = _providers_tests.reach

QWEN = "Qwen3-4B-GGUF"
GLM = "fireworks.glm-5p3-flash"
DEEPSEEK = RECOMMENDED_MODELS[0].id
#: The same model as this Fireworks key lists it.
DEEPSEEK_LISTED = "fireworks.accounts/fireworks/models/deepseek-v4p1-flash"


@pytest.fixture(autouse=True)
def _empty_config():
    config_mod.GAIA_CONFIG_FILE.unlink(missing_ok=True)
    yield
    config_mod.GAIA_CONFIG_FILE.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _no_warm_up(monkeypatch):
    from gaia.ui import warmup

    asked = []
    monkeypatch.setattr(
        warmup,
        "request_warm_up",
        lambda reason, fresh=False, model=None: asked.append(model),
    )
    return asked


@pytest.fixture
def client(db, lemonade, keys):
    lemonade.models = _CATALOG
    app = FastAPI()
    app.include_router(providers.router)
    app.include_router(run_modes.router)
    app.state.db = db
    return TestClient(app, headers={"X-Gaia-UI": "1"})


def _config():
    return json.loads(config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8"))


def _write_config(data):
    config_mod.GAIA_CONFIG_FILE.write_text(json.dumps(data), encoding="utf-8")


def _mode(view, mode):
    return next(m for m in view["modes"] if m["id"] == mode)


# ── Reading the mode ───────────────────────────────────────────────────────


def test_a_fresh_install_is_local_on_the_default_model(client):
    view = client.get("/api/run-modes").json()
    assert view["active"] == "local"
    local, cloud = _mode(view, "local"), _mode(view, "cloud")
    assert local["active"] and not cloud["active"]
    assert local["chat"]["model"] == DEFAULT_MODEL_NAME
    assert local["chat"]["customized"] is False
    assert cloud["chat"]["model"] == DEEPSEEK
    assert cloud["chat"]["provider"] == "fireworks"
    assert cloud["chat"]["provider_name"] == "Fireworks AI"
    assert cloud["chat"]["customized"] is False


def test_an_old_install_on_a_cloud_model_reads_as_cloud_with_no_migration(client, db):
    # Saved by a GAIA from before run modes: no run_mode, no run_profiles.
    _write_config({"last_provider": "fireworks", "last_model": GLM})
    db.set_setting("custom_model", GLM)
    view = client.get("/api/run-modes").json()
    assert view["active"] == "cloud"
    assert _mode(view, "cloud")["chat"]["model"] == GLM
    assert _mode(view, "cloud")["chat"]["customized"] is True
    # Reading changes nothing on disk.
    assert _config() == {"last_provider": "fireworks", "last_model": GLM}


def test_a_saved_default_model_stays_locals_default(client):
    _write_config({"default_model": QWEN})
    local = _mode(client.get("/api/run-modes").json(), "local")
    assert local["chat"]["model"] == QWEN
    assert local["chat"]["customized"] is False


def test_the_running_model_wins_over_a_stale_saved_mode(client, db):
    # The TUI switched to a local model after the Agent UI saved "cloud".
    _write_config({"run_mode": "cloud", "last_model": QWEN})
    db.set_setting("custom_model", QWEN)
    assert client.get("/api/run-modes").json()["active"] == "local"


def test_every_other_capability_stays_on_this_pc_in_both_modes(client):
    view = client.get("/api/run-modes").json()
    for mode in ("local", "cloud"):
        rows = _mode(view, mode)["capabilities"]
        assert [r["id"] for r in rows] == [
            "vision",
            "speech_to_text",
            "read_aloud",
            "images",
            "documents",
        ]
        assert {r["place"] for r in rows} == {"local"}
        # Named for a person, not by the id Lemonade uses.
        names = {r["id"]: r["display_name"] for r in rows}
        assert names["speech_to_text"] == "Whisper Large v3 Turbo"
        assert "user." not in names["documents"]
    notes = {r["id"]: r["cloud_note"] for r in _mode(view, "cloud")["capabilities"]}
    assert notes == {
        "vision": "not_tested",
        "speech_to_text": "not_offered",
        "read_aloud": "not_offered",
        "images": "not_offered",
        "documents": "not_tested",
    }
    assert {r["cloud_note"] for r in _mode(view, "local")["capabilities"]} == {None}


def test_read_aloud_is_marked_off_where_the_voice_is_unproven(client, monkeypatch):
    monkeypatch.setattr(config_mod, "_current_platform", lambda: "darwin")
    rows = _mode(client.get("/api/run-modes").json(), "local")["capabilities"]
    voice = next(r for r in rows if r["id"] == "read_aloud")
    assert voice["unavailable"] == "platform" and voice["model"] is None
    monkeypatch.setattr(config_mod, "_current_platform", lambda: "win32")
    rows = _mode(client.get("/api/run-modes").json(), "local")["capabilities"]
    voice = next(r for r in rows if r["id"] == "read_aloud")
    assert voice["unavailable"] is None and voice["model"] == "kokoro-v1"


# ── Switching ──────────────────────────────────────────────────────────────


def test_switching_to_cloud_runs_its_model_and_saves_it(client, db, keys, lemonade):
    _write_config({"full_access": True, "last_model": QWEN})
    db.set_setting("custom_model", QWEN)
    resp = client.post("/api/run-modes/select", json={"mode": "cloud"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] == "cloud"
    # Named as this key lists it, or Lemonade would not find the model.
    assert db.get_setting("custom_model") == DEEPSEEK_LISTED
    # The key was checked against Lemonade before anything was saved.
    assert keys["ensure"] == ["fireworks"]
    assert "models?show_all=true" in lemonade.paths("GET")
    assert _config() == {
        "full_access": True,
        "last_provider": "fireworks",
        "last_model": DEEPSEEK_LISTED,
        "run_mode": "cloud",
        # Local remembers the model it was on, so switching back reopens on it.
        "run_profiles": {"local": {"chat": QWEN}},
    }


def test_switching_back_reopens_on_the_model_each_mode_was_left_on(
    client, db, _no_warm_up
):
    db.set_setting("custom_model", QWEN)
    client.post("/api/run-modes/select", json={"mode": "cloud", "chat": GLM})
    resp = client.post("/api/run-modes/select", json={"mode": "local"})
    assert resp.status_code == 200, resp.text
    assert db.get_setting("custom_model") == QWEN
    assert _no_warm_up == [QWEN]  # the local model starts loading
    assert _config()["run_profiles"] == {
        "local": {"chat": QWEN},
        "cloud": {"chat": GLM},
    }
    again = client.post("/api/run-modes/select", json={"mode": "cloud"}).json()
    assert _mode(again, "cloud")["chat"]["model"] == GLM


def test_cloud_without_a_working_key_is_refused_and_nothing_changes(client, db, keys):
    db.set_setting("custom_model", QWEN)
    keys["ensure_result"] = False
    resp = client.post("/api/run-modes/select", json={"mode": "cloud"})
    assert resp.status_code == 409
    assert "Fireworks AI has no working key" in resp.json()["detail"]
    assert db.get_setting("custom_model") == QWEN
    assert client.get("/api/run-modes").json()["active"] == "local"
    assert not config_mod.GAIA_CONFIG_FILE.exists()


def test_cloud_is_refused_while_local_only_is_on(client, db, monkeypatch):
    db.set_setting("custom_model", QWEN)
    monkeypatch.setattr(providers, "cloud_blocked", lambda: True)
    monkeypatch.setattr(local_only, "cloud_blocked", lambda: True)
    resp = client.post("/api/run-modes/select", json={"mode": "cloud"})
    assert resp.status_code == 409
    assert "Local only" in resp.json()["detail"]
    assert db.get_setting("custom_model") == QWEN


def test_local_is_refused_when_its_model_is_not_downloaded(client, db):
    db.set_setting("custom_model", GLM)
    # DEFAULT_MODEL_NAME is listed but not downloaded in the fake catalog.
    resp = client.post("/api/run-modes/select", json={"mode": "local"})
    assert resp.status_code == 409
    assert "not downloaded" in resp.json()["detail"]
    assert db.get_setting("custom_model") == GLM
    assert client.get("/api/run-modes").json()["active"] == "cloud"


def test_a_cloud_failure_never_falls_back_to_a_local_model(client, db, lemonade):
    import requests

    db.set_setting("custom_model", QWEN)
    lemonade.answers[("GET", "models?show_all=true")] = requests.ConnectionError(
        "network is unreachable"
    )
    resp = client.post("/api/run-modes/select", json={"mode": "cloud"})
    # "Try again", not a verdict on the model: the list could not be read.
    assert resp.status_code == 503
    assert "not reachable" in resp.json()["detail"]
    assert db.get_setting("custom_model") == QWEN
    assert not config_mod.GAIA_CONFIG_FILE.exists()
    # Nothing but the cloud route was asked: no local model was loaded instead.
    assert not [p for p in lemonade.paths("POST") if p in ("load", "chat/completions")]
    lemonade.answers.clear()
    assert client.get("/api/run-modes").json()["active"] == "local"


def test_selecting_the_mode_already_in_use_changes_nothing(client, db, lemonade):
    db.set_setting("custom_model", QWEN)
    before = len(lemonade.calls)
    resp = client.post("/api/run-modes/select", json={"mode": "local"})
    assert resp.status_code == 200
    assert len(lemonade.calls) == before
    assert not config_mod.GAIA_CONFIG_FILE.exists()


def test_quick_pick_sets_the_model_and_switches_in_one_call(client, db):
    db.set_setting("custom_model", QWEN)
    resp = client.post("/api/run-modes/select", json={"mode": "cloud", "chat": GLM})
    assert resp.status_code == 200, resp.text
    cloud = _mode(resp.json(), "cloud")
    assert cloud["active"] and cloud["chat"]["model"] == GLM
    assert cloud["chat"]["customized"] is True


def test_quick_pick_refuses_a_model_from_the_other_place(client, db):
    db.set_setting("custom_model", QWEN)
    resp = client.post("/api/run-modes/select", json={"mode": "cloud", "chat": QWEN})
    assert resp.status_code == 422
    assert "runs on this PC" in resp.json()["detail"]
    resp = client.post("/api/run-modes/select", json={"mode": "local", "chat": GLM})
    assert resp.status_code == 422
    assert "runs in the cloud" in resp.json()["detail"]
    assert db.get_setting("custom_model") == QWEN


def test_an_unknown_mode_is_404(client):
    resp = client.post("/api/run-modes/select", json={"mode": "hybrid"})
    assert resp.status_code == 404
    assert "local, cloud" in resp.json()["detail"]


def test_mutations_need_the_ui_header(db, lemonade, keys):
    app = FastAPI()
    app.include_router(run_modes.router)
    app.state.db = db
    # A client that does not send X-Gaia-UI, as a cross-site page could not.
    bare = TestClient(app, headers={"X-Gaia-UI": "0"})
    assert bare.post("/api/run-modes/select", json={"mode": "cloud"}).status_code == 403
    assert bare.put("/api/run-modes/cloud", json={"chat": GLM}).status_code == 403
    assert bare.post("/api/run-modes/cloud/reset").status_code == 403


# ── Customizing and resetting ──────────────────────────────────────────────


def test_customizing_the_mode_not_in_use_saves_without_switching(client, db):
    db.set_setting("custom_model", QWEN)
    resp = client.put("/api/run-modes/cloud", json={"chat": GLM})
    assert resp.status_code == 200, resp.text
    view = resp.json()
    assert view["active"] == "local"
    assert _mode(view, "cloud")["chat"]["model"] == GLM
    assert db.get_setting("custom_model") == QWEN
    assert _config() == {"run_profiles": {"cloud": {"chat": GLM}}}


def test_customizing_the_mode_in_use_switches_the_model_now(client, db):
    db.set_setting("custom_model", DEEPSEEK)
    resp = client.put("/api/run-modes/cloud", json={"chat": GLM})
    assert resp.status_code == 200, resp.text
    assert db.get_setting("custom_model") == GLM
    assert _config()["last_model"] == GLM
    assert _config()["run_profiles"] == {"cloud": {"chat": GLM}}


def test_customizing_refuses_a_model_that_cannot_run(client, db):
    db.set_setting("custom_model", GLM)
    resp = client.put("/api/run-modes/local", json={"chat": DEFAULT_MODEL_NAME})
    assert resp.status_code == 409
    assert "not downloaded" in resp.json()["detail"]
    assert not config_mod.GAIA_CONFIG_FILE.exists()


def test_customizing_refuses_the_wrong_kind_of_model(client):
    resp = client.put("/api/run-modes/local", json={"chat": GLM})
    assert resp.status_code == 422
    assert "Set it under Cloud instead" in resp.json()["detail"]


def test_reset_puts_the_mode_not_in_use_back_on_its_default(client, db):
    db.set_setting("custom_model", QWEN)
    client.put("/api/run-modes/cloud", json={"chat": GLM})
    resp = client.post("/api/run-modes/cloud/reset")
    assert resp.status_code == 200
    cloud = _mode(resp.json(), "cloud")
    assert cloud["chat"]["model"] == DEEPSEEK and cloud["chat"]["customized"] is False
    assert "run_profiles" not in _config()
    assert db.get_setting("custom_model") == QWEN


def test_reset_of_the_mode_in_use_switches_to_its_default(client, db):
    db.set_setting("custom_model", GLM)
    resp = client.post("/api/run-modes/cloud/reset")
    assert resp.status_code == 200, resp.text
    assert db.get_setting("custom_model") == DEEPSEEK_LISTED
    assert _config()["last_model"] == DEEPSEEK_LISTED
    assert "run_profiles" not in _config()


def test_reset_that_cannot_run_the_default_changes_nothing(client, db):
    # Local's default (Gemma) is not downloaded in the fake catalog.
    db.set_setting("custom_model", QWEN)
    resp = client.post("/api/run-modes/local/reset")
    assert resp.status_code == 409
    assert db.get_setting("custom_model") == QWEN


def test_the_choice_survives_a_restart(client, db, lemonade, keys):
    db.set_setting("custom_model", QWEN)
    client.post("/api/run-modes/select", json={"mode": "cloud", "chat": GLM})
    saved = _config()
    # A new backend with an empty settings table restores from config.json.
    from gaia.ui.database import ChatDatabase

    fresh = ChatDatabase(":memory:")
    try:
        providers.reset_restore_state()
        app = FastAPI()
        app.include_router(providers.router)
        app.include_router(run_modes.router)
        app.state.db = fresh
        restarted = TestClient(app, headers={"X-Gaia-UI": "1"})
        assert restarted.get("/api/providers/active").json()["model"] == GLM
        view = restarted.get("/api/run-modes").json()
        assert view["active"] == "cloud"
        assert _mode(view, "local")["chat"]["model"] == QWEN
        assert _config() == saved
    finally:
        fresh.close()


def test_a_pick_under_settings_model_moves_the_mode_with_it(client, db):
    db.set_setting("custom_model", QWEN)
    client.post("/api/providers/select", json={"model": GLM})
    assert _config()["run_mode"] == "cloud"
    # Local keeps the model it was on, exactly as a switch from the composer does.
    assert _config()["run_profiles"]["local"] == {"chat": QWEN}
    assert client.get("/api/run-modes").json()["active"] == "cloud"
