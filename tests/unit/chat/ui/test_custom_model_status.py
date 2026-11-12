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
    state = {"models": [], "status": 200, "down": False, "body": None, "calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append((request.method, request.url.path, request.url.query))
        if state["down"]:
            raise httpx.ConnectError("connection refused", request=request)
        if request.url.path.endswith("/models"):
            body = state["body"] or {"data": state["models"]}
            return httpx.Response(state["status"], json=body)
        return httpx.Response(200, json={"all_models_loaded": []})

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
