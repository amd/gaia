# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dropping several files at once must not break each other's embedding.

Each upload builds its own ``RAGSDK``, whose first embed unloads and reloads
the embedder. Another upload, or a chat searching its documents, that is
embedding at that moment used to lose its request ("Model not loaded").
"""

import threading
import time
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from gaia.llm.lemonade_client import LemonadeClientError
from gaia.rag.sdk import RAGSDK

MODEL = "test-embedder"


class _FakeLemonade:
    """One embedder slot: an unload drops any request in flight on it."""

    def __init__(self):
        self.loaded = True
        self.generation = 0
        self.in_flight = 0
        self.swapped_during_request = 0
        self._lock = threading.Lock()

    def is_users_server(self):
        return False

    def unload_model(self, _model, ignore_if_not_loaded=False):
        with self._lock:
            if self.in_flight:
                self.swapped_during_request += 1
            self.loaded = False
            self.generation += 1

    def load_model(self, _model, llamacpp_args=None):
        time.sleep(0.01)
        self.loaded = True

    def embeddings(self, texts, model, timeout):
        with self._lock:
            if not self.loaded:
                raise LemonadeClientError(f"Model not loaded: {model}")
            self.in_flight += 1
            started = self.generation
        try:
            time.sleep(0.005)
        finally:
            with self._lock:
                self.in_flight -= 1
        if self.generation != started:
            raise LemonadeClientError("Request failed: connection reset by peer")
        return {
            "data": [{"embedding": [1.0, 0.0], "index": i} for i in range(len(texts))]
        }


def _sdk():
    instance = RAGSDK.__new__(RAGSDK)
    instance.config = SimpleNamespace(
        embedding_model=MODEL, show_stats=False, base_url="http://127.0.0.1:1"
    )
    instance.log = Mock()
    instance.embedder = None
    instance.llm_client = None
    return instance


@pytest.fixture
def lemonade():
    server = _FakeLemonade()
    with (
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=server),
        patch(
            "gaia.daemon.broker_client.model_lease",
            side_effect=lambda *a, **k: nullcontext(),
        ),
    ):
        yield server


def test_uploads_loading_the_embedder_never_break_a_search_in_flight(lemonade):
    searcher = _sdk()
    searcher._load_embedder()
    errors: list = []
    done = threading.Event()

    def search():
        try:
            while not done.is_set():
                searcher._encode_texts(["what is the total?"] * 3)
        except Exception as e:  # noqa: BLE001 — reported by the assertion
            errors.append(e)

    def upload():
        try:
            for _ in range(15):
                rag = _sdk()
                rag._load_embedder()
                rag._encode_texts([f"chunk {i}" for i in range(30)])
        except Exception as e:  # noqa: BLE001 — reported by the assertion
            errors.append(e)

    reader = threading.Thread(target=search)
    uploads = [threading.Thread(target=upload) for _ in range(4)]
    reader.start()
    for t in uploads:
        t.start()
    for t in uploads:
        t.join(timeout=60)
    done.set()
    reader.join(timeout=60)

    assert errors == []
    assert lemonade.swapped_during_request == 0


def test_a_code_search_waits_out_an_embedder_reload(tmp_path):
    from gaia.code_index.sdk import CodeIndexConfig, CodeIndexSDK
    from gaia.rag.sdk import _EMBEDDER_SWAP

    sdk = CodeIndexSDK(CodeIndexConfig(repo_path=str(tmp_path)))
    reached = threading.Event()
    sdk._embedder = Mock()
    sdk._embedder.embeddings.side_effect = lambda *a, **k: (
        reached.set() or {"data": [{"embedding": [1.0]}]}
    )

    with _EMBEDDER_SWAP.swap():
        search = threading.Thread(
            target=sdk._embed_batch_call, args=(["def f(): pass"],)
        )
        search.start()
        # Held off while the embedder is being reloaded.
        assert not reached.wait(0.3)
    search.join(timeout=10)

    assert reached.is_set()
