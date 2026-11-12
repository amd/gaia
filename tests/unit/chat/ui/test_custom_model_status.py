# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What saving a Custom model ID learns about the id from the model server."""

import asyncio

import httpx
import pytest

from gaia.ui.routers import system

BASE = "http://127.0.0.1:1/api/v1"


@pytest.fixture
def model_server(monkeypatch):
    """The model server's HTTP surface, answered from a table; records each call."""
    state = {
        "models": [],
        "status": 200,
        "down": False,
        "body": None,
        "health": {"all_models_loaded": []},
        "raw": None,
        "calls": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append((request.method, request.url.path, request.url.query))
        if state["down"]:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path.endswith("/models"):
            if state["raw"] is not None:
                return httpx.Response(200, text=state["raw"])
            body = state["body"] or {"data": state["models"]}
            return httpx.Response(state["status"], json=body)
        return httpx.Response(200, json=state["health"])

    real_client = httpx.AsyncClient

    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(system, "resolve_lemonade_base_url", lambda: BASE)
    monkeypatch.setattr(system, "resolve_lemonade_api_key", lambda **k: None)
    return state


def _status(model_id):
    return asyncio.run(system._check_model_status(model_id))


def test_a_listed_model_is_found_and_checked(model_server):
    model_server["models"] = [{"id": "fireworks.glm-5p3-flash", "recipe": "cloud"}]
    status = _status("fireworks.glm-5p3-flash")
    assert (status.checked, status.found) == (True, True)
    assert ("GET", "/api/v1/models", b"show_all=true") in model_server["calls"]


def test_an_id_the_server_does_not_list_is_checked_and_not_found(model_server):
    model_server["models"] = [{"id": "fireworks.glm-5p3-flash", "recipe": "cloud"}]
    status = _status("fireworks.no-such-model-violet-otter")
    assert (status.checked, status.found) == (True, False)


def test_a_user_model_matches_its_listing_without_the_prefix(model_server):
    model_server["models"] = [{"id": "My-Model-GGUF", "downloaded": True}]
    status = _status("user.My-Model-GGUF")
    assert (status.checked, status.found, status.downloaded) == (True, True, True)


def test_an_unreachable_server_leaves_the_id_unchecked(model_server):
    model_server["down"] = True
    status = _status("fireworks.no-such-model-violet-otter")
    assert (status.checked, status.found) == (False, False)


def test_a_failing_model_list_leaves_the_id_unchecked(model_server):
    model_server["status"] = 502
    status = _status("Qwen3-4B-GGUF")
    assert (status.checked, status.found) == (False, False)


def test_a_malformed_model_list_leaves_the_id_unchecked(model_server):
    model_server["body"] = {"object": "list", "data": "x"}
    status = _status("Qwen3-4B-GGUF")
    assert (status.checked, status.found) == (False, False)


def test_a_model_list_with_a_non_object_entry_leaves_the_id_unchecked(model_server):
    model_server["body"] = {"data": [{"id": "Qwen3-4B-GGUF"}, "Qwen3-4B-GGUF"]}
    status = _status("Qwen3-4B-GGUF")
    assert (status.checked, status.found) == (False, False)


def test_a_model_list_that_is_not_json_leaves_the_id_unchecked(model_server):
    model_server["raw"] = "<html>proxy login</html>"
    status = _status("Qwen3-4B-GGUF")
    assert (status.checked, status.found) == (False, False)


@pytest.mark.parametrize(
    "health",
    [
        ["not", "a", "dict"],
        {"model_loaded": 7, "all_models_loaded": "x"},
        {"all_models_loaded": [None, 3, {"model_name": None}]},
    ],
)
def test_a_malformed_health_answer_keeps_the_list_verdict(model_server, health):
    model_server["models"] = [{"id": "Qwen3-4B-GGUF"}]
    model_server["health"] = health
    status = _status("Qwen3-4B-GGUF")
    assert (status.checked, status.found, status.loaded) == (True, True, False)


def test_a_loaded_user_model_counts_without_the_prefix(model_server):
    model_server["health"] = {
        "model_loaded": "My-Model-GGUF",
        "all_models_loaded": [{"model_name": "My-Model-GGUF"}],
    }
    status = _status("user.My-Model-GGUF")
    assert (status.found, status.loaded) == (True, True)


def test_a_prefix_or_substring_of_a_listed_id_is_not_found(model_server):
    model_server["models"] = [{"id": "Qwen3-4B-GGUF", "name": "Qwen3 4B"}]
    for wrong in ("Qwen3-4B", "Qwen3-4B-GGUF-extra", "user.", " "):
        assert _status(wrong).found is False, wrong


@pytest.mark.parametrize(
    "bad_base",
    [
        "http://localhost:13305x/api/v1",
        "http://127.0.0.1:notaport/api/v1",
        "http://[::1/api/v1",
        "http://a\x00b/api/v1",
    ],
)
def test_a_mistyped_server_address_leaves_the_id_unchecked(
    model_server, monkeypatch, bad_base
):
    monkeypatch.setattr(system, "resolve_lemonade_base_url", lambda: bad_base)
    status = _status("Qwen3-4B-GGUF")
    assert (status.checked, status.found, status.loaded) == (False, False, False)
    assert model_server["calls"] == []


def test_a_mistyped_server_address_does_not_fail_settings(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from gaia.ui.server import create_app

    monkeypatch.setattr("gaia.config.GAIA_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(
        system, "resolve_lemonade_base_url", lambda: "http://localhost:13305x/api/v1"
    )
    monkeypatch.setattr(system, "resolve_lemonade_api_key", lambda **k: None)
    client = TestClient(create_app(db_path=":memory:"))

    saved = client.put("/api/settings", json={"custom_model": "Qwen3-4B-GGUF"})
    loaded = client.get("/api/settings")

    for resp in (saved, loaded):
        assert resp.status_code == 200, resp.text
        assert resp.json()["model_status"]["checked"] is False
