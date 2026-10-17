# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Routes behind the Agent UI's model picker and the sidebar's live model status.

Covers the active model a new chat starts on (#4823), the local model list's
state, device, fit and speed (#5209, #5214), the context-size choice (#5213),
and the status popover with its Restart button. Lemonade answers from recorded
``/health``, ``/system-info`` and ``/models`` bodies.
"""

import json
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia import config as config_mod
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, LARGE_DEFAULT_MODEL_NAME
from gaia.ui import warmup
from gaia.ui.database import ChatDatabase
from gaia.ui.routers import providers

BASE = "http://127.0.0.1:13305/api/v1"

HALO_INFO = {
    "Physical Memory": "128.00 GB",
    "devices": {
        "amd_igpu": {
            "name": "AMD Radeon 8060S",
            "available": True,
            "vram_gb": 96,
            "virtual_mem_gb": 16,
        }
    },
    "recipes": {
        "llamacpp": {
            "default_backend": "vulkan",
            "backends": {"vulkan": {"state": "installed"}},
        },
        "flm": {"backends": {"npu": {"state": "unsupported", "message": "No NPU"}}},
    },
}


def _entry(name, ctx=131072, device="gpu", status="ready"):
    return {
        "model_name": name,
        "recipe": "llamacpp",
        "device": device,
        "type": "llm",
        "status": status,
        "recipe_options": {"ctx_size": ctx, "llamacpp_backend": "vulkan"},
    }


class Lemonade:
    """Answers the three reads the routes make; ``down`` refuses every call."""

    def __init__(self):
        self.down = False
        self.owner = "gaia"
        self.loaded = []
        self.models = [
            {
                "id": DEFAULT_MODEL_NAME,
                "recipe": "llamacpp",
                "downloaded": True,
                "size": 5.56,
                "labels": ["chat"],
            },
            {
                "id": LARGE_DEFAULT_MODEL_NAME,
                "recipe": "llamacpp",
                "downloaded": False,
                "size": 21.7,
                "labels": ["chat"],
            },
            {
                "id": "gemma4-it-e2b-FLM",
                "recipe": "flm",
                "downloaded": True,
                "size": 3.0,
                "labels": ["chat", "audio"],
            },
        ]

    def __call__(self, method, path, payload=None):
        if self.down:
            raise providers.ProviderError(
                "The local model server is not reachable.", 503
            )
        if path == "health":
            return 200, {"all_models_loaded": self.loaded}
        if path == "system-info":
            return 200, HALO_INFO
        if path == "models?show_all=true":
            return 200, {"data": self.models}
        raise AssertionError(f"unexpected Lemonade call {method} {path}")


@pytest.fixture
def lemonade(monkeypatch):
    fake = Lemonade()
    monkeypatch.setattr(providers, "_lemonade", fake)
    monkeypatch.setattr(providers, "_base_url", lambda: BASE)
    monkeypatch.setattr(providers, "_server_owner", lambda base: fake.owner)
    return fake


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


def _write_config(data):
    config_mod.GAIA_CONFIG_FILE.write_text(json.dumps(data), encoding="utf-8")


# ── #4823: the active model is the one a new chat starts on ───────────────


def test_active_names_the_configured_default_not_gemma(client, lemonade):
    _write_config({"default_model": "Qwen3-4B-GGUF"})
    body = client.get("/api/providers/active").json()
    assert body["model"] == "Qwen3-4B-GGUF"
    assert body["is_default"] is True
    assert body["display_name"] == "Qwen3 4B"


@pytest.mark.parametrize(
    "default,expected",
    [
        # `gaia init` on a 64 GB+ Strix Halo records the large default.
        (LARGE_DEFAULT_MODEL_NAME, LARGE_DEFAULT_MODEL_NAME),
        # Smaller PCs leave default_model unset and run Gemma.
        (None, DEFAULT_MODEL_NAME),
    ],
)
def test_new_chat_model_per_hardware_tier(client, lemonade, default, expected):
    _write_config({"default_model": default} if default else {})
    body = client.get("/api/providers/active").json()
    assert body["model"] == expected


def test_active_default_not_downloaded_is_offered_for_download(client, lemonade):
    _write_config({"default_model": LARGE_DEFAULT_MODEL_NAME})
    body = client.get("/api/providers/active").json()
    assert body["model"] == LARGE_DEFAULT_MODEL_NAME
    assert body["downloaded"] is False


def test_active_downloaded_is_unknown_while_the_server_starts(client, lemonade):
    lemonade.down = True
    assert client.get("/api/providers/active").json()["downloaded"] is None


def test_active_with_a_corrupt_config_names_no_model(client, lemonade):
    config_mod.GAIA_CONFIG_FILE.write_text("{nope", encoding="utf-8")
    body = client.get("/api/providers/active").json()
    assert body["model"] is None
    assert "not valid JSON" in body["config_error"]


# ── #5209 / #5214: the local model list ───────────────────────────────────


def test_local_list_reports_state_device_fit_and_speed(client, lemonade, db):
    lemonade.loaded = [_entry(DEFAULT_MODEL_NAME, ctx=65536)]
    session = db.create_session(title="t")
    db.upsert_message(
        session["id"],
        None,
        "assistant",
        "hi",
        inference_stats={"tokens_per_second": 38.0, "model": DEFAULT_MODEL_NAME},
    )
    body = client.get("/api/providers/local/models").json()
    by_id = {m["id"]: m for m in body["models"]}
    gemma = by_id[DEFAULT_MODEL_NAME]
    assert gemma["state"] == "loaded"
    assert gemma["placement"]["label"] == "GPU · Vulkan"
    assert gemma["placement"]["source"] == "reported"
    assert gemma["fits"] is True
    assert gemma["size_gb"] == 5.6
    assert gemma["tokens_per_second"] == 38.0
    assert gemma["display_name"] == "Gemma 4 E4B"
    # The large default is offered even before it is downloaded.
    assert by_id[LARGE_DEFAULT_MODEL_NAME]["state"] == "not_downloaded"
    assert by_id[LARGE_DEFAULT_MODEL_NAME]["available"] is True
    assert body["memory_source"] == "AMD iGPU"


def test_cloud_list_is_unchanged(client, lemonade):
    lemonade.models.append(
        {"id": "fireworks.glm", "recipe": "cloud", "labels": ["chat"]}
    )
    models = client.get("/api/providers/fireworks/models").json()["models"]
    assert [m["id"] for m in models] == ["fireworks.glm"]
    assert "placement" not in models[0]


# ── #5213: context size ───────────────────────────────────────────────────


def test_context_options_and_automatic(client, lemonade):
    body = client.get(
        "/api/providers/local/context", params={"model": LARGE_DEFAULT_MODEL_NAME}
    ).json()
    assert body["options"][-1] == body["max_fit"]
    assert body["chosen"] is None
    assert body["automatic"] == body["max_fit"]


def _join_restart():
    for t in threading.enumerate():
        if t.name == "gaia-model-restart":
            t.join(5)


@pytest.fixture
def jobs(monkeypatch):
    """Record reloads instead of loading; release the lock as the real job does."""
    calls = []

    def job(model, up):
        calls.append((model, up))
        providers._restart_lock.release()  # pylint: disable=protected-access

    monkeypatch.setattr(providers, "_restart_job", job)
    return calls


def test_choosing_a_size_persists_and_reloads_through_gaia(client, lemonade, jobs):
    lemonade.loaded = [_entry(LARGE_DEFAULT_MODEL_NAME, ctx=262144)]
    resp = client.put(
        "/api/providers/local/context",
        json={"model": LARGE_DEFAULT_MODEL_NAME, "ctx_size": 131072},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chosen"] == 131072
    assert body["reload"] == {"started": True, "reason": None}
    _join_restart()
    # The checked load path resolves the saved size; nothing posts /load directly.
    assert jobs == [(LARGE_DEFAULT_MODEL_NAME, True)]
    saved = json.loads(config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8"))
    assert saved["context_sizes"] == {LARGE_DEFAULT_MODEL_NAME: 131072}


def test_a_size_change_never_reloads_mid_reply(client, lemonade, jobs):
    lemonade.loaded = [{**_entry(LARGE_DEFAULT_MODEL_NAME), "is_busy": True}]
    body = client.put(
        "/api/providers/local/context",
        json={"model": LARGE_DEFAULT_MODEL_NAME, "ctx_size": 131072},
    ).json()
    assert body["reload"] == {"started": False, "reason": "busy"}
    assert jobs == []


def test_automatic_clears_the_choice(client, lemonade):
    _write_config({"context_sizes": {LARGE_DEFAULT_MODEL_NAME: 131072}})
    body = client.put(
        "/api/providers/local/context",
        json={"model": LARGE_DEFAULT_MODEL_NAME, "ctx_size": None},
    ).json()
    assert body["chosen"] is None
    assert body["reload"] is None  # not loaded


def test_a_size_above_the_fit_is_refused(client, lemonade):
    resp = client.put(
        "/api/providers/local/context",
        json={"model": LARGE_DEFAULT_MODEL_NAME, "ctx_size": 1048576},
    )
    assert resp.status_code == 422
    assert "not offered" in resp.json()["detail"]


@pytest.mark.parametrize(
    "model,automatic",
    [("Qwen3-30B-A3B-Instruct-2507-GGUF", 65536), ("Llama-3B-FLM", 32768)],
)
def test_an_unsized_model_offers_only_automatic_at_its_flat_window(
    client, lemonade, model, automatic
):
    body = client.get("/api/providers/local/context", params={"model": model}).json()
    assert body["options"] == []
    assert body["automatic"] == automatic
    assert "keeps the automatic size" in body["note"]


def test_cloud_models_have_no_context_choice(client, lemonade):
    resp = client.get("/api/providers/local/context", params={"model": "fireworks.glm"})
    assert resp.status_code == 422


# ── The sidebar popover's live status ─────────────────────────────────────


def test_status_loaded(client, lemonade):
    lemonade.loaded = [_entry(DEFAULT_MODEL_NAME, ctx=65536)]
    body = client.get("/api/providers/local/status").json()
    assert body["state"] == "loaded"
    assert body["display_name"] == "Gemma 4 E4B"
    assert body["placement"]["label"] == "GPU · Vulkan"
    assert body["ctx_size"] == 65536
    assert body["memory"]["total_gb"] == 112.0
    assert body["memory"]["model_gb"] > 5


def test_status_stopped(client, lemonade):
    lemonade.down = True
    body = client.get("/api/providers/local/status").json()
    assert body["state"] == "stopped"
    assert body["error"].startswith(f"The model server isn't answering at {BASE}.")
    assert "not reachable" in body["error_detail"]


def test_status_idle_and_warming(client, lemonade, monkeypatch):
    assert client.get("/api/providers/local/status").json()["state"] == "idle"
    monkeypatch.setattr(warmup, "warm_up_status", lambda: {"state": warmup.WARMING})
    assert client.get("/api/providers/local/status").json()["state"] == "warming"
    lemonade.loaded = [_entry(DEFAULT_MODEL_NAME, status="loading")]
    monkeypatch.setattr(warmup, "warm_up_status", lambda: {"state": warmup.IDLE})
    assert client.get("/api/providers/local/status").json()["state"] == "warming"


def test_status_answering_model_is_loaded_and_busy(client, lemonade):
    """Lemonade reports "in_use" while the loaded model answers another request."""
    entry = {
        **_entry("Qwen3-30B-A3B-Instruct-2507-GGUF", status="in_use"),
        "is_busy": True,
    }
    lemonade.loaded = [entry]
    lemonade.models.append(
        {
            "id": entry["model_name"],
            "recipe": "llamacpp",
            "downloaded": True,
            "size": 16.2,
            "labels": ["chat"],
        }
    )
    body = client.get("/api/providers/local/status").json()
    assert (body["state"], body["busy"]) == ("loaded", True)
    # Not in GAIA's model table: estimated from Lemonade's size.
    assert body["memory"]["model_gb"] == pytest.approx(16.2 * 1.05 + 1.0, abs=0.1)


def test_status_needs_the_ui_header(client, lemonade):
    resp = client.get("/api/providers/local/status", headers={"X-Gaia-UI": ""})
    assert resp.status_code == 403


def test_restart_reloads_the_loaded_model(client, lemonade, jobs):
    lemonade.loaded = [_entry(DEFAULT_MODEL_NAME)]
    resp = client.post("/api/providers/local/restart")
    assert resp.status_code == 202
    assert resp.json()["starting_server"] is False
    _join_restart()
    assert jobs == [(DEFAULT_MODEL_NAME, True)]


def test_restart_starts_a_stopped_server_with_the_active_model(client, lemonade, jobs):
    lemonade.down = True
    resp = client.post("/api/providers/local/restart")
    assert resp.status_code == 202
    _join_restart()
    assert jobs == [(DEFAULT_MODEL_NAME, False)]


def test_restart_refuses_while_the_model_answers(client, lemonade, jobs):
    lemonade.loaded = [{**_entry(DEFAULT_MODEL_NAME), "is_busy": True}]
    resp = client.post("/api/providers/local/restart")
    assert resp.status_code == 409
    assert "once the reply finishes" in resp.json()["detail"]
    assert jobs == []


def test_a_second_restart_while_one_runs_is_409(client, lemonade):
    lemonade.loaded = [_entry(DEFAULT_MODEL_NAME)]
    assert providers._restart_lock.acquire(
        blocking=False
    )  # pylint: disable=protected-access
    try:
        assert client.post("/api/providers/local/restart").status_code == 409
    finally:
        providers._restart_lock.release()  # pylint: disable=protected-access


def test_restart_failure_is_shown_in_status(client, lemonade, monkeypatch):
    from gaia.llm import lemonade_client

    def boom(self, model, auto_download=True, *, force=False):
        raise lemonade_client.LemonadeClientError("out of memory")

    monkeypatch.setattr(lemonade_client.LemonadeClient, "_ensure_model_loaded", boom)
    monkeypatch.setattr(
        lemonade_client.LemonadeClient, "__init__", lambda self, **k: None
    )
    # pylint: disable=protected-access
    assert providers._restart_lock.acquire(blocking=False)
    providers._restart_job(DEFAULT_MODEL_NAME, True)
    assert not providers._restart_lock.locked()
    body = client.get("/api/providers/local/status").json()
    assert "out of memory" in body["restart_error"]


def test_a_cloud_entry_is_never_the_local_model(client, lemonade):
    lemonade.loaded = [{**_entry("fireworks.glm"), "recipe": "cloud"}]
    assert client.get("/api/providers/local/status").json()["state"] == "idle"


def test_corrupt_config_is_reported_not_a_500(client, lemonade):
    lemonade.loaded = [_entry(DEFAULT_MODEL_NAME)]
    config_mod.GAIA_CONFIG_FILE.write_text("{nope", encoding="utf-8")
    status = client.get("/api/providers/local/status")
    assert status.status_code == 200
    assert "not valid JSON" in status.json()["error"]
    models = client.get("/api/providers/local/models")
    assert models.status_code == 500
    assert "not valid JSON" in models.json()["detail"]


def test_active_survives_a_lemonade_error(client, lemonade, monkeypatch):
    def broken(*_a, **_k):
        raise providers.ProviderError("Lemonade answered /models with HTTP 500.", 502)

    monkeypatch.setattr(providers, "_list_models", broken)
    resp = client.get("/api/providers/active")
    assert resp.status_code == 200
    assert resp.json()["downloaded"] is None


# ── #5253: the user's own Lemonade, and figures that add up ───────────────


def test_restart_on_the_users_server_asks_first(client, lemonade, jobs):
    lemonade.owner = "user"
    lemonade.loaded = [_entry(DEFAULT_MODEL_NAME)]
    resp = client.post("/api/providers/local/restart")
    assert resp.status_code == 409
    assert "on your Lemonade" in resp.json()["detail"]
    assert "Other apps using it are interrupted" in resp.json()["detail"]
    assert jobs == []
    resp = client.post("/api/providers/local/restart", json={"confirm": True})
    assert resp.status_code == 202
    _join_restart()
    assert jobs == [(DEFAULT_MODEL_NAME, True)]


@pytest.mark.parametrize("owner", ["user", "remote"])
def test_gaia_never_starts_a_server_it_does_not_run(client, lemonade, jobs, owner):
    lemonade.owner = owner
    lemonade.down = True
    resp = client.post("/api/providers/local/restart", json={"confirm": True})
    assert resp.status_code == 409
    assert "doesn't start" in resp.json()["detail"]
    assert jobs == []
    status = client.get("/api/providers/local/status").json()
    assert status["owner"] == owner
    assert "start it yourself" in status["error"]


def test_a_size_change_never_reloads_the_users_server(client, lemonade, jobs):
    lemonade.owner = "user"
    lemonade.loaded = [_entry(LARGE_DEFAULT_MODEL_NAME)]
    body = client.put(
        "/api/providers/local/context",
        json={"model": LARGE_DEFAULT_MODEL_NAME, "ctx_size": 131072},
    ).json()
    assert body["reload"] == {"started": False, "reason": "not_gaias_server"}
    assert body["chosen"] == 131072
    assert jobs == []


def test_free_memory_never_exceeds_the_total(client, lemonade, monkeypatch):
    from gaia.llm import model_fit

    lemonade.loaded = [_entry(DEFAULT_MODEL_NAME, ctx=65536)]
    # Windows iGPU: the carve-out plus all free RAM, more than the GPU pool.
    monkeypatch.setattr(
        model_fit,
        "free_memory",
        lambda info, os_mem: model_fit.FreeMemory(197.5, 210.0),
    )
    memory = client.get("/api/providers/local/status").json()["memory"]
    assert memory["total_gb"] == 112.0
    assert memory["free_gb"] <= memory["total_gb"]
    # Used plus free adds up to the pool, as setup describes it.
    assert memory["free_gb"] == pytest.approx(
        memory["total_gb"] - memory["model_gb"], abs=0.11
    )


def test_active_says_where_a_new_chat_runs_before_any_chat(client, lemonade):
    body = client.get("/api/providers/active").json()
    assert body["inference_remote"] is False
