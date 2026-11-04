# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A used cloud model must never supply a local model's context window (#6575).

Lemonade lists a cloud model it has routed to as an ``llm`` entry in
``/health``'s ``all_models_loaded``, with a ``ctx_size`` 4096 placeholder, and
can list it ahead of the local model. Reading the first LLM entry made GAIA
believe a local model it had just loaded at 65536 was running at 4096.
"""

import threading
from unittest.mock import patch

import pytest

from gaia.llm.lemonade_client import (
    LemonadeClient,
    LoadedModelMissingError,
    is_cloud_model_entry,
    loaded_ctx_size,
)
from gaia.llm.lemonade_manager import LemonadeManager

LOCAL = "Qwen3.6-35B-A3B-GGUF"
CLOUD = "fireworks.deepseek-v4p1-flash"


def _cloud_entry():
    # Field names and values as a live Lemonade 2026.44.1 reports them.
    return {
        "model_name": CLOUD,
        "checkpoint": "accounts/fireworks/models/deepseek-v4p1-flash",
        "type": "llm",
        "device": "none",
        "recipe": "cloud",
        "recipe_options": {"ctx_size": 4096},
        "max_context_window": 1048576,
    }


def _local_entry(ctx):
    return {
        "model_name": LOCAL,
        "checkpoint": "unsloth/Qwen3.6-35B-A3B-GGUF:Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",
        "type": "llm",
        "device": "gpu",
        "recipe": "llamacpp",
        "recipe_options": {"ctx_size": ctx, "llamacpp_backend": "vulkan"},
        "max_context_window": 262144,
    }


def _health(*entries):
    return {
        "status": "ok",
        "version": "2026.44.1",
        "model_loaded": LOCAL,
        "all_models_loaded": list(entries),
    }


@pytest.fixture
def client():
    c = LemonadeClient(base_url="http://127.0.0.1:1/api/v1")
    with (
        patch.object(c, "list_models", return_value={"data": []}),
        patch.object(c, "get_model_max_context_window", return_value=262144),
    ):
        yield c


@pytest.fixture(autouse=True)
def _reset_manager():
    LemonadeManager.reset()
    yield
    LemonadeManager.reset()


def test_get_status_reads_the_local_model_not_a_cloud_entry_listed_first(client):
    with patch.object(
        client,
        "health_check",
        return_value=_health(_cloud_entry(), _local_entry(65536)),
    ):
        assert client.get_status().context_size == 65536


def test_get_status_without_the_catalog_still_skips_the_cloud_entry(client):
    with patch.object(
        client,
        "health_check",
        return_value=_health(_cloud_entry(), _local_entry(65536)),
    ):
        assert client.get_status(include_catalog=False).context_size == 65536


def test_only_a_cloud_entry_loaded_reports_no_window(client):
    with patch.object(client, "health_check", return_value=_health(_cloud_entry())):
        assert client.get_status().context_size == 0


def test_validate_context_size_skips_the_cloud_entry(client):
    with patch.object(
        client,
        "health_check",
        return_value=_health(_cloud_entry(), _local_entry(65536)),
    ):
        assert client.validate_context_size(32768, quiet=True) == (True, None)


def test_a_cloud_entry_is_recognised_from_its_raw_health_fields():
    assert is_cloud_model_entry(_cloud_entry())
    assert is_cloud_model_entry(
        {"model_name": "fireworks.x", "type": "llm", "device": "none"}
    )
    assert not is_cloud_model_entry(_local_entry(65536))
    assert not is_cloud_model_entry(
        {"model_name": "user.embeddinggemma-300m-GGUF", "device": "gpu"}
    )


def test_loaded_ctx_size_matches_the_named_model():
    entries = [_cloud_entry(), _local_entry(262144)]
    assert loaded_ctx_size(entries, LOCAL) == 262144
    assert loaded_ctx_size(entries) == 262144
    assert loaded_ctx_size(entries, "Gemma-4-E4B-it-GGUF") is None


def test_reload_reads_the_reloaded_models_own_window(client):
    before = _health(_cloud_entry(), _local_entry(4096))
    after = _health(_cloud_entry(), _local_entry(65536))
    lock = threading.Lock()
    with patch.object(client, "health_check", return_value=before):
        status = client.get_status()
    lock.acquire()
    with (
        patch.object(client, "health_check", return_value=after),
        patch.object(client, "load_model") as load,
    ):
        assert LemonadeManager._try_reload_with_ctx(client, status, 65536, True, lock)
    assert load.call_args.args[0] == LOCAL
    assert load.call_args.kwargs["ctx_size"] == 65536
    assert LemonadeManager._context_size == 65536


def test_reload_whose_model_vanished_from_health_fails_loudly(client):
    before = _health(_cloud_entry(), _local_entry(4096))
    after = _health(_cloud_entry())
    lock = threading.Lock()
    with patch.object(client, "health_check", return_value=before):
        status = client.get_status()
    lock.acquire()
    with (
        patch.object(client, "health_check", return_value=after),
        patch.object(client, "load_model"),
        pytest.raises(LoadedModelMissingError, match=LOCAL),
    ):
        LemonadeManager._try_reload_with_ctx(client, status, 65536, True, lock)
    assert lock.locked()
