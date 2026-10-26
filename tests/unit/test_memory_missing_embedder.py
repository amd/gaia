# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A missing embedding model is downloaded at startup, not left off (#2831).

From a cold state (Lemonade running, the embedder never pulled) memory used to
start disabled for the whole session. It now pulls the embedder the way
``gaia init`` does and starts on; when the pull fails, the session reports the
pull error and ``gaia init`` as the fix. Every test mocks Lemonade.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.memory import (
    MEMORY_UNAVAILABLE_MODEL_NOT_PULLED,
    MEMORY_UNAVAILABLE_SERVICE_UNREACHABLE,
    MemoryMixin,
)
from gaia.llm.lemonade_client import (
    DEFAULT_EMBEDDING_CHECKPOINT,
    DEFAULT_EMBEDDING_MODEL,
)

NOT_FOUND = RuntimeError(
    "Embedding failed: Error generating embeddings: Request failed with status "
    '404: {"error":{"code":"model_not_found","message":"Model '
    f"'{DEFAULT_EMBEDDING_MODEL}' was not found.\"}}}}"
)
VECTOR = [np.ones(768, dtype=np.float32).tolist()]


class _Host(MemoryMixin):
    pass


@contextmanager
def _lemonade(embed_side_effect, pulled=True, pull_error=None):
    """Mock the embedder and the Lemonade client the pull goes through.

    ``pull_error`` is sent as the pull stream's error event before it returns.
    Yields the ``LemonadeClient`` class mock, so a test can read the pull call.
    """

    def ensure_model_downloaded(name, on_progress=None, **kwargs):
        if pull_error is not None:
            on_progress({"event": "error", "error": pull_error})
        return pulled

    embedder = MagicMock()
    embedder.embed.side_effect = embed_side_effect
    with (
        patch.object(MemoryMixin, "_get_embedder", return_value=embedder),
        patch.object(MemoryMixin, "_get_embedding_cache") as cache,
        patch.object(MemoryMixin, "_backfill_embeddings", return_value=0),
        patch.object(MemoryMixin, "_rebuild_faiss_index", return_value=None),
        patch.object(MemoryMixin, "_rebuild_proc_faiss_index", return_value=None),
        patch(
            "gaia.agents.base.memory._system_context_is_enabled",
            return_value=False,
        ),
        patch("gaia.llm.lemonade_client.LemonadeClient") as client_cls,
    ):
        cache.return_value.get.return_value = None
        client_cls.return_value.ensure_model_downloaded.side_effect = (
            ensure_model_downloaded
        )
        yield client_cls


@pytest.fixture(autouse=True)
def _memory_on(monkeypatch):
    monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)


def _start(tmp_path, **kwargs):
    host = _Host()
    host.init_memory(db_path=tmp_path / "memory.db", **kwargs)
    return host


def test_a_missing_default_embedder_is_pulled_and_memory_starts(tmp_path):
    with _lemonade([NOT_FOUND, VECTOR]) as client_cls:
        host = _start(tmp_path)
    try:
        assert host.memory_store is not None
        assert host.memory_unavailable_message() is None
        pull = client_cls.return_value.ensure_model_downloaded
        pull.assert_called_once()
        # A user. model registers on its first pull: name-only 500s on Lemonade.
        args, kwargs = pull.call_args
        assert args == (DEFAULT_EMBEDDING_MODEL,)
        assert kwargs["checkpoint"] == DEFAULT_EMBEDDING_CHECKPOINT
        assert kwargs["recipe"] == "llamacpp"
        assert kwargs["embedding"] is True
    finally:
        host.memory_store.close()


def test_a_failed_pull_reports_its_reason_and_gaia_init(tmp_path):
    with _lemonade([NOT_FOUND], pulled=False, pull_error="disk full"):
        host = _start(tmp_path)
    assert host.memory_store is None
    assert host._memory_unavailable_reason == MEMORY_UNAVAILABLE_MODEL_NOT_PULLED
    message = host.memory_unavailable_message()
    assert DEFAULT_EMBEDDING_MODEL in message
    assert "downloading it failed: disk full" in message
    assert "Run `gaia init`" in message
    assert "https://amd-gaia.ai/docs/reference/troubleshooting" in message


def test_a_pull_that_raises_is_still_reported_as_not_pulled(tmp_path):
    with _lemonade([NOT_FOUND]) as client_cls:
        client_cls.side_effect = ConnectionError("reset by peer")
        host = _start(tmp_path)
    assert host._memory_unavailable_reason == MEMORY_UNAVAILABLE_MODEL_NOT_PULLED
    assert "downloading it failed: reset by peer" in host.memory_unavailable_message()


def test_a_built_in_embedder_is_pulled_by_name_only(tmp_path):
    # Sending a recipe for a built-in makes Lemonade 400 the pull (#1655).
    with _lemonade([NOT_FOUND, VECTOR]) as client_cls:
        host = _start(tmp_path, embedding_model="embed-gemma-300m-FLM")
    try:
        pull = client_cls.return_value.ensure_model_downloaded
        args, kwargs = pull.call_args
        assert args == ("embed-gemma-300m-FLM",)
        assert set(kwargs) == {"show_progress", "on_progress"}
    finally:
        host.memory_store.close()


def test_an_unprefixed_registry_id_registers_under_its_user_name(tmp_path):
    with _lemonade([NOT_FOUND, VECTOR]) as client_cls:
        host = _start(tmp_path, embedding_model="embeddinggemma-300m-GGUF")
    try:
        args, kwargs = client_cls.return_value.ensure_model_downloaded.call_args
        assert args == (DEFAULT_EMBEDDING_MODEL,)
        assert kwargs["checkpoint"] == DEFAULT_EMBEDDING_CHECKPOINT
    finally:
        host.memory_store.close()


def test_the_download_is_announced_on_the_console(tmp_path):
    host = _Host()
    host.console = MagicMock()
    with _lemonade([NOT_FOUND, VECTOR]):
        host.init_memory(db_path=tmp_path / "memory.db")
    try:
        notice = host.console.print_info.call_args[0][0]
        assert f"Downloading the embedding model '{DEFAULT_EMBEDDING_MODEL}'" in notice
    finally:
        host.memory_store.close()


def test_a_pull_that_does_not_fix_the_probe_still_reports_not_pulled(tmp_path):
    with _lemonade([NOT_FOUND, NOT_FOUND]) as client_cls:
        host = _start(tmp_path)
    client_cls.return_value.ensure_model_downloaded.assert_called_once()
    assert host.memory_store is None
    assert host._memory_unavailable_reason == MEMORY_UNAVAILABLE_MODEL_NOT_PULLED
    assert "has not been pulled" in host.memory_unavailable_message()


def test_an_unregistered_custom_embedder_is_not_pulled(tmp_path):
    with _lemonade([NOT_FOUND]) as client_cls:
        host = _start(tmp_path, embedding_model="user.someone-elses-embed-GGUF")
    client_cls.return_value.ensure_model_downloaded.assert_not_called()
    assert host._memory_unavailable_reason == MEMORY_UNAVAILABLE_MODEL_NOT_PULLED
    message = host.memory_unavailable_message()
    assert "not in GAIA's model registry" in message
    assert "Pull 'user.someone-elses-embed-GGUF' in Lemonade" in message


def test_an_unreachable_server_is_not_sent_a_pull(tmp_path):
    with _lemonade([ConnectionError("Connection refused")]) as client_cls:
        host = _start(tmp_path)
    client_cls.return_value.ensure_model_downloaded.assert_not_called()
    assert host._memory_unavailable_reason == MEMORY_UNAVAILABLE_SERVICE_UNREACHABLE
