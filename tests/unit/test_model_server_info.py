# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Model server panel: which Lemonade, where, and which llama.cpp backend.

Users could not tell whether inference ran on Vulkan, ROCm or the CPU, nor
choose. These pin the reading of Lemonade's reports (including older ones that
omit the backend), the choice being limited to what this machine can run, and
the choice reaching the ``/load`` request.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from gaia import config as config_mod
from gaia.llm import lemonade_client
from gaia.llm.lemonade_client import (
    DEFAULT_EMBEDDING_MODEL,
    LemonadeClient,
    LemonadeClientError,
)
from gaia.llm.model_server_info import (
    EMBEDDER_ON_CPU_WITH_ROCM,
    available_llamacpp_backends,
    describe_loaded_model,
    describe_model_server,
    server_owner,
)

LONG_NAME = "user." + "Very-Long-Model-Name-" * 20 + "GGUF"

SYSINFO = {
    "recipes": {
        "llamacpp": {
            "default_backend": "vulkan",
            "backends": {
                "vulkan": {"state": "installed"},
                "rocm": {"state": "installable"},
                "metal": {"state": "unsupported"},
                "cpu": {"state": "installed"},
                "cuda": {"state": "installed"},
            },
        }
    }
}


def _llm(name="Gemma-4-E4B-it-GGUF", **extra):
    entry = {
        "model_name": name,
        "type": "llm",
        "recipe": "llamacpp",
        "device": "gpu",
        "recipe_options": {"ctx_size": 65536},
    }
    entry.update(extra)
    return entry


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", path)
    return path


# ── reading Lemonade's reports ────────────────────────────────────────────


def test_only_backends_this_machine_can_run_are_offered():
    names = [b["name"] for b in available_llamacpp_backends(SYSINFO)]
    # metal is unsupported here; cuda is not a backend /load accepts.
    assert names == ["vulkan", "rocm", "cpu"]


@pytest.mark.parametrize(
    "sysinfo",
    [
        None,
        {},
        {"recipes": None},
        {"recipes": {"llamacpp": None}},
        {"recipes": {"llamacpp": {"backends": "garbage"}}},
        {"recipes": {"llamacpp": {"backends": {"vulkan": "installed"}}}},
    ],
)
def test_older_or_malformed_system_info_offers_nothing_and_reads_unknown(sysinfo):
    info = describe_model_server(
        base_url="http://localhost:13305/api/v1",
        owner="user",
        health={
            "all_models_loaded": [{"model_name": "Old-Model", "recipe": "llamacpp"}]
        },
        system_info=sysinfo,
        preferred_backend=None,
    )
    assert info["available_backends"] == []
    assert info["default_backend"] is None
    assert info["chat_model"]["name"] == "Old-Model"
    assert info["chat_model"]["backend"] is None
    assert info["chat_model"]["ctx_size"] is None


def test_health_with_no_models_and_no_version():
    info = describe_model_server(
        base_url="http://localhost:13305/api/v1",
        owner="gaia",
        health={},
        system_info=SYSINFO,
        preferred_backend="rocm",
    )
    assert info["chat_model"] is None
    assert info["embedding_model"] is None
    assert info["version"] is None
    assert info["port"] == 13305
    assert info["preferred_backend"] == "rocm"


@pytest.mark.parametrize(
    "default_backend,preferred,note",
    [
        ("rocm", None, EMBEDDER_ON_CPU_WITH_ROCM),
        ("vulkan", "rocm", EMBEDDER_ON_CPU_WITH_ROCM),
        ("vulkan", None, None),
        ("vulkan", "cpu", None),
        (None, None, None),
    ],
)
def test_the_panel_says_when_the_embedder_runs_on_the_cpu_for_rocm(
    monkeypatch, default_backend, preferred, note
):
    monkeypatch.setattr(lemonade_client.platform, "system", lambda: "Linux")
    sysinfo = {"recipes": {"llamacpp": {"default_backend": default_backend}}}
    info = describe_model_server(
        base_url="http://localhost:13305/api/v1",
        owner="gaia",
        health={},
        system_info=sysinfo,
        preferred_backend=preferred,
    )
    assert info["embedding_note"] == note


def test_the_panel_has_no_rocm_note_on_macos(monkeypatch):
    monkeypatch.setattr(lemonade_client.platform, "system", lambda: "Darwin")
    info = describe_model_server(
        base_url="http://remote:13305/api/v1",
        owner="remote",
        health={},
        system_info={"recipes": {"llamacpp": {"default_backend": "rocm"}}},
        preferred_backend=None,
    )
    assert info["embedding_note"] is None


