# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A Lemonade the user runs is used, never reconfigured.

Every test drives real GAIA code against a fake Lemonade on an ephemeral
loopback port that records each request. In ``lemonade_mode: external`` none of
setup, chat start, model load or a settings save may send it a setup change:
no provider registration, no keys, no backend installs, no saved load options,
no unloads or deletes. Each refusal test has a control in which the same server
is chosen through ``LEMONADE_BASE_URL`` instead, so the write is seen to go
through and the recorder is known to catch it.

Also here: the status panel picking the chat model when a speech model loaded
last, and "Use GAIA's own" never claiming a switch to a server that isn't
installed.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from unittest import mock

import pytest

from gaia.config import GaiaConfig
from gaia.llm import lemonade_choice as choice

pytestmark = pytest.mark.allow_network

#: Routes that change a server's setup. None may reach the user's server.
SETUP_ROUTES = ("install", "uninstall", "delete", "unload", "cloud/auth")

CHAT = "Gemma-4-E4B-it-GGUF"
EMBEDDER = "user.embeddinggemma-300m-GGUF"


class FakeLemonade:
    """A Lemonade stand-in that answers reads and records every request."""

    def __init__(self) -> None:
        self.requests: List[Tuple[str, str, Optional[Dict[str, Any]]]] = []
        self.providers: List[Dict[str, Any]] = []
        self.loaded: List[Dict[str, Any]] = []
        self.root_model: Optional[str] = None
        self.catalog: List[Dict[str, Any]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _reply(self, body: Any, status: int = 200) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record(self) -> Optional[Dict[str, Any]]:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                path = self.path.split("/api/v1/", 1)[-1]
                fake.requests.append((self.command, path, body))
                return body

            def do_GET(self):
                self._record()
                path = self.path.split("/api/v1/", 1)[-1].split("?")[0]
                if path == "health":
                    return self._reply(
                        {
                            "status": "ok",
                            "version": "2026.40.0",
                            "model_loaded": fake.root_model,
                            "all_models_loaded": fake.loaded,
                        }
                    )
                if path == "system-info":
                    return self._reply(
                        {
                            "cloud": {"providers": fake.providers},
                            "recipes": {
                                "llamacpp": {
                                    "default_backend": "vulkan",
                                    "backends": {
                                        "vulkan": {"state": "installed"},
                                        "cpu": {"state": "installed"},
                                    },
                                }
                            },
                        }
                    )
                if path == "models":
                    return self._reply({"data": fake.catalog})
                return self._reply({}, 404)

            def do_POST(self):
                self._record()
                self._reply(
                    {"status": "success", "models_discovered": 3, "auth_state": {}}
                )

            def do_DELETE(self):
                self._record()
                self._reply({"status": "success"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/api/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def setup_writes(self) -> List[Tuple[str, str, Optional[Dict[str, Any]]]]:
        """Every request that changed, or tried to change, the server's setup."""
        return [
            (method, path, body)
            for method, path, body in self.requests
            if method != "GET"
            and (
                path.split("?")[0] in SETUP_ROUTES
                or path.startswith("cloud/auth")
                or method == "DELETE"
                or (path == "load" and (body or {}).get("save_options"))
            )
        ]

    def posts(self, route: str) -> List[Optional[Dict[str, Any]]]:
        return [b for m, p, b in self.requests if m == "POST" and p == route]


@pytest.fixture
def server():
    fake = FakeLemonade()
    yield fake
    fake.close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_LEMONADE_EMBEDDED"):
        monkeypatch.delenv(name, raising=False)
    for name in ("LEMONADE_AMD_API_KEY", "LEMONADE_FIREWORKS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    # The AMD gateway's network check must not reach the real network.
    from gaia.ui.routers import providers

    monkeypatch.setattr(
        providers, "check_gateway_reachable", lambda base_url=None: None
    )


@pytest.fixture
def keyring(monkeypatch):
    store: Dict[str, str] = {}
    from gaia.connectors import store as store_mod

    monkeypatch.setattr(store_mod, "save_secret", store.__setitem__)
    monkeypatch.setattr(store_mod, "peek_secret", store.get)
    monkeypatch.setattr(store_mod, "delete_secret", lambda n: store.pop(n, None))
    return store


def _save(**fields):
    cfg = GaiaConfig.load()
    for key, value in fields.items():
        setattr(cfg, key, value)
    cfg.save()


@pytest.fixture
def users_server(server):
    """The fake is the user's own server, chosen with "Use mine"."""
    _save(lemonade_mode="external", lemonade_base_url=server.url)
    return server


@pytest.fixture
def env_server(server, monkeypatch):
    """Control: the same fake chosen by LEMONADE_BASE_URL, which GAIA may set up."""
    monkeypatch.setenv("LEMONADE_BASE_URL", server.url)
    return server


def _provider(name, *, key=True, models=3):
    return {
        "name": name,
        "base_url": "https://gateway.example/v1",
        "runtime_key_set": key,
        "env_var_set": False,
        "models_discovered": models,
    }


# ---------------------------------------------------------------------------
# The guard itself
# ---------------------------------------------------------------------------


class TestGuard:
    @pytest.mark.parametrize("route", SETUP_ROUTES + ("cloud/auth/amd",))
    def test_every_setup_route_is_refused(self, users_server, route):
        assert choice.setup_change_refusal(f"{users_server.url}/{route}", "POST")

    def test_loopback_spellings_are_the_same_server(self, users_server):
        url = f"http://localhost:{users_server.port}/api/v1/install"
        assert choice.setup_change_refusal(url, "POST")

    def test_use_is_allowed(self, users_server):
        for route in ("load", "pull", "chat/completions", "embeddings"):
            assert (
                choice.setup_change_refusal(
                    f"{users_server.url}/{route}", "POST", {"model_name": "m"}
                )
                is None
            )
        assert choice.setup_change_refusal(f"{users_server.url}/health", "GET") is None

    def test_saving_load_options_is_refused(self, users_server):
        url = f"{users_server.url}/load"
        assert choice.setup_change_refusal(url, "POST", {"save_options": True})

    def test_another_port_is_not_the_users_server(self, users_server):
        url = f"http://127.0.0.1:{users_server.port + 1}/api/v1/install"
        assert choice.setup_change_refusal(url, "POST") is None

    def test_env_chosen_server_is_not_guarded(self, env_server):
        assert choice.setup_change_refusal(f"{env_server.url}/install", "POST") is None


# ---------------------------------------------------------------------------
# First-run setup: Connect a cloud provider (Agent UI)
# ---------------------------------------------------------------------------


class TestSetupConnect:
    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from gaia.ui.dependencies import get_db
        from gaia.ui.routers import providers as providers_mod

        app = FastAPI()
        app.include_router(providers_mod.router)
        db = mock.Mock()
        db.get_setting.return_value = None
        app.dependency_overrides[get_db] = lambda: db
        return TestClient(app)

    def test_connect_uses_the_users_working_provider_and_writes_nothing(
        self, client, users_server, keyring
    ):
        users_server.providers = [_provider("amd")]
        r = client.post("/api/providers/amd/connect", json={"api_key": "pasted"})
        assert r.status_code == 200, r.text
        assert r.json()["models_discovered"] is True
        assert users_server.setup_writes() == []
        assert (
            keyring == {}
        ), "a pasted key must not be kept for a server it never reached"

    def test_connect_without_the_provider_refuses_and_writes_nothing(
        self, client, users_server, keyring
    ):
        r = client.post("/api/providers/amd/connect", json={"api_key": "pasted"})
        assert r.status_code == 409
        assert "does not change your server's setup" in r.json()["detail"]
        assert "gaia config set lemonade_mode gaia" in r.json()["detail"]
        assert users_server.setup_writes() == []

    def test_connect_with_a_keyless_provider_refuses_and_sends_no_key(
        self, client, users_server, keyring
    ):
        users_server.providers = [_provider("fireworks", key=False, models=0)]
        r = client.post("/api/providers/fireworks/connect", json={"api_key": "k"})
        assert r.status_code == 409
        assert "LEMONADE_FIREWORKS_API_KEY" in r.json()["detail"]
        assert users_server.setup_writes() == []

    def test_listing_providers_hands_the_users_server_no_stored_key(
        self, client, users_server, keyring
    ):
        from gaia.llm.cloud_keys import remember_key

        remember_key("fireworks", "kept")
        users_server.providers = [_provider("fireworks", key=False, models=0)]
        r = client.get("/api/providers")
        assert r.status_code == 200
        assert users_server.setup_writes() == []

    def test_forgetting_a_key_leaves_the_users_server_alone(
        self, client, users_server, keyring
    ):
        from gaia.llm.cloud_keys import remember_key

        remember_key("fireworks", "kept")
        r = client.delete("/api/providers/fireworks/key")
        assert r.status_code == 200 and r.json()["removed"] is True
        assert users_server.setup_writes() == []

    def test_control_env_chosen_server_is_set_up(self, client, env_server, keyring):
        env_server.providers = [_provider("amd")]
        r = client.post("/api/providers/amd/connect", json={"api_key": "pasted"})
        assert r.status_code == 200, r.text
        routes = [path for _, path, _ in env_server.setup_writes()]
        assert routes == ["install", "cloud/auth"]


# ---------------------------------------------------------------------------
# Chat start
# ---------------------------------------------------------------------------


class TestChatStart:
    @pytest.mark.parametrize("provider", ["fireworks", "amd"])
    def test_stored_key_is_never_replayed_into_the_users_server(
        self, users_server, keyring, provider
    ):
        from gaia.llm.cloud_keys import (
            CloudKeyError,
            ensure_authenticated,
            remember_key,
        )

        remember_key(provider, "kept")
        users_server.providers = [_provider(provider, key=False, models=0)]
        with pytest.raises(CloudKeyError, match="does not change your server's setup"):
            ensure_authenticated(provider)
        assert users_server.setup_writes() == []

    def test_a_working_provider_on_the_users_server_is_used_as_is(
        self, users_server, keyring
    ):
        from gaia.llm.cloud_keys import ensure_authenticated

        users_server.providers = [_provider("amd")]
        assert ensure_authenticated("amd") is True
        assert users_server.setup_writes() == []

    def test_gateway_manager_refuses_instead_of_handing_a_token(
        self, users_server, keyring
    ):
        from gaia.llm.gateway import GatewayError, GatewayManager, remember_token

        remember_token("kept")
        users_server.providers = [_provider("amd", key=False, models=0)]
        with pytest.raises(GatewayError, match="does not change your server's setup"):
            GatewayManager().ensure_authenticated()
        assert users_server.setup_writes() == []

    def test_control_stored_key_is_replayed_into_an_env_chosen_server(
        self, env_server, keyring
    ):
        from gaia.llm.cloud_keys import ensure_authenticated, remember_key

        remember_key("fireworks", "kept")
        env_server.providers = [_provider("fireworks", key=False, models=0)]
        assert ensure_authenticated("fireworks") is True
        assert [p for _, p, _ in env_server.setup_writes()] == ["cloud/auth"]

    def _preflight(self, monkeypatch):
        from contextlib import nullcontext

        from gaia.daemon import broker_client
        from gaia.llm import lemonade_client
        from gaia.llm.lemonade_manager import LemonadeManager
        from gaia.ui import _chat_helpers

        monkeypatch.setattr(broker_client, "model_lease", lambda *a, **k: nullcontext())
        monkeypatch.setattr(
            LemonadeManager, "get_base_url", classmethod(lambda c: None)
        )
        monkeypatch.setattr(
            lemonade_client.LemonadeClient, "_check_free_memory", lambda *a: None
        )
        monkeypatch.setattr(
            lemonade_client.LemonadeClient, "_model_recipe", lambda *a: "llamacpp"
        )
        monkeypatch.setattr(_chat_helpers, "_eval_provider_kwargs", lambda: None)
        return _chat_helpers._maybe_load_expected_model

    def test_preflight_loads_without_unloading_the_users_models(
        self, users_server, monkeypatch
    ):
        users_server.loaded = [
            {"model_name": "Users-Own-Model", "type": "llm", "recipe": "llamacpp"}
        ]
        self._preflight(monkeypatch)(CHAT)
        assert [b["model_name"] for b in users_server.posts("load")] == [CHAT]
        assert users_server.setup_writes() == []

    def test_preflight_leaves_another_apps_model_on_gaias_own_server(
        self, env_server, monkeypatch
    ):
        env_server.loaded = [
            {"model_name": "Other-Apps-Model", "type": "llm", "recipe": "llamacpp"}
        ]
        self._preflight(monkeypatch)(CHAT)
        assert [b["model_name"] for b in env_server.posts("load")] == [CHAT]
        assert not [w for w in env_server.setup_writes() if w[1] == "unload"]


# ---------------------------------------------------------------------------
# Model load
# ---------------------------------------------------------------------------


class TestModelLoad:
    @pytest.fixture
    def client(self, monkeypatch):
        from gaia.llm import lemonade_client

        monkeypatch.setattr(lemonade_client.platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            lemonade_client.LemonadeClient, "_check_free_memory", lambda *a: None
        )
        return lemonade_client.LemonadeClient(verbose=False)

    def test_embedder_load_saves_no_options_on_the_users_server(
        self, users_server, client
    ):
        from gaia.llm.lemonade_client import EMBEDDER_MODELS

        embedder = sorted(EMBEDDER_MODELS)[0]
        client.load_model(embedder, auto_download=False, prompt=False)
        loads = users_server.posts("load")
        assert len(loads) == 1 and "save_options" not in loads[0]
        assert users_server.setup_writes() == []

    def test_embedder_is_reloaded_with_gaias_options_whenever_it_is_gone(
        self, users_server, client
    ):
        """Nothing is saved there, so Lemonade's own reload would drop them."""
        from gaia.llm.lemonade_client import EMBEDDER_MODELS

        embedder = sorted(EMBEDDER_MODELS)[0]
        client._pin_llamacpp_backend(embedder)
        client._pin_llamacpp_backend(embedder)
        assert len(users_server.posts("load")) == 2
        users_server.loaded = [{"model_name": embedder, "type": "embedding"}]
        client._pin_llamacpp_backend(embedder)
        assert len(users_server.posts("load")) == 2
        assert users_server.setup_writes() == []

    def test_control_embedder_load_saves_options_on_an_env_chosen_server(
        self, env_server, client
    ):
        from gaia.llm.lemonade_client import EMBEDDER_MODELS

        client.load_model(sorted(EMBEDDER_MODELS)[0], auto_download=False, prompt=False)
        assert env_server.posts("load")[0].get("save_options") is True

    @pytest.mark.parametrize(
        "call",
        [
            lambda c: c.unload_model("Users-Own-Model"),
            lambda c: c.unload_model(),
            lambda c: c.delete_model("Users-Own-Model"),
            lambda c: c.install_backend("llamacpp:rocm"),
            lambda c: c.uninstall_backend("llamacpp:vulkan"),
            lambda c: c.load_model(
                CHAT, auto_download=False, prompt=False, save_options=True
            ),
        ],
        ids=["unload", "unload-all", "delete", "install", "uninstall", "save-opts"],
    )
    def test_setup_changes_are_refused_before_anything_is_sent(
        self, users_server, client, call
    ):
        from gaia.llm.lemonade_client import LemonadeClientError

        with pytest.raises(LemonadeClientError, match="does not change"):
            call(client)
        assert users_server.setup_writes() == []

    def test_a_broken_model_is_left_for_the_user_to_repair(
        self, users_server, client, monkeypatch
    ):
        """Resume failed: the advice is to fix it on their server, never delete."""
        from gaia.llm import lemonade_client as lc

        def broken_load(self, *a, **k):
            raise lc.LemonadeClientError(
                "download validation failed: files are missing"
            )

        monkeypatch.setattr(
            lc.LemonadeClient, "_post_load_with_transient_retry", broken_load
        )
        monkeypatch.setattr(
            lc.LemonadeClient,
            "_consume_pull_stream",
            lambda *a: (_ for _ in ()).throw(lc.LemonadeClientError("pull stalled")),
        )
        with pytest.raises(lc.UsersServerUnchangedError) as err:
            client.load_model(CHAT, auto_download=True, prompt=False)
        message = str(err.value)
        assert "Repair it there" in message
        assert "gaia config set lemonade_mode gaia" in message
        assert "delete" not in message.lower()
        assert "Force-redownload" not in message
        assert users_server.setup_writes() == []

    def test_gateway_registration_is_refused(self, users_server):
        from gaia.llm.gateway import GatewayError, GatewayManager

        with pytest.raises(GatewayError, match="register a cloud provider"):
            GatewayManager().install("https://gateway.example/v1", api_key="k")
        with pytest.raises(GatewayError):
            GatewayManager().uninstall()
        assert users_server.setup_writes() == []

    def test_rag_embedder_reload_does_not_unload_on_the_users_server(
        self, users_server, monkeypatch
    ):
        from contextlib import nullcontext

        from gaia.daemon import broker_client
        from gaia.llm.lemonade_client import LemonadeClient

        monkeypatch.setattr(broker_client, "model_lease", lambda *a, **k: nullcontext())
        llm = mock.Mock(spec=LemonadeClient)
        llm.is_users_server.return_value = True
        self._load_rag_embedder(llm)
        llm.unload_model.assert_not_called()
        llm.load_model.assert_called_once()

    def test_control_rag_embedder_reload_unloads_on_gaias_server(self, monkeypatch):
        from contextlib import nullcontext

        from gaia.daemon import broker_client
        from gaia.llm.lemonade_client import LemonadeClient

        monkeypatch.setattr(broker_client, "model_lease", lambda *a, **k: nullcontext())
        llm = mock.Mock(spec=LemonadeClient)
        llm.is_users_server.return_value = False
        self._load_rag_embedder(llm)
        llm.unload_model.assert_called_once()

    @staticmethod
    def _load_rag_embedder(llm):
        from gaia.rag.sdk import RAGSDK

        rag = RAGSDK.__new__(RAGSDK)
        rag.config = mock.Mock(embedding_model=EMBEDDER)
        rag.llm_client = llm
        rag.embedder = None
        rag.use_lemonade_embeddings = False
        rag.log = mock.Mock()
        rag._load_embedder()


# ---------------------------------------------------------------------------
# Settings save
# ---------------------------------------------------------------------------


class TestSettingsSave:
    def test_backend_choice_is_saved_in_gaia_only(self, users_server):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from gaia.ui.routers import system as system_mod

        app = FastAPI()
        app.include_router(system_mod.router)
        r = TestClient(app).put(
            "/api/system/model-server/backend", json={"backend": "cpu"}
        )
        assert r.status_code == 200, r.text
        assert GaiaConfig.load().llamacpp_backend == "cpu"
        assert [m for m, _, _ in users_server.requests if m != "GET"] == []


# ---------------------------------------------------------------------------
# Bug: a speech model read as the chat model
# ---------------------------------------------------------------------------


class TestStatusPicksTheChatModel:
    @pytest.fixture
    def status(self, users_server):
        from fastapi.testclient import TestClient

        from gaia.ui.server import create_app

        def _get():
            body = TestClient(create_app(db_path=":memory:")).get("/api/system/status")
            assert body.status_code == 200, body.text
            return body.json()

        return _get

    @staticmethod
    def _tts(ctx=4096):
        return {
            "model_name": "kokoro-v1",
            "type": "tts",
            "recipe": "kokoro",
            "recipe_options": {"ctx_size": ctx},
        }

    def test_tts_loaded_last_beside_a_cloud_chat_model(self, users_server, status):
        users_server.root_model = "kokoro-v1"
        users_server.loaded = [
            {
                "model_name": "amd.DeepSeek-V4.1-Flash",
                "type": "llm",
                "recipe": "cloud",
                "recipe_options": {"ctx_size": 4096},
            },
            {
                "model_name": "embeddinggemma-300m-GGUF",
                "type": "embedding",
                "recipe": "llamacpp",
                "recipe_options": {"ctx_size": 2048},
            },
            self._tts(),
        ]
        users_server.catalog = [
            {"id": "kokoro-v1", "recipe_options": {"ctx_size": 4096}},
            {"id": "amd.DeepSeek-V4.1-Flash", "recipe_options": {"ctx_size": 4096}},
        ]
        body = status()
        assert body["model_loaded"] == "amd.DeepSeek-V4.1-Flash"
        assert body["model_context_size"] is None
        assert body["context_size_sufficient"] is not False

    def test_tts_loaded_last_beside_a_local_chat_model(self, users_server, status):
        users_server.root_model = "kokoro-v1"
        users_server.loaded = [
            {
                "model_name": CHAT,
                "type": "llm",
                "recipe": "llamacpp",
                "recipe_options": {"ctx_size": 65536},
            },
            self._tts(),
        ]
        body = status()
        assert body["model_loaded"] == CHAT
        assert body["model_context_size"] == 65536
        assert body["context_size_sufficient"] is True

    @pytest.mark.parametrize("kind", ["tts", "audio", "transcription", "asr", "image"])
    def test_only_non_chat_models_loaded_means_no_chat_model(
        self, users_server, status, kind
    ):
        users_server.root_model = "whatever"
        users_server.loaded = [{**self._tts(), "model_name": "whatever", "type": kind}]
        body = status()
        assert body["model_loaded"] is None
        assert body["context_size_sufficient"] is not False

    def test_the_expected_chat_model_wins_over_another_chat_model(
        self, users_server, status
    ):
        users_server.root_model = "Users-Small-Model"
        users_server.loaded = [
            {
                "model_name": "Users-Small-Model",
                "type": "llm",
                "recipe": "llamacpp",
                "recipe_options": {"ctx_size": 4096},
            },
            {
                "model_name": CHAT,
                "type": "llm",
                "recipe": "llamacpp",
                "recipe_options": {"ctx_size": 65536},
            },
        ]
        body = status()
        assert body["model_loaded"] == CHAT
        assert body["context_size_sufficient"] is True


# ---------------------------------------------------------------------------
# Bug: "Use GAIA's own" reporting a switch that did not happen
# ---------------------------------------------------------------------------


def _install_gaias_own(binary: bool) -> None:
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    embedded = EmbeddedLemonade()
    embedded.dist_dir.mkdir(parents=True, exist_ok=True)
    if binary:
        embedded.daemon_path.write_bytes(b"")


class TestUseGaiasOwn:
    USER_URL = "http://127.0.0.1:13305/api/v1"
    H = {"X-Gaia-UI": "1"}

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from gaia.ui.routers import lemonade as router_mod

        app = FastAPI()
        app.include_router(router_mod.router)
        self.applied = mock.Mock(return_value={"action": "starting_gaia_lemonade"})
        monkeypatch.setattr(choice, "apply_to_daemon", self.applied)
        self.ensured = mock.Mock(return_value={"port": 1})
        monkeypatch.setattr("gaia.daemon.client.ensure_lemonade", self.ensured)
        monkeypatch.setattr(router_mod, "_drop_cached_agents", mock.Mock())
        _save(lemonade_mode="external", lemonade_base_url=self.USER_URL)
        return TestClient(app)

    def _switch(self, client):
        return client.post(
            "/api/lemonade/target", json={"mode": "gaia"}, headers=self.H
        )

    def _still_on_users_server(self):
        cfg = GaiaConfig.load()
        assert (cfg.lemonade_mode, cfg.lemonade_base_url) == (
            "external",
            self.USER_URL,
        )
        assert choice.describe()["mode"] == "external"

    def test_missing_is_refused_and_nothing_changes(self, client):
        r = self._switch(client)
        assert r.status_code == 422
        assert r.json()["detail"]["status"] == choice.NOT_INSTALLED
        assert "gaia init --gaia-lemonade" in r.json()["detail"]["message"]
        self._still_on_users_server()
        self.applied.assert_not_called()

    def test_half_installed_is_refused_and_nothing_changes(self, client):
        _install_gaias_own(binary=False)
        r = self._switch(client)
        assert r.status_code == 422
        assert r.json()["detail"]["status"] == choice.NOT_INSTALLED
        self._still_on_users_server()

    def test_a_daemon_that_fails_the_switch_leaves_the_setting_unchanged(self, client):
        from gaia.daemon.errors import DaemonError

        _install_gaias_own(binary=True)
        self.applied.side_effect = DaemonError("it would not start")
        r = self._switch(client)
        assert r.status_code == 502
        assert "could not switch" in r.json()["detail"]
        assert "Nothing was changed" in r.json()["detail"]
        self._still_on_users_server()

    def test_installed_switches_once_gaias_server_answers(self, client):
        _install_gaias_own(binary=True)
        r = self._switch(client)
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "gaia"
        assert GaiaConfig.load().lemonade_mode == "gaia"
        self.ensured.assert_called_once()

    def test_a_background_start_that_fails_rolls_the_switch_back(self, client):
        """The daemon accepts the switch, then GAIA's server will not start."""
        from gaia.daemon.errors import DaemonError

        _install_gaias_own(binary=True)
        self.ensured.side_effect = DaemonError(
            "the daemon could not start Lemonade Server: port in use"
        )
        r = self._switch(client)
        assert r.status_code == 502
        assert "port in use" in r.json()["detail"]
        assert "Nothing was changed" in r.json()["detail"]
        self._still_on_users_server()

    def test_cli_rolls_back_a_background_start_that_fails(self, monkeypatch, capsys):
        from gaia.cli import _set_lemonade_config
        from gaia.daemon.errors import DaemonError

        _save(lemonade_mode="external", lemonade_base_url=self.USER_URL)
        _install_gaias_own(binary=True)
        monkeypatch.setattr(
            choice, "apply_to_daemon", mock.Mock(return_value={"action": "x"})
        )
        monkeypatch.setattr(
            "gaia.daemon.client.ensure_lemonade",
            mock.Mock(side_effect=DaemonError("would not start")),
        )
        with pytest.raises(SystemExit):
            _set_lemonade_config("lemonade_mode", "gaia", "cfg", None)
        out = capsys.readouterr().out
        assert "would not start" in out and "✅" not in out
        self._still_on_users_server()

    def test_first_run_choice_saves_before_setup_installs(self, client):
        _save(lemonade_mode=None)
        r = self._switch(client)
        assert r.status_code == 200, r.text
        assert GaiaConfig.load().lemonade_mode == "gaia"

    def test_cli_refuses_when_missing(self, capsys):
        from gaia.cli import _set_lemonade_config

        _save(lemonade_mode="external", lemonade_base_url=self.USER_URL)
        with pytest.raises(SystemExit):
            _set_lemonade_config("lemonade_mode", "gaia", "cfg", None)
        assert "gaia init --gaia-lemonade" in capsys.readouterr().err
        self._still_on_users_server()

    def test_cli_puts_the_mode_back_when_the_daemon_fails(self, monkeypatch, capsys):
        from gaia.cli import _set_lemonade_config
        from gaia.daemon.errors import DaemonError

        _save(lemonade_mode="external", lemonade_base_url=self.USER_URL)
        _install_gaias_own(binary=True)
        monkeypatch.setattr(
            choice, "apply_to_daemon", mock.Mock(side_effect=DaemonError("down"))
        )
        with pytest.raises(SystemExit):
            _set_lemonade_config("lemonade_mode", "gaia", "cfg", None)
        out = capsys.readouterr().out
        assert "Nothing was changed" in out and "✅" not in out
        self._still_on_users_server()


# ---------------------------------------------------------------------------
# First-run model check: never offer a model the user's server already has
# ---------------------------------------------------------------------------


class TestSetupCheckSaysWhatTheServerHas:
    def test_each_model_says_whether_the_server_has_it(self, users_server, monkeypatch):
        from gaia.installer import init_command
        from gaia.llm.lemonade_client import LemonadeClient

        monkeypatch.setattr(init_command, "with_chat_model", lambda p, ids, r: ids)
        monkeypatch.setattr(
            LemonadeClient, "check_model_available", lambda self, m: m == CHAT
        )
        monkeypatch.setattr(
            LemonadeClient, "get_model_info", lambda self, m: {"size_gb": 1.0}
        )
        status = init_command.check_setup_status(profile="chat", load=True)
        assert status.ready is False
        assert {m.id: m.downloaded for m in status.models} == {
            CHAT: True,
            EMBEDDER: False,
        }
        assert users_server.setup_writes() == []
