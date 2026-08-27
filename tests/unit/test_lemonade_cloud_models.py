# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A cloud-routed model must never be pulled, loaded, or slot-leased.

Every test here starts from a **cold** classification cache. A warm cache is
exactly the hidden state that would let the bug through: once the catalog has
been read, the short-circuit fires and the test passes whether or not the cold
path works — and the cold path is the one a user hits on a fresh process.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm import lemonade_client as lc
from gaia.llm.lemonade_client import (
    LemonadeClient,
    is_cloud_model,
    is_tool_calling_model,
    may_be_cloud_model,
    record_cloud_models,
)

CLOUD_MODEL = "amd.Claude-Opus-5"
LOCAL_MODEL = "Gemma-4-E4B-it-GGUF"

CATALOG = {
    "data": [
        {
            "id": CLOUD_MODEL,
            "recipe": "cloud",
            "labels": ["tool-calling", "vision"],
            "context_length": 1000000,
        },
        {
            "id": "amd.tiny-chat",
            "recipe": "cloud",
            "labels": [],
            "context_length": 8192,
        },
        {
            "id": LOCAL_MODEL,
            "recipe": "llamacpp",
            "labels": ["hot"],
            "downloaded": True,
        },
    ]
}


@pytest.fixture(autouse=True)
def cold_cache():
    """Start every test with no knowledge of which models are cloud-routed."""
    record_cloud_models({"data": []})
    yield
    record_cloud_models({"data": []})


@pytest.fixture
def client():
    c = LemonadeClient(verbose=False)
    c.list_models = MagicMock(side_effect=lambda *a, **k: _list_and_record())
    return c


def _list_and_record():
    """Stand in for the real list_models, including its cache side effect."""
    record_cloud_models(CATALOG)
    return CATALOG


class TestClassification:
    def test_cache_starts_cold(self):
        assert not is_cloud_model(CLOUD_MODEL)

    def test_prefilter_rules_out_local_ids_without_a_request(self):
        # A dotless id can never be "<provider>.<model>", so no network call.
        assert not may_be_cloud_model(LOCAL_MODEL)
        assert may_be_cloud_model(CLOUD_MODEL)

    def test_registered_local_model_with_a_dot_is_not_probed(self):
        for requirement in lc.MODELS.values():
            if "." in requirement.model_id:
                assert not may_be_cloud_model(requirement.model_id)

    def test_client_warms_the_cache_on_demand(self, client):
        assert client._is_cloud_model(CLOUD_MODEL) is True
        assert client.list_models.called

    def test_client_does_not_probe_for_a_local_model(self, client):
        assert client._is_cloud_model(LOCAL_MODEL) is False
        client.list_models.assert_not_called()

    def test_uninstalling_the_provider_drops_its_models(self):
        record_cloud_models(CATALOG)
        assert is_cloud_model(CLOUD_MODEL)
        record_cloud_models({"data": []})
        assert not is_cloud_model(CLOUD_MODEL)


class TestNoDownloadOrLoad:
    def test_cold_cache_cloud_model_is_never_loaded(self, client):
        """The regression this whole change exists to prevent."""
        with (
            patch.object(client, "load_model") as load,
            patch.object(client, "pull_model") as pull,
        ):
            client._ensure_model_loaded(CLOUD_MODEL, auto_download=True)

        load.assert_not_called()
        pull.assert_not_called()

    def test_pinned_ctx_does_not_evict_the_local_model_for_a_cloud_one(self, client):
        """The pin path runs first, so the cloud check must precede it.

        ``_ensure_pinned_load`` unloads whatever is resident before loading at
        the pinned window. Letting a cloud model reach it would evict the local
        model for something that has no local weights at all.
        """
        client.ctx_size_override = 65536
        with (
            patch.object(client, "_ensure_pinned_load") as pinned,
            patch.object(client, "load_model") as load,
        ):
            client._ensure_model_loaded(CLOUD_MODEL, auto_download=True)

        pinned.assert_not_called()
        load.assert_not_called()

    def test_local_model_still_takes_the_normal_load_path(self, client):
        client.ctx_size_override = 65536
        with patch.object(client, "_ensure_pinned_load") as pinned:
            client._ensure_model_loaded(LOCAL_MODEL, auto_download=True)

        pinned.assert_called_once_with(LOCAL_MODEL)

    def test_ensure_model_downloaded_reports_a_cloud_model_as_available(self, client):
        with patch.object(client, "pull_model") as pull:
            assert client.ensure_model_downloaded(CLOUD_MODEL) is True
        pull.assert_not_called()

    def test_a_missing_cloud_model_is_not_retried_as_a_download(self, client):
        """A 404 from the gateway means a bad id or a dead token, not a
        missing local file — surface it instead of burying it."""
        record_cloud_models(CATALOG)
        original = lc.LemonadeClientError("model not found")
        with (
            patch.object(client, "_is_model_error", return_value=True),
            patch.object(client, "ensure_model_downloaded") as download,
        ):
            with pytest.raises(lc.LemonadeClientError):
                client._execute_with_auto_download(
                    MagicMock(), CLOUD_MODEL, auto_download=True, error=original
                )
        download.assert_not_called()


class TestSlotLease:
    def test_cloud_model_takes_no_model_slot(self, client):
        """A gateway request holds no local slot; leasing one would stall
        every local agent for the length of a remote call."""
        record_cloud_models(CATALOG)
        with patch("gaia.daemon.broker_client.model_lease") as lease:
            with client._model_slot_lease(CLOUD_MODEL):
                pass
        lease.assert_not_called()

    def test_local_model_still_takes_the_lease(self, client):
        with patch("gaia.daemon.broker_client.model_lease") as lease:
            client._model_slot_lease(LOCAL_MODEL)
        lease.assert_called_once()


class TestToolCalling:
    def test_local_models_are_unchanged_by_cloud_discovery(self):
        """The change must be strictly additive for every local id."""
        record_cloud_models(CATALOG)
        assert is_tool_calling_model(LOCAL_MODEL) is True
        assert is_tool_calling_model("gemma4-it-e2b-FLM") is False
        assert is_tool_calling_model("Some-Unknown-GGUF") is True
        assert is_tool_calling_model(None) is False

    def test_gateway_capability_label_is_respected(self):
        record_cloud_models(CATALOG)
        assert is_tool_calling_model(CLOUD_MODEL) is True
        # The gateway says this one has no tools, so the optimistic GGUF
        # default must not apply.
        assert is_tool_calling_model("amd.tiny-chat") is False

    def test_unclassified_cloud_id_keeps_the_optimistic_default(self):
        assert is_tool_calling_model("amd.not-yet-discovered") is True