def test_backend_named_by_the_load_wins():
    entry = _llm(recipe_options={"ctx_size": 65536, "llamacpp_backend": "rocm"})
    info = describe_loaded_model(entry, SYSINFO)
    assert (info["backend"], info["backend_source"]) == ("rocm", "requested")
    assert info["ctx_size"] == 65536


def test_backend_read_from_the_launched_llama_server_path():
    entry = _llm(
        launch_command=[
            r"C:\Users\x\.cache\lemonade\bin\llamacpp\rocm\llama-server.exe"
        ]
    )
    info = describe_loaded_model(entry, SYSINFO)
    assert (info["backend"], info["backend_source"]) == ("rocm", "launch")


def test_backend_falls_to_lemonades_default_and_says_so():
    info = describe_loaded_model(_llm(), SYSINFO)
    assert (info["backend"], info["backend_source"]) == ("vulkan", "lemonade_default")


def test_cloud_model_is_remote_not_a_local_backend():
    entry = _llm(
        name="amd.DeepSeek-V4.1-Flash",
        recipe="cloud",
        recipe_options={"ctx_size": 4096, "llamacpp_backend": "vulkan"},
    )
    info = describe_loaded_model(entry, SYSINFO)
    assert info["backend"] == "remote"
    assert info["ctx_size"] is None


def test_cloud_model_detected_by_id_when_recipe_missing():
    info = describe_model_server(
        base_url="http://localhost:13305/api/v1",
        owner="user",
        health={"all_models_loaded": [{"model_name": "claude.claude-sonnet-5"}]},
        system_info=SYSINFO,
        preferred_backend=None,
        is_cloud_model=lambda n: n.startswith("claude."),
    )
    assert info["chat_model"]["backend"] == "remote"


def test_npu_model_reports_npu():
    entry = {"model_name": "gemma4-it-e2b-FLM", "recipe": "flm", "device": "npu"}
    assert describe_loaded_model(entry, SYSINFO)["backend"] == "npu"


def test_chat_and_embedder_are_told_apart_and_long_names_survive():
    health = {
        "version": "2026.41.1",
        "model_loaded": DEFAULT_EMBEDDING_MODEL,
        "all_models_loaded": [
            {
                "model_name": DEFAULT_EMBEDDING_MODEL,
                "type": "embedding",
                "recipe": "llamacpp",
                "recipe_options": {"llamacpp_backend": "cpu"},
            },
            _llm(name=LONG_NAME),
        ],
    }
    info = describe_model_server(
        base_url="http://localhost:13305/api/v1",
        owner="gaia",
        health=health,
        system_info=SYSINFO,
        preferred_backend=None,
    )
    assert info["chat_model"]["name"] == LONG_NAME
    assert info["embedding_model"]["backend"] == "cpu"
    assert info["version"] == "2026.41.1"


@pytest.mark.parametrize(
    "url,port,owner",
    [
        ("http://localhost:51234/api/v1", 51234, "gaia"),
        ("http://127.0.0.1:13305/api/v1", 51234, "user"),
        ("http://localhost:13305/api/v1", None, "user"),
        ("https://lemonade.example.com/api/v1", None, "remote"),
    ],
)
def test_server_owner(url, port, owner):
    assert server_owner(url, port) == owner


# ── the saved choice ──────────────────────────────────────────────────────


def test_config_accepts_backends_and_auto_means_unset(config_file):
    cfg = config_mod.GaiaConfig()
    cfg.set("llamacpp_backend", "ROCm")
    assert cfg.llamacpp_backend == "rocm"
    cfg.set("llamacpp_backend", "auto")
    assert cfg.llamacpp_backend is None
    with pytest.raises(config_mod.GaiaConfigError, match="llamacpp_backend"):
        cfg.set("llamacpp_backend", "cuda")


def test_a_bad_backend_on_disk_fails_loudly(config_file):
    config_file.write_text(json.dumps({"llamacpp_backend": "directx"}))
    with pytest.raises(
        config_mod.GaiaConfigError, match="gaia config set llamacpp_backend auto"
    ):
        config_mod.GaiaConfig.load()


def test_save_key_keeps_keys_this_version_does_not_know(config_file):
    config_file.write_text(
        json.dumps({"lemonade": {"mode": "existing"}, "profile": "npu"})
    )
    config_mod.GaiaConfig.save_key("llamacpp_backend", "vulkan")
    data = json.loads(config_file.read_text())
    assert data == {
        "lemonade": {"mode": "existing"},
        "profile": "npu",
        "llamacpp_backend": "vulkan",
    }


