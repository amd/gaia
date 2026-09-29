# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A cloud key typed into one TUI must reach the next one.

Lemonade holds a pasted key in memory only, so the next TUI — or the next
embedded-Lemonade start — had no Fireworks key. These pin that the key is kept
in the OS credential store, replayed into a Lemonade that lacks it, and never
replayed over a key Lemonade already has.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.llm import cloud_keys

KEY = "fw-secret-value"


@pytest.fixture()
def vault(monkeypatch):
    """An in-memory stand-in for the OS credential store."""
    store: dict = {}
    monkeypatch.setattr("gaia.connectors.store.save_secret", store.__setitem__)
    monkeypatch.setattr("gaia.connectors.store.peek_secret", store.get)
    monkeypatch.setattr(
        "gaia.connectors.store.delete_secret", lambda n: store.pop(n, None)
    )
    return store


class FakeLemonade:
    """Records what reaches Lemonade's cloud routes."""

    def __init__(self, provider: dict, discovered: int = 25):
        self.provider = provider
        self.discovered = discovered
        self.auth_calls: list = []

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        class _Resp:
            status_code = 200
            content = b"{}"

            def __init__(self, body):
                self._body = body

            def json(self):
                return self._body

        if url.endswith("/system-info"):
            return _Resp(
                {"cloud": {"providers": [self.provider] if self.provider else []}}
            )
        if url.endswith("/cloud/auth"):
            self.auth_calls.append(json)
            return _Resp({"models_discovered": self.discovered})
        raise AssertionError(f"unexpected {method} {url}")


@pytest.fixture()
def lemonade(monkeypatch):
    def install(provider, discovered=25):
        fake = FakeLemonade(provider, discovered)
        monkeypatch.setattr(cloud_keys.requests, "request", fake)
        monkeypatch.setattr(
            cloud_keys,
            "LemonadeClient",
            lambda verbose=False: type(
                "C", (), {"base_url": "http://127.0.0.1:1/api/v1"}
            )(),
        )
        return fake

    return install


def test_a_kept_key_is_replayed_into_a_lemonade_that_lost_it(vault, lemonade):
    cloud_keys.remember_key("fireworks", KEY)
    fake = lemonade(
        {"name": "fireworks", "runtime_key_set": False, "env_var_set": False}
    )

    assert cloud_keys.ensure_authenticated("fireworks") is True
    assert fake.auth_calls == [{"provider": "fireworks", "api_key": KEY}]


def test_a_key_lemonade_already_has_is_not_overwritten(vault, lemonade):
    cloud_keys.remember_key("fireworks", KEY)
    fake = lemonade({"name": "fireworks", "env_var_set": True, "models_discovered": 25})

    assert cloud_keys.ensure_authenticated("fireworks") is True
    assert fake.auth_calls == []


def test_nothing_is_replayed_when_the_provider_is_not_registered(vault, lemonade):
    cloud_keys.remember_key("fireworks", KEY)
    fake = lemonade(None)

    assert cloud_keys.ensure_authenticated("fireworks") is False
    assert fake.auth_calls == []


def test_a_rejected_key_is_reported_not_usable(vault, lemonade):
    cloud_keys.remember_key("fireworks", KEY)
    lemonade({"name": "fireworks"}, discovered=0)

    assert cloud_keys.ensure_authenticated("fireworks") is False


def test_the_fireworks_slot_is_the_one_gaia_already_reads(vault, monkeypatch):
    """A key kept from the TUI also serves gaia's own Fireworks client."""
    from gaia.llm.providers.fireworks import API_KEY_ENV_VARS, resolve_fireworks_api_key

    for name in API_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    cloud_keys.remember_key("fireworks", KEY)
    assert resolve_fireworks_api_key() == KEY


def test_a_store_that_discards_writes_is_an_error(monkeypatch):
    monkeypatch.setattr("gaia.connectors.store.save_secret", lambda n, v: None)
    monkeypatch.setattr("gaia.connectors.store.peek_secret", lambda n: None)

    with pytest.raises(cloud_keys.CloudKeyError, match="LEMONADE_FIREWORKS_API_KEY"):
        cloud_keys.remember_key("fireworks", KEY)


def test_forget_removes_the_kept_key(vault):
    cloud_keys.remember_key("fireworks", KEY)
    assert cloud_keys.forget_key("fireworks") is True
    assert cloud_keys.recall_key("fireworks") is None


def test_an_unknown_provider_is_refused(vault):
    with pytest.raises(cloud_keys.CloudKeyError, match="Unknown cloud provider"):
        cloud_keys.remember_key("openai", KEY)


# -- the daemon routes the TUI calls ----------------------------------------

TOKEN = "daemon-client-token"
pytestmark = pytest.mark.allow_network


@pytest.fixture()
def daemon():
    from gaia.daemon.provider_key_routes import build_provider_key_router

    app = FastAPI()
    app.include_router(build_provider_key_router(TOKEN))
    return TestClient(app)


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_the_route_needs_the_daemon_token(daemon, vault):
    resp = daemon.post("/daemon/v1/providers/fireworks/key", json={"key": KEY})
    assert resp.status_code == 401
    assert vault == {}


def test_the_route_keeps_the_key_and_never_echoes_it(daemon, vault):
    resp = daemon.post(
        "/daemon/v1/providers/fireworks/key", json={"key": KEY}, headers=AUTH
    )
    assert resp.status_code == 200
    assert KEY not in resp.text
    assert vault["fireworks_api_key"] == KEY


def test_an_unusable_store_is_a_503_with_the_remedy(daemon, monkeypatch):
    monkeypatch.setattr("gaia.connectors.store.save_secret", lambda n, v: None)
    monkeypatch.setattr("gaia.connectors.store.peek_secret", lambda n: None)
    resp = daemon.post(
        "/daemon/v1/providers/fireworks/key", json={"key": KEY}, headers=AUTH
    )
    assert resp.status_code == 503
    assert "LEMONADE_FIREWORKS_API_KEY" in resp.json()["detail"]


def test_an_unknown_provider_is_a_404(daemon, vault):
    resp = daemon.post(
        "/daemon/v1/providers/openai/key", json={"key": KEY}, headers=AUTH
    )
    assert resp.status_code == 404
    assert vault == {}


def test_forget_route_removes_the_key(daemon, vault):
    vault["fireworks_api_key"] = KEY
    resp = daemon.delete("/daemon/v1/providers/fireworks/key", headers=AUTH)
    assert resp.json() == {"removed": True}
    assert "fireworks_api_key" not in vault


# -- the replay every front-end reaches through readiness --------------------


def test_readiness_replays_a_fireworks_key(monkeypatch):
    from gaia.agents.base import readiness

    seen = []
    monkeypatch.setattr(
        "gaia.llm.cloud_keys.ensure_authenticated", lambda p: seen.append(p) or True
    )
    assert readiness._replay_cloud_key("fireworks.deepseek-v4p1-flash") is True
    assert readiness._replay_cloud_key("amd.gpt-5") is True
    assert readiness._replay_cloud_key("Gemma-4-E4B-it-GGUF") is False
    assert seen == ["fireworks", "amd"]
