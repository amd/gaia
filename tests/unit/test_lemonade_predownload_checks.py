# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Pre-download checks read what Lemonade actually reports (#4214).

The mocked server mirrors Lemonade 11.8.1: ``/models`` lists undownloaded
catalog entries only under ``?show_all=true``, sizes arrive in a ``size`` field
(GB), free space for the model cache is ``/system-info`` ``model_storage``, and
only ``/health`` knows which models are loaded.
"""

import json
from unittest.mock import patch

import pytest
import responses

from gaia.llm.lemonade_client import (
    InsufficientDiskSpaceError,
    LemonadeClient,
    LemonadeClientError,
)

BASE = "http://localhost:13305/api/v1"
GIB = 1024**3

DOWNLOADED = [
    {"id": "Gemma-4-E4B-it-GGUF", "size": 5.56, "downloaded": True, "labels": []},
    {"id": "Qwen3.5-4B-GGUF", "size": 2.91, "downloaded": True, "labels": []},
]
UNDOWNLOADED = [
    {"id": "Gemma-4-12B-it-GGUF", "size": 7.29, "downloaded": False, "labels": []},
]
HEALTH = {
    "status": "ok",
    "all_models_loaded": [
        {
            "model_name": "Qwen3.5-4B-GGUF",
            "type": "llm",
            "recipe_options": {"ctx_size": 65536},
        }
    ],
}
SSE_COMPLETE = b"event: complete\ndata: {}\n\n"


def _models(request):
    data = DOWNLOADED + (UNDOWNLOADED if "show_all=true" in request.url else [])
    return 200, {}, json.dumps({"object": "list", "data": data})


@pytest.fixture
def server():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(responses.GET, f"{BASE}/models", callback=_models)
        rsps.add(responses.GET, f"{BASE}/health", json=HEALTH)
        yield rsps


@pytest.fixture
def client():
    return LemonadeClient(base_url=BASE)


def _storage(server, free_bytes):
    server.add(
        responses.GET,
        f"{BASE}/system-info",
        json={"model_storage": {"path": "/srv/models", "free_bytes": free_bytes}},
    )


class TestGetModelInfo:
    def test_undownloaded_model_reports_catalog_size(self, server, client):
        info = client.get_model_info("Gemma-4-12B-it-GGUF")

        assert info == {
            "id": "Gemma-4-12B-it-GGUF",
            "size_gb": 7.29,
            "downloaded": False,
        }

    def test_downloaded_model_reads_size_field(self, server, client):
        assert client.get_model_info("Gemma-4-E4B-it-GGUF")["size_gb"] == 5.56

    def test_server_error_is_raised_not_guessed(self, client):
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, f"{BASE}/models", status=500)
            with pytest.raises(LemonadeClientError):
                client.get_model_info("Gemma-4-12B-it-GGUF")


class TestAutoDownloadDiskCheck:
    """``load_model(auto_download=True)`` checks the server's model cache."""

    def _load(self, client, *load_results):
        with patch.object(
            client, "_post_load_with_transient_retry", side_effect=load_results
        ):
            return client.load_model(
                "Gemma-4-12B-it-GGUF", auto_download=True, prompt=False
            )

    def _pull(self, server):
        return server.add(
            responses.POST,
            f"{BASE}/pull",
            body=SSE_COMPLETE,
            content_type="text/event-stream",
        )

    def test_refuses_when_model_storage_is_short(self, server, client):
        _storage(server, 4 * GIB)
        pull = self._pull(server)

        with pytest.raises(InsufficientDiskSpaceError, match="/srv/models"):
            self._load(client, LemonadeClientError("model not found"))

        assert pull.call_count == 0

    def test_downloads_when_model_storage_fits(self, server, client):
        _storage(server, 100 * GIB)
        pull = self._pull(server)

        result = self._load(
            client, LemonadeClientError("model not found"), {"status": "success"}
        )

        assert result == {"status": "success"}
        assert pull.call_count == 1

    def test_missing_free_bytes_raises_before_downloading(self, server, client):
        server.add(responses.GET, f"{BASE}/system-info", json={"devices": {}})
        pull = self._pull(server)

        with pytest.raises(LemonadeClientError, match="model_storage"):
            self._load(client, LemonadeClientError("model not found"))

        assert pull.call_count == 0


class TestFirstRunBanner:
    def _ensure(self, client, model):
        with (
            patch.object(
                client, "get_status", side_effect=LemonadeClientError("probe down")
            ),
            patch.object(client, "get_model_max_context_window", return_value=None),
            patch.object(client, "load_model", return_value={}),
        ):
            client._ensure_model_loaded_locked(model)

    def test_undownloaded_model_announces_download(self, server, client, capsys):
        self._ensure(client, "Gemma-4-12B-it-GGUF")

        assert "Downloading model" in capsys.readouterr().out

    def test_downloaded_model_announces_load(self, server, client, capsys):
        self._ensure(client, "Gemma-4-E4B-it-GGUF")

        out = capsys.readouterr().out
        assert "Loading model" in out
        assert "Downloading model" not in out


class TestCheckModelLoaded:
    def test_loaded_model_is_reported(self, server, client):
        assert client.check_model_loaded("Qwen3.5-4B-GGUF") is True

    def test_downloaded_but_unloaded_model_is_not_loaded(self, server, client):
        assert client.check_model_loaded("Gemma-4-E4B-it-GGUF") is False

    def test_substring_of_a_loaded_model_is_not_loaded(self, server, client):
        assert client.check_model_loaded("Qwen3.5-4B") is False

    def test_health_failure_is_raised(self, client):
        with responses.RequestsMock() as rsps:
            rsps.add(responses.GET, f"{BASE}/health", status=500)
            with pytest.raises(LemonadeClientError):
                client.check_model_loaded("Qwen3.5-4B-GGUF")