# ── the choice reaches /load ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(lemonade_client.time, "sleep", lambda s: None)
    monkeypatch.setattr(lemonade_client, "_PINNED_BACKENDS", set())
    monkeypatch.setattr(lemonade_client, "_CPU_FALLBACK_EMBEDDERS", set())


def _recording_client(calls, recipe="llamacpp", fail_load=None):
    client = LemonadeClient(verbose=False)

    def fake_send(method, url, data=None, **kwargs):
        endpoint = url.split("?")[0].rsplit("/", 1)[-1]
        calls.append((endpoint, dict(data or {})))
        if endpoint == "models":
            return {"data": [{"id": "Gemma-4-E4B-it-GGUF", "recipe": recipe}]}
        if endpoint == "system-info":
            return SYSINFO
        if endpoint == "load" and fail_load:
            raise LemonadeClientError(fail_load)
        return {"status": "success"}

    client._send_request = fake_send
    return client


def _loads(calls):
    return [data for endpoint, data in calls if endpoint == "load"]


def test_chosen_backend_is_sent_on_the_next_chat_load(config_file):
    config_mod.GaiaConfig.save_key("llamacpp_backend", "rocm")
    calls = []
    _recording_client(calls).load_model("Gemma-4-E4B-it-GGUF", prompt=False)
    assert _loads(calls) == [
        {"model_name": "Gemma-4-E4B-it-GGUF", "llamacpp_backend": "rocm"}
    ]


def test_auto_sends_no_backend(config_file):
    calls = []
    _recording_client(calls).load_model("Gemma-4-E4B-it-GGUF", prompt=False)
    assert _loads(calls) == [{"model_name": "Gemma-4-E4B-it-GGUF"}]


def test_a_non_llamacpp_model_never_gets_a_llamacpp_backend(config_file):
    config_mod.GaiaConfig.save_key("llamacpp_backend", "cpu")
    calls = []
    _recording_client(calls, recipe="flm").load_model(
        "Gemma-4-E4B-it-GGUF", prompt=False
    )
    assert "llamacpp_backend" not in _loads(calls)[0]


def test_the_embedder_keeps_its_own_backend(config_file, monkeypatch):
    monkeypatch.setattr(lemonade_client.platform, "system", lambda: "Windows")
    config_mod.GaiaConfig.save_key("llamacpp_backend", "cpu")
    calls = []
    _recording_client(calls).load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)
    assert _loads(calls)[0]["llamacpp_backend"] == "vulkan"


def test_a_saved_rocm_choice_puts_the_embedder_on_the_cpu(config_file, monkeypatch):
    monkeypatch.setattr(lemonade_client.platform, "system", lambda: "Linux")
    config_mod.GaiaConfig.save_key("llamacpp_backend", "rocm")
    calls = []
    _recording_client(calls).load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)
    assert _loads(calls)[0]["llamacpp_backend"] == "cpu"


def test_a_failed_load_on_the_chosen_backend_says_how_to_undo_it(config_file):
    config_mod.GaiaConfig.save_key("llamacpp_backend", "rocm")
    client = _recording_client([], fail_load="backend rocm is not installed")
    with pytest.raises(LemonadeClientError) as err:
        client.load_model("Gemma-4-E4B-it-GGUF", prompt=False, load_retries=0)
    assert "gaia config set llamacpp_backend auto" in str(err.value)
    assert "rocm" in str(err.value)


# ── the Agent UI endpoints ────────────────────────────────────────────────


class _FakeLemonade:
    """Stands in for LemonadeClient in the router: canned /health, /system-info."""

    def __init__(self, health=None, sysinfo=None, down=False, sysinfo_down=False):
        self.base_url = "http://localhost:13305/api/v1"
        self._health, self._sysinfo = health or {}, sysinfo
        self._down, self._sysinfo_down = down, sysinfo_down

    def health_check(self, timeout=None):
        if self._down:
            raise LemonadeClientError(
                "Request failed: HTTPConnectionPool(host='localhost', port=13305):\n"
                "Max retries exceeded"
            )
        return self._health

    def get_system_info(self, timeout=None):
        if self._sysinfo_down:
            raise LemonadeClientError("Request failed: 404")
        return self._sysinfo


