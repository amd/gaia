# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``LemonadeClient.list_models`` reports a malformed model list instead of crashing."""

import pytest
import responses

from gaia.llm.lemonade_client import (
    CLOUD_RECIPE,
    LemonadeClient,
    LemonadeClientError,
    is_cloud_model,
    record_cloud_models,
)

API_BASE = "http://localhost:13305/api/v1"
MODELS_URL = f"{API_BASE}/models"
CLOUD_ID = "amd.Example-Cloud-Model"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    for name in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "LEMONADE_PORT"):
        monkeypatch.delenv(name, raising=False)
    return LemonadeClient(base_url=API_BASE, verbose=False)


@pytest.fixture
def known_cloud_model():
    record_cloud_models({"data": [{"id": CLOUD_ID, "recipe": CLOUD_RECIPE}]})
    yield CLOUD_ID
    record_cloud_models({"data": []})


@pytest.mark.parametrize(
    "body, got",
    [
        ([{"id": "Gemma-4-E4B-it-GGUF"}], "got a JSON array"),
        ({"data": "x"}, "got 'data' as a JSON string"),
        ({"data": [1]}, "got entry 0 of 'data' as a JSON number"),
        ({"data": [{"id": "ok"}, None]}, "got entry 1 of 'data' as a JSON null"),
        ({"data": None}, "got 'data' as a JSON null"),
        ({"object": "list"}, "got an object with no 'data' field"),
    ],
)
@responses.activate
def test_malformed_model_list_raises_actionable_error(client, body, got):
    responses.add(responses.GET, MODELS_URL, json=body, status=200)

    with pytest.raises(LemonadeClientError) as excinfo:
        client.list_models()

    message = str(excinfo.value)
    assert got in message
    assert "localhost:13305" in message
    assert "LEMONADE_BASE_URL" in message


@responses.activate
def test_malformed_model_list_keeps_known_cloud_models(client, known_cloud_model):
    responses.add(responses.GET, MODELS_URL, json={"data": [1]}, status=200)

    with pytest.raises(LemonadeClientError):
        client.list_models()

    assert is_cloud_model(known_cloud_model)


@responses.activate
def test_valid_model_list_is_returned_unchanged(client):
    body = {
        "object": "list",
        "data": [
            {"id": "Gemma-4-E4B-it-GGUF", "recipe": "llamacpp", "downloaded": True},
            {"id": CLOUD_ID, "recipe": CLOUD_RECIPE, "labels": ["tool-calling"]},
        ],
    }
    responses.add(responses.GET, f"{MODELS_URL}?show_all=true", json=body, status=200)

    try:
        assert client.list_models(show_all=True) == body
        assert is_cloud_model(CLOUD_ID)
        assert client._model_metadata["Gemma-4-E4B-it-GGUF"]["recipe"] == "llamacpp"
    finally:
        record_cloud_models({"data": []})


@responses.activate
def test_empty_model_list_is_returned_unchanged(client):
    body = {"object": "list", "data": []}
    responses.add(responses.GET, MODELS_URL, json=body, status=200)

    assert client.list_models() == body
