# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An embedding request that lands while another process reloads the embedder.

GAIA reloads the embedder around an indexing job. A memory recall sent from
the agent's process in that gap got Lemonade's 404 ``model_not_loaded`` and
failed the turn: 8 of 1,236 requests across 60 reloads on real Lemonade.
"""

import pytest

from gaia.llm import lemonade_client
from gaia.llm.lemonade_client import (
    DEFAULT_EMBEDDING_MODEL,
    LemonadeClient,
    LemonadeClientError,
)

_NOT_LOADED = (
    'Request failed with status 404: {"error":{"message":"No model loaded: '
    'user.embeddinggemma-300m-GGUF","type":"model_not_loaded"}}'
)


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(lemonade_client, "_PINNED_BACKENDS", set())
    monkeypatch.setattr(lemonade_client, "_CPU_FALLBACK_EMBEDDERS", set())
    monkeypatch.setattr(lemonade_client, "preferred_llamacpp_backend", lambda: None)
    monkeypatch.setattr(lemonade_client.platform, "system", lambda: "Windows")


def _client(calls, embedding_errors):
    """A client whose ``/embeddings`` raises the queued errors, then answers."""
    client = LemonadeClient(verbose=False)
    client._follow_gaia_lemonade = lambda: None
    errors = list(embedding_errors)

    def fake_send(method, url, data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        calls.append(endpoint)
        if endpoint == "system-info":
            return {"recipes": {"llamacpp": {"default_backend": "vulkan"}}}
        if endpoint == "embeddings":
            if errors:
                raise LemonadeClientError(errors.pop(0))
            return {"data": [{"embedding": [0.0, 1.0]}]}
        return {"status": "success"}

    client._send_request = fake_send
    return client


def _requests(calls):
    return [c for c in calls if c != "system-info"]


def test_a_request_that_finds_the_embedder_unloaded_loads_it_and_is_sent_again():
    calls = []
    client = _client(calls, [_NOT_LOADED])
    client.model = "Gemma-4-E4B-it-GGUF"

    result = client.embeddings("recall", model=DEFAULT_EMBEDDING_MODEL)

    assert result["data"][0]["embedding"] == [0.0, 1.0]
    assert _requests(calls) == ["load", "embeddings", "load", "embeddings"]
    # The chat model this client is talking to stays its active model.
    assert client.model == "Gemma-4-E4B-it-GGUF"


def test_the_request_is_sent_again_only_once():
    calls = []
    client = _client(calls, [_NOT_LOADED, _NOT_LOADED])

    with pytest.raises(LemonadeClientError, match="No model loaded"):
        client.embeddings("recall", model=DEFAULT_EMBEDDING_MODEL)

    assert _requests(calls).count("embeddings") == 2


def test_any_other_embedding_failure_is_raised_at_once():
    calls = []
    client = _client(calls, ["Request failed with status 500: out of memory"])

    with pytest.raises(LemonadeClientError, match="out of memory"):
        client.embeddings("recall", model=DEFAULT_EMBEDDING_MODEL)

    assert _requests(calls) == ["load", "embeddings"]


def test_an_embedder_gaia_does_not_manage_is_not_reloaded():
    calls = []
    client = _client(calls, [_NOT_LOADED])

    with pytest.raises(LemonadeClientError, match="No model loaded"):
        client.embeddings("recall", model="nomic-embed-text-v2-moe-GGUF")

    assert _requests(calls) == ["embeddings"]