@pytest.fixture
def ui(monkeypatch, config_file):
    from gaia.ui.server import create_app

    def _make(**kw):
        fake = _FakeLemonade(**kw)
        monkeypatch.setattr(lemonade_client, "LemonadeClient", lambda **_: fake)
        monkeypatch.setattr(
            lemonade_client, "_read_embedded_lemonade_state", lambda: None
        )
        return TestClient(create_app(db_path=":memory:"))

    return _make


@pytest.mark.allow_network
def test_unreachable_lemonade_is_one_plain_line(ui):
    body = ui(down=True).get("/api/system/model-server").json()
    assert body["reachable"] is False
    assert (
        body["error"] == "Lemonade is not answering at http://localhost:13305/api/v1."
    )
    assert "\n" not in body["error"]


@pytest.mark.allow_network
def test_the_panel_payload_carries_the_rocm_embedder_note(ui, monkeypatch):
    monkeypatch.setattr(lemonade_client.platform, "system", lambda: "Windows")
    sysinfo = {"recipes": {"llamacpp": {"default_backend": "rocm"}}}
    body = ui(sysinfo=sysinfo).get("/api/system/model-server").json()
    assert body["embedding_note"] == EMBEDDER_ON_CPU_WITH_ROCM
    body = ui(sysinfo=SYSINFO).get("/api/system/model-server").json()
    assert body["embedding_note"] is None


@pytest.mark.allow_network
def test_panel_survives_a_failed_system_info(ui):
    body = (
        ui(health={"all_models_loaded": [_llm()]}, sysinfo_down=True)
        .get("/api/system/model-server")
        .json()
    )
    assert body["reachable"] is True
    assert body["chat_model"]["name"] == "Gemma-4-E4B-it-GGUF"
    assert body["chat_model"]["backend"] is None
    assert body["available_backends"] == []
    assert "system info" in body["system_info_error"]


@pytest.mark.allow_network
def test_choosing_an_unavailable_backend_is_refused_and_not_saved(ui, config_file):
    client = ui(health={}, sysinfo=SYSINFO)
    resp = client.put("/api/system/model-server/backend", json={"backend": "metal"})
    assert resp.status_code == 400
    assert "not available on this machine" in resp.json()["detail"]
    assert not config_file.exists()


@pytest.mark.allow_network
def test_choosing_without_system_info_is_refused(ui, config_file):
    client = ui(health={}, sysinfo_down=True)
    resp = client.put("/api/system/model-server/backend", json={"backend": "rocm"})
    assert resp.status_code == 400
    assert not config_file.exists()


@pytest.mark.allow_network
def test_choosing_while_lemonade_is_down_is_refused(ui, config_file):
    resp = ui(down=True).put(
        "/api/system/model-server/backend", json={"backend": "vulkan"}
    )
    assert resp.status_code == 503
    assert not config_file.exists()


@pytest.mark.allow_network
def test_choosing_an_available_backend_persists(ui, config_file):
    client = ui(health={}, sysinfo=SYSINFO)
    resp = client.put("/api/system/model-server/backend", json={"backend": "rocm"})
    assert resp.status_code == 200
    assert resp.json()["preferred_backend"] == "rocm"
    assert json.loads(config_file.read_text())["llamacpp_backend"] == "rocm"

    resp = client.put("/api/system/model-server/backend", json={"backend": "auto"})
    assert resp.json()["preferred_backend"] is None
    assert json.loads(config_file.read_text())["llamacpp_backend"] is None


@pytest.mark.allow_network
def test_ui_load_model_sends_the_chosen_backend(monkeypatch, config_file):
    from gaia.ui.routers import system as system_mod
    from gaia.ui.server import create_app

    config_mod.GaiaConfig.save_key("llamacpp_backend", "rocm")
    sent = []

    def fake_post(path, payload, **_kw):
        sent.append((path, payload))
        return asyncio.sleep(0)

    monkeypatch.setattr(system_mod, "_lemonade_post", fake_post)
    monkeypatch.setattr(
        lemonade_client.LemonadeClient,
        "_model_recipe",
        lambda self, name: "llamacpp",
    )
    resp = TestClient(create_app(db_path=":memory:")).post(
        "/api/system/load-model",
        json={"model_name": "Gemma-4-E4B-it-GGUF", "ctx_size": 65536},
    )
    assert resp.status_code == 202
    assert sent == [
        (
            "load",
            {
                "model_name": "Gemma-4-E4B-it-GGUF",
                "ctx_size": 65536,
                "llamacpp_backend": "rocm",
            },
        )
    ]
