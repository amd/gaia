# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Clearing Settings > Advanced > Custom model ID goes back to the picked model.

The field says "Leave empty to use the model chosen under Model", but clearing
it dropped a cloud-only user onto the local default they never downloaded and
forgot their pick, so a restart did not bring it back either.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gaia.config import GaiaConfig
from gaia.ui.routers import providers
from gaia.ui.server import create_app

_PICKED = "fireworks.deepseek-v4p1-flash"
_CUSTOM = "fireworks.glm-5p3-flash"
_LOCAL = "Gemma-4-E4B-it-GGUF"


@pytest.fixture(autouse=True)
def _private_home(monkeypatch, tmp_path):
    monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setenv("GAIA_POLICY_FILE", str(tmp_path / "policy.json"))
    providers.reset_restore_state()


@pytest.fixture
def app():
    return create_app(db_path=":memory:")


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def db(app):
    return app.state.db


def _local_models(*downloaded):
    """Stand in for the model server's local list."""

    def listing(provider, offer_optional=True):
        return [{"id": _LOCAL, "downloaded": _LOCAL in downloaded}]

    return patch.object(providers, "_list_models", side_effect=listing)


def _pick(client, model):
    with patch.object(providers, "_check_model_available", return_value=None):
        resp = client.post(
            "/api/providers/select", json={"model": model}, headers=_UI_HEADERS
        )
    assert resp.status_code == 200, resp.text


_UI_HEADERS = {"X-Gaia-UI": "1"}


def test_clearing_goes_back_to_the_model_picked_under_model(client, db):
    _pick(client, _PICKED)
    assert client.put("/api/settings", json={"custom_model": _CUSTOM}).json()[
        "custom_model"
    ] == (_CUSTOM)

    with _local_models():
        resp = client.put("/api/settings", json={"custom_model": ""})

    assert resp.status_code == 200
    assert db.get_setting("custom_model") == _PICKED
    cfg = GaiaConfig.load()
    assert (cfg.last_model, cfg.last_provider) == (_PICKED, "fireworks")
    # The field is empty again: the picked model is not a custom ID.
    assert resp.json()["custom_model"] is None
    assert client.get("/api/settings").json()["custom_model"] is None


def test_clearing_the_picked_model_out_of_the_field_keeps_it(client, db):
    """The field used to list the picked model as if it were a custom ID."""
    _pick(client, _PICKED)

    with _local_models():
        client.put("/api/settings", json={"custom_model": ""})

    assert db.get_setting("custom_model") == _PICKED
    assert GaiaConfig.load().last_model == _PICKED


def test_a_cloud_user_with_no_recorded_pick_is_not_dropped_on_an_undownloaded_model(
    client, db
):
    """A pick made before GAIA recorded picks: the local default is not on disk."""
    client.put("/api/settings", json={"custom_model": _PICKED})

    with _local_models():
        resp = client.put("/api/settings", json={"custom_model": ""})

    assert db.get_setting("custom_model") == _PICKED
    assert GaiaConfig.load().last_model == _PICKED
    assert resp.json()["custom_model"] is None


def test_a_cloud_user_whose_local_default_is_on_disk_goes_back_to_it(client, db):
    client.put("/api/settings", json={"custom_model": _PICKED})

    with (
        _local_models(_LOCAL),
        patch("gaia.ui.database.resolved_default_model", return_value=_LOCAL),
    ):
        client.put("/api/settings", json={"custom_model": ""})

    assert db.get_setting("custom_model") is None
    assert GaiaConfig.load().last_model is None


def test_a_local_custom_model_clears_to_the_default(client, db):
    client.put("/api/settings", json={"custom_model": "Qwen3-4B-GGUF"})

    with _local_models():
        client.put("/api/settings", json={"custom_model": ""})

    assert db.get_setting("custom_model") is None
    assert GaiaConfig.load().last_model is None


def test_a_picked_cloud_model_is_not_restored_while_local_only_is_on(client, db):
    from gaia.llm import local_only

    _pick(client, _PICKED)
    client.put("/api/settings", json={"custom_model": "Qwen3-4B-GGUF"})
    local_only.set_policy("local_ai_only")

    with _local_models():
        resp = client.put("/api/settings", json={"custom_model": ""})

    assert resp.status_code == 200
    assert db.get_setting("custom_model") is None
    assert GaiaConfig.load().last_model is None
